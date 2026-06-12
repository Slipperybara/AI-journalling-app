"""Device push-token registration (per user).

The native app registers its Expo push token after the user grants
notification permission. The nightly batch reads `device_tokens` to send the
morning-brief push. Idempotent upsert keyed on (user_id, token).
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..db import connect


router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceTokenRegister(BaseModel):
    token: str
    platform: str | None = None


@router.post("/register")
async def register_device_token(
    body: DeviceTokenRegister,
    user_id: UUID = Depends(get_current_user_id),
):
    token = (body.token or "").strip()
    if not token:
        return {"status": "ignored", "reason": "empty token"}

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO device_tokens (user_id, token, platform, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, token) DO UPDATE SET
                platform = excluded.platform,
                updated_at = excluded.updated_at
            """,
            (str(user_id), token, body.platform, datetime.now().isoformat()),
        )
    return {"status": "registered"}
