"""User-facing notification preferences (multi-tenant).

The client saves the user's chosen morning-reflection time (local hour:minute +
their IANA timezone) here. Delivery happens server-side via the notify_delivery
cron. `user_id` comes from the auth dependency.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import get_current_user_id
from ..notifications_prefs import MIN_HOUR, MIN_MINUTE, get_prefs, upsert_prefs


router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationPrefsIn(BaseModel):
    enabled: bool = True
    hour: int = Field(default=8, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    tz: str = "UTC"


@router.get("")
async def get_notification_prefs(user_id: UUID = Depends(get_current_user_id)):
    prefs = get_prefs(user_id)
    # Expose the floor so the client picker can enforce the same minimum.
    return {"prefs": prefs, "min_hour": MIN_HOUR, "min_minute": MIN_MINUTE}


@router.put("")
async def put_notification_prefs(
    payload: NotificationPrefsIn,
    user_id: UUID = Depends(get_current_user_id),
):
    return upsert_prefs(user_id, payload.enabled, payload.hour, payload.minute, payload.tz)
