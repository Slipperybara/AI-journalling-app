"""Endpoints for user tracking-field selection (multi-tenant).

The Tracking page (mobile) reads the preset catalog + the user's current
selection, and saves the whole selection in one PUT. `user_id` comes from the
auth dependency; the service enforces catalog validity.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import analytics, tracking as tracking_svc, tracking_catalog
from ..auth import get_current_user_id


router = APIRouter(prefix="/api/tracking", tags=["tracking"])


class TrackingSelection(BaseModel):
    preset_keys: list[str] = []
    custom_names: list[str] = []


@router.get("/catalog")
async def get_catalog():
    """The fixed preset catalog rendered as bubbles on the Tracking page."""
    return [
        {"key": e["key"], "label": e["label"]}
        for e in tracking_catalog.PRESET_CATALOG
    ]


@router.get("")
async def get_tracking(user_id: UUID = Depends(get_current_user_id)):
    return tracking_svc.list_tracked_fields(user_id, status="active")


@router.put("")
async def put_tracking(
    body: TrackingSelection,
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        result = tracking_svc.set_selection(user_id, body.preset_keys, body.custom_names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    analytics.capture(user_id, "tracking_saved", {
        "preset_count": len(body.preset_keys),
        "custom_count": len(body.custom_names),
    })
    return result
