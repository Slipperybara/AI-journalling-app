"""Optional product analytics via PostHog (server-side).

No-op unless POSTHOG_API_KEY is set, so dev, tests, and un-configured deploys
are unaffected. Captures high-signal engagement events keyed by user_id. The
PostHog client batches and sends on a background thread, so capture() is
non-blocking and never raises into request handling.

Server-side capture is deliberate: every meaningful action (a message, a new
conversation, a dashboard view) already passes through the backend, so this
covers both the mobile and web clients without a client SDK or app rebuild.
"""
import atexit
from typing import Optional
from uuid import UUID

from .core import settings

# Read via pydantic-settings so it resolves from .env locally AND real env vars
# on Render (os.getenv alone would miss the .env file this app relies on).
_API_KEY = settings.posthog_api_key.strip()
_HOST = (settings.posthog_host or "https://us.i.posthog.com").strip()

_client = None
if _API_KEY:
    try:
        from posthog import Posthog

        _client = Posthog(_API_KEY, host=_HOST, enable_exception_autocapture=True)
        atexit.register(_client.shutdown)
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


def identify(user_id: UUID | str, properties: Optional[dict] = None) -> None:
    """Set person properties for a user. Silent no-op when analytics is disabled."""
    if _client is None:
        return
    try:
        _client.identify(distinct_id=str(user_id), properties=properties or {})
    except Exception:  # pragma: no cover
        pass


def shutdown() -> None:
    """Flush pending events on app shutdown."""
    if _client is not None:
        try:
            _client.flush()
        except Exception:  # pragma: no cover
            pass
