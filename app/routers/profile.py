"""Onboarding profile sync (multi-tenant).

The native client captures onboarding answers locally during the funnel and
PUTs them here once after login. Stored so the empathetic bot's earliest
replies already know the user. `user_id` comes from the auth dependency.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..profile import get_profile, upsert_profile


router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileIn(BaseModel):
    name: str | None = None
    age: str | None = None
    gender: str | None = None
    occupation: str | None = None
    emotional: str | None = None
    familiarity: str | None = None
    issues: list[str] | None = None


@router.get("")
async def get_profile_endpoint(user_id: UUID = Depends(get_current_user_id)):
    return get_profile(user_id) or {}


@router.put("")
async def put_profile_endpoint(
    payload: ProfileIn,
    user_id: UUID = Depends(get_current_user_id),
):
    return upsert_profile(user_id, payload.model_dump(exclude_none=True))
