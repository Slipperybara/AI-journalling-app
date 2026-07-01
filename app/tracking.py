"""User tracking-field selections (multi-tenant).

What the user has asked JAI to track. Two kinds:
  - `preset`  — from `tracking_catalog`; each projects to its own Neo4j label
                via the nightly parser + `graph_batch`.
  - `custom`  — free text; captured as Events by the parser (no new label).

Postgres is the source of truth. Unlike goals, selecting a field writes NO
Neo4j node here — preset nodes are data-driven (they appear as the nightly
projection accrues daily readings). The row just tells the parser to extract it
and the bot/brief to ask about it.

Selection is a bulk replace (`set_selection`) so the Tracking page's Save button
is a single call. Removal is a soft delete (`status='removed'`) so history and
already-projected nodes are preserved.
"""
import re
from typing import Optional
from uuid import UUID

from . import tracking_catalog
from .db import connect

VALID_STATUSES = {"active", "removed"}
CUSTOM_PREFIX = "custom:"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def list_tracked_fields(user_id: UUID, status: Optional[str] = "active") -> list[dict]:
    """Rows for the user, newest first. `status=None` returns every row."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tracked_fields WHERE user_id = %s AND status = %s "
                "ORDER BY created_at DESC",
                (str(user_id), status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tracked_fields WHERE user_id = %s ORDER BY created_at DESC",
                (str(user_id),),
            ).fetchall()
    return [dict(r) for r in rows]


def set_selection(
    user_id: UUID,
    preset_keys: list[str],
    custom_names: list[str],
) -> list[dict]:
    """Bulk-replace the user's active tracked fields.

    `preset_keys` must be catalog keys; `custom_names` are free-text field names.
    Fields present become (or stay) active; active fields no longer present are
    soft-removed. Idempotent. Returns the new active set.
    """
    uid = str(user_id)

    unknown = [k for k in preset_keys if not tracking_catalog.is_preset_key(k)]
    if unknown:
        raise ValueError(f"unknown preset field keys: {sorted(set(unknown))}")

    # Build the desired active set: field_key -> (name, kind). Dedupe presets by
    # key and customs by slug (last name wins for a given slug).
    desired: dict[str, tuple[str, str]] = {}
    for k in preset_keys:
        desired[k] = (tracking_catalog.BY_KEY[k]["label"], "preset")
    for raw in custom_names:
        name = (raw or "").strip()
        slug = _slug(name)
        if not slug:
            continue
        if tracking_catalog.is_preset_key(slug):
            continue  # name maps onto a preset — the preset owns that concept
        fk = CUSTOM_PREFIX + slug
        if fk in desired:  # dedupe duplicate custom names
            continue
        desired[fk] = (name, "custom")

    with connect() as conn:
        cursor = conn.cursor()
        for fk, (name, kind) in desired.items():
            cursor.execute(
                """
                INSERT INTO tracked_fields (user_id, field_key, name, kind, status)
                VALUES (%s, %s, %s, %s, 'active')
                ON CONFLICT (user_id, field_key) DO UPDATE SET
                    status = 'active',
                    name = excluded.name,
                    kind = excluded.kind
                """,
                (uid, fk, name, kind),
            )

        desired_keys = list(desired.keys())
        if desired_keys:
            cursor.execute(
                "UPDATE tracked_fields SET status = 'removed' "
                "WHERE user_id = %s AND status = 'active' AND NOT (field_key = ANY(%s))",
                (uid, desired_keys),
            )
        else:
            cursor.execute(
                "UPDATE tracked_fields SET status = 'removed' "
                "WHERE user_id = %s AND status = 'active'",
                (uid,),
            )

    return list_tracked_fields(user_id, status="active")


def add_custom_field(user_id: UUID, name: str) -> dict:
    """Add (or reactivate) a single custom field. Returns the row."""
    name = (name or "").strip()
    slug = _slug(name)
    if not slug:
        raise ValueError("field name cannot be blank")
    fk = CUSTOM_PREFIX + slug
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tracked_fields (user_id, field_key, name, kind, status)
            VALUES (%s, %s, %s, 'custom', 'active')
            ON CONFLICT (user_id, field_key) DO UPDATE SET status = 'active', name = excluded.name
            """,
            (str(user_id), fk, name),
        )
        row = conn.execute(
            "SELECT * FROM tracked_fields WHERE user_id = %s AND field_key = %s",
            (str(user_id), fk),
        ).fetchone()
    return dict(row)


def remove_field(user_id: UUID, field_key: str) -> None:
    """Soft-remove a tracked field by key."""
    with connect() as conn:
        conn.execute(
            "UPDATE tracked_fields SET status = 'removed' WHERE user_id = %s AND field_key = %s",
            (str(user_id), field_key),
        )


def active_preset_keys(user_id: UUID) -> list[str]:
    """Catalog keys the user actively tracks (used by the parser/extraction)."""
    return [
        r["field_key"]
        for r in list_tracked_fields(user_id, status="active")
        if r["kind"] == "preset"
    ]
