"""Push notifications via the Expo Push API.

The native app registers an Expo push token (see routers/devices.py). This
module delivers notifications to a user's devices by POSTing to Expo's push
service, which forwards to APNs/FCM. No APNs key is handled here — EAS manages
the credential and Expo delivers on our behalf.

Best-effort: every failure is swallowed so a push problem never breaks the
nightly batch or a request.
"""
import traceback
from typing import Optional
from uuid import UUID

import httpx

from .db import connect


_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _tokens_for_user(user_id: UUID) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT token FROM device_tokens WHERE user_id = %s",
            (str(user_id),),
        ).fetchall()
    return [r["token"] for r in rows if r.get("token")]


def send_push_to_user(
    user_id: UUID,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> dict:
    """Send a push to every Expo-registered device for the user. No-op when the
    user has no tokens; best-effort on any network/Expo error."""
    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            **({"data": data} if data else {}),
        }
        for token in _tokens_for_user(user_id)
        # Only Expo-format tokens; ignore anything stale/malformed.
        if token.startswith("ExponentPushToken")
    ]
    if not messages:
        return {"sent": 0}
    try:
        with httpx.Client(timeout=10.0) as http:
            resp = http.post(
                _EXPO_PUSH_URL,
                json=messages,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            resp.raise_for_status()
        return {"sent": len(messages)}
    except Exception:  # pragma: no cover - push must never break the caller
        traceback.print_exc()
        return {"sent": 0, "error": True}
