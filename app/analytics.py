"""Optional product analytics via PostHog (server-side).

No-op unless POSTHOG_API_KEY is set, so dev, tests, and un-configured deploys
are unaffected. Captures high-signal engagement events keyed by user_id. The
PostHog client batches and sends on a background thread, so capture() is
non-blocking and never raises into request handling.

Server-side capture is deliberate: every meaningful action (a message, a new
conversation, a dashboard view) already passes through the backend, so this
covers both the mobile and web clients without a client SDK or app rebuild.
"""
import os
from typing import Optional
from uuid import UUID

_API_KEY = os.getenv("POSTHOG_API_KEY", "").strip()
_HOST = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com").strip()

_client = None
if _API_KEY:
    try:
        from posthog import Posthog

        _client = Posthog(_API_KEY, host=_HOST)
    except Exception:  # pragma: no cover - import/config guard
        _client = None


def capture(user_id: UUID | str, event: str, properties: Optional[dict] = None) -> None:
    """Record an event for a user. Silent no-op when analytics is disabled."""
    if _client is None:
        return
    try:
        _client.capture(distinct_id=str(user_id), event=event, properties=properties or {})
    except Exception:  # pragma: no cover - analytics must never break a request
        pass


def shutdown() -> None:
    """Flush pending events on app shutdown."""
    if _client is not None:
        try:
            _client.flush()
        except Exception:  # pragma: no cover
            pass
