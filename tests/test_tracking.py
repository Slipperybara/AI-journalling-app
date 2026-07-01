"""Tracking fields — catalog integrity, service lifecycle, parser addendum,
extraction storage, graph projection, and bot-context injection (multi-tenant)."""
import pytest

from app import tracking as tracking_svc, tracking_catalog
from app.bot import assemble_bot_context
from app.db import connect
from app.extractions import store_extractions
from app.graph_db import graph_connect, init_graph
from app.graph_schema import ONTOLOGY_SCHEMA, USER_SCOPED_LABELS, validate_user_id_scoping
from app.models import (
    EmotionalAnalysis,
    HealthMetrics,
    JournalParserResponse,
    ProductivityMetrics,
    TrackedFieldReading,
)
from app.parser import _tracked_fields_addendum
from tests.conftest import TEST_USER_ID, TEST_USER_ID_B

UID = str(TEST_USER_ID)


# ---- catalog integrity / ontology sync -------------------------------------

def test_catalog_labels_are_all_user_scoped_and_in_ontology():
    for label in tracking_catalog.preset_node_labels():
        assert label in USER_SCOPED_LABELS, f"{label} missing from USER_SCOPED_LABELS"
        assert label in ONTOLOGY_SCHEMA, f"{label} missing from ONTOLOGY_SCHEMA"


def test_catalog_labels_are_valid_cypher_identifiers():
    # Labels are f-string-interpolated on the write path, so they must be safe.
    for label in tracking_catalog.preset_node_labels():
        assert label.isalnum() and label[0].isalpha()


def test_catalog_index_specs_cover_every_label():
    specs = dict(tracking_catalog.index_specs())
    for label in tracking_catalog.preset_node_labels():
        assert specs[label] == "value"


def test_guardrail_scopes_preset_labels():
    assert validate_user_id_scoping("MATCH (h:Hydration) RETURN h") is not None
    assert validate_user_id_scoping("MATCH (h:Hydration {user_id: $user_id}) RETURN h") is None


# ---- service lifecycle ------------------------------------------------------

def test_set_selection_presets_and_customs():
    result = tracking_svc.set_selection(TEST_USER_ID, ["hydration", "meditation"], ["Guitar practice"])
    keys = {r["field_key"] for r in result}
    assert "hydration" in keys and "meditation" in keys
    custom = [r for r in result if r["kind"] == "custom"]
    assert len(custom) == 1
    assert custom[0]["name"] == "Guitar practice"
    assert custom[0]["field_key"] == "custom:guitar_practice"


def test_set_selection_bulk_replace_soft_removes():
    tracking_svc.set_selection(TEST_USER_ID, ["hydration", "stress"], [])
    tracking_svc.set_selection(TEST_USER_ID, ["hydration"], [])
    active = {r["field_key"] for r in tracking_svc.list_tracked_fields(TEST_USER_ID)}
    assert active == {"hydration"}
    all_rows = tracking_svc.list_tracked_fields(TEST_USER_ID, status=None)
    stress = next(r for r in all_rows if r["field_key"] == "stress")
    assert stress["status"] == "removed"


def test_set_selection_reactivates_removed():
    tracking_svc.set_selection(TEST_USER_ID, ["hydration"], [])
    tracking_svc.set_selection(TEST_USER_ID, [], [])          # remove all
    tracking_svc.set_selection(TEST_USER_ID, ["hydration"], [])  # bring it back
    active = {r["field_key"] for r in tracking_svc.list_tracked_fields(TEST_USER_ID)}
    assert active == {"hydration"}


def test_set_selection_unknown_preset_raises():
    with pytest.raises(ValueError):
        tracking_svc.set_selection(TEST_USER_ID, ["not_a_field"], [])


def test_custom_cannot_collide_with_preset_label():
    result = tracking_svc.set_selection(TEST_USER_ID, ["hydration"], ["Hydration"])
    # The custom "Hydration" slugs to "hydration" and is dropped in favour of the preset.
    customs = [r for r in result if r["kind"] == "custom"]
    assert customs == []


def test_selection_is_user_scoped():
    tracking_svc.set_selection(TEST_USER_ID, ["hydration"], [])
    tracking_svc.set_selection(TEST_USER_ID_B, ["stress"], [])
    a = {r["field_key"] for r in tracking_svc.list_tracked_fields(TEST_USER_ID)}
    b = {r["field_key"] for r in tracking_svc.list_tracked_fields(TEST_USER_ID_B)}
    assert a == {"hydration"} and b == {"stress"}


# ---- parser addendum --------------------------------------------------------

def test_addendum_lists_active_presets_and_customs():
    tracking_svc.set_selection(TEST_USER_ID, ["hydration"], ["Guitar"])
    add = _tracked_fields_addendum(TEST_USER_ID)
    assert "hydration" in add
    assert tracking_catalog.BY_KEY["hydration"]["value_hint"] in add
    assert "Guitar" in add


def test_addendum_empty_without_selection():
    assert _tracked_fields_addendum(TEST_USER_ID) == ""


# ---- extraction storage -----------------------------------------------------

def _parsed_with_tracked(readings):
    return JournalParserResponse(
        events=[],
        emotions=EmotionalAnalysis(
            valence=0.0, arousal=0.0, primary_quadrant="Recovery & Clarity",
            cognitive_labels=[], cognitive_triggers=[], social_interactions=[],
        ),
        health=HealthMetrics(
            sleep_quality=None, exercise_type=None, diet_quality=None,
            somatic_sensations=[], physical_performance=None, supplements=[],
        ),
        productivity=ProductivityMetrics(
            deep_work_hours=None, shallow_work_hours=None,
            time_block_adherence=None, cognitive_load=None, friction_points=[],
        ),
        tracked_fields=readings,
    )


def test_store_extractions_filters_unknown_and_blank():
    parsed = _parsed_with_tracked([
        TrackedFieldReading(field_key="hydration", value="6 glasses", note=None),
        TrackedFieldReading(field_key="bogus", value="x", note=None),      # unknown key dropped
        TrackedFieldReading(field_key="meditation", value="", note=None),  # blank value dropped
    ])
    store_extractions(parsed, day="2026-07-01", user_id=TEST_USER_ID)
    with connect() as conn:
        rows = conn.execute(
            "SELECT field_key, value FROM tracked_field_values WHERE user_id = %s AND day = %s",
            (UID, "2026-07-01"),
        ).fetchall()
    assert {r["field_key"]: r["value"] for r in rows} == {"hydration": "6 glasses"}


# ---- bot context ------------------------------------------------------------

def test_tracked_fields_land_in_bot_context():
    tracking_svc.set_selection(TEST_USER_ID, ["hydration"], ["Guitar"])
    ctx = assemble_bot_context(TEST_USER_ID)
    names = {t["name"] for t in ctx["tracked_fields"]}
    assert "Hydration" in names and "Guitar" in names


# ---- graph projection (needs Neo4j; skips if unreachable) -------------------

@pytest.fixture
def graph_ready():
    try:
        init_graph()
    except Exception:
        pytest.skip("Neo4j not available")
    yield
    with graph_connect() as s:
        s.run("MATCH (n {user_id: $u}) DETACH DELETE n", u=UID)


def _mark_succeeded(day):
    with connect() as conn:
        conn.execute(
            "INSERT INTO parse_log (user_id, day, status, parsed_at) VALUES (%s, %s, 'succeeded', %s) "
            "ON CONFLICT (user_id, day) DO UPDATE SET status = 'succeeded'",
            (UID, day, "2026-07-02T06:00:00"),
        )


def test_write_day_projects_preset_node_idempotently(graph_ready):
    from app import graph_batch

    day = "2026-07-02"
    tracking_svc.set_selection(TEST_USER_ID, ["hydration"], [])
    parsed = _parsed_with_tracked([
        TrackedFieldReading(field_key="hydration", value="2L", note="stayed on top of it"),
    ])
    store_extractions(parsed, day=day, user_id=TEST_USER_ID)
    _mark_succeeded(day)

    graph_batch.write_day(day, TEST_USER_ID)
    graph_batch.write_day(day, TEST_USER_ID)  # re-run must not duplicate

    with graph_connect() as s:
        rec = s.run(
            "MATCH (d:Day {user_id: $u, date: $day})-[:TRACKED]->(h:Hydration {user_id: $u}) "
            "RETURN count(h) AS n, collect(h.value)[0] AS v",
            u=UID, day=day,
        ).single()
    assert rec["n"] == 1
    assert rec["v"] == "2L"
