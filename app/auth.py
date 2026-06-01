"""User-identity resolution for FastAPI routes (Phase 3).

Verifies Supabase access tokens (JWT). Three modes, picked by config:

1. **Asymmetric (JWKS, recommended)** — when `settings.supabase_url` is set.
   Fetches `<project>/auth/v1/.well-known/jwks.json` once, caches it, verifies
   tokens with ES256/RS256. This is the default for new Supabase projects
   using publishable + secret keys.

2. **Symmetric (HS256)** — when only `settings.supabase_jwt_secret` is set
   (no `supabase_url`). Legacy mode for older Supabase projects on the
   shared JWT secret.

3. **Dev fallback** — when neither is set. Returns `settings.dev_user_id`.
   Keeps `python main.py` usable locally without provisioning Supabase.

JWKS is preferred when both `supabase_url` and `supabase_jwt_secret` are set
because the user is opting into the modern flow.
"""
from uuid import UUID

import httpx
from fastapi import Header, HTTPException
from jose import JWTError, jwt

from .core import settings


_JWT_AUDIENCE = "authenticated"
# python-jose picks the right key from the JWKS by `kid` header automatically.
_ASYMMETRIC_ALGS = ["ES256", "RS256"]

_JWKS_CACHE: dict | None = None


def _fetch_jwks() -> dict:
    """Lazy-load the project's JWKS. Cached at module scope; restart the
    app to pick up rotated signing keys (Supabase rotates rarely)."""
    global _JWKS_CACHE
    if _JWKS_CACHE is None:
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            _JWKS_CACHE = resp.json()
    return _JWKS_CACHE


def _reset_jwks_cache() -> None:
    """Test helper: drop the cached JWKS so the next call refetches."""
    global _JWKS_CACHE
    _JWKS_CACHE = None


def get_current_user_id(authorization: str = Header(default="")) -> UUID:
    if settings.supabase_url:
        verify = _verify_jwks
    elif settings.supabase_jwt_secret:
        verify = _verify_hs256
    else:
        # Dev fallback.
        return UUID(settings.dev_user_id)

    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    token = authorization.split(" ", 1)[1].strip()

    try:
        payload = verify(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="token missing sub claim")

    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(status_code=401, detail="sub claim is not a UUID")


def _verify_jwks(token: str) -> dict:
    return jwt.decode(
        token,
        _fetch_jwks(),
        algorithms=_ASYMMETRIC_ALGS,
        audience=_JWT_AUDIENCE,
    )


def _verify_hs256(token: str) -> dict:
    return jwt.decode(
        token,
        settings.supabase_jwt_secret,
        algorithms=["HS256"],
        audience=_JWT_AUDIENCE,
    )
