"""Phase 3: Supabase JWT verification tests.

Covers both verification modes:
- HS256 (legacy projects, shared `SUPABASE_JWT_SECRET`)
- JWKS / ES256 (current projects with asymmetric signing keys)

Plus the dev-fallback path (no Supabase configured).
"""
import time
from unittest.mock import patch
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from jose import jwk, jwt

from app import auth as auth_module
from app.auth import get_current_user_id
from app.core import settings
from tests.conftest import TEST_USER_ID


SECRET = "test-jwt-secret-not-secure"


def _make_token(sub: str, expires_in: int = 3600, audience: str = "authenticated", secret: str = SECRET) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def supabase_enabled():
    """Pin supabase_jwt_secret for the test, force HS256 path, restore after."""
    prev_url = settings.supabase_url
    prev_secret = settings.supabase_jwt_secret
    settings.supabase_url = ""  # ensures HS256 branch is taken
    settings.supabase_jwt_secret = SECRET
    yield
    settings.supabase_url = prev_url
    settings.supabase_jwt_secret = prev_secret


def test_dev_fallback_when_nothing_configured():
    """With no supabase_url AND no jwt_secret, every request resolves to dev_user_id."""
    prev_url = settings.supabase_url
    prev_secret = settings.supabase_jwt_secret
    settings.supabase_url = ""
    settings.supabase_jwt_secret = ""
    try:
        result = get_current_user_id(authorization="")
        assert result == UUID(settings.dev_user_id)
        # Even a junk Bearer is ignored in dev mode.
        assert get_current_user_id(authorization="Bearer junk") == UUID(settings.dev_user_id)
    finally:
        settings.supabase_url = prev_url
        settings.supabase_jwt_secret = prev_secret


def test_valid_token_returns_sub_as_uuid(supabase_enabled):
    token = _make_token(sub=str(TEST_USER_ID))
    result = get_current_user_id(authorization=f"Bearer {token}")
    assert result == TEST_USER_ID


def test_missing_authorization_header_raises_401(supabase_enabled):
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization="")
    assert exc.value.status_code == 401
    assert "Bearer" in exc.value.detail


def test_non_bearer_scheme_raises_401(supabase_enabled):
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization="Basic xyz")
    assert exc.value.status_code == 401


def test_tampered_signature_raises_401(supabase_enabled):
    token = _make_token(sub=str(TEST_USER_ID))
    # Flip a char in the signature so verification fails.
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {tampered}")
    assert exc.value.status_code == 401
    assert "invalid token" in exc.value.detail


def test_expired_token_raises_401(supabase_enabled):
    token = _make_token(sub=str(TEST_USER_ID), expires_in=-10)
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


def test_wrong_audience_raises_401(supabase_enabled):
    """Supabase access tokens carry aud='authenticated'; tokens with a
    different audience (e.g. service-role tokens) must be rejected."""
    token = _make_token(sub=str(TEST_USER_ID), audience="service_role")
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


def test_missing_sub_claim_raises_401(supabase_enabled):
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "iat": now, "exp": now + 3600},
        SECRET, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401
    assert "sub" in exc.value.detail


def test_sub_not_a_uuid_raises_401(supabase_enabled):
    token = _make_token(sub="not-a-uuid")
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


# ── JWKS / asymmetric tests ─────────────────────────────────────────────────

_KID = "test-signing-key-1"


def _make_ec_keypair():
    """Generate a fresh P-256 keypair and return (private_pem, jwks)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public_jwk = jwk.construct(public_pem, algorithm="ES256").to_dict()
    public_jwk["kid"] = _KID
    public_jwk["alg"] = "ES256"
    public_jwk["use"] = "sig"
    return private_pem, {"keys": [public_jwk]}


def _make_es256_token(private_pem: str, sub: str, expires_in: int = 3600,
                       audience: str = "authenticated") -> str:
    now = int(time.time())
    payload = {"sub": sub, "aud": audience, "iat": now, "exp": now + expires_in}
    return jwt.encode(payload, private_pem, algorithm="ES256",
                      headers={"kid": _KID})


@pytest.fixture
def supabase_jwks_enabled():
    """Pin supabase_url for the test, force JWKS path, mock the JWKS fetch."""
    prev_url = settings.supabase_url
    prev_secret = settings.supabase_jwt_secret
    settings.supabase_url = "https://test-project.supabase.co"
    settings.supabase_jwt_secret = ""
    auth_module._reset_jwks_cache()

    private_pem, jwks = _make_ec_keypair()

    with patch.object(auth_module, "_fetch_jwks", return_value=jwks):
        yield private_pem

    settings.supabase_url = prev_url
    settings.supabase_jwt_secret = prev_secret
    auth_module._reset_jwks_cache()


def test_jwks_valid_es256_token_accepted(supabase_jwks_enabled):
    private_pem = supabase_jwks_enabled
    token = _make_es256_token(private_pem, sub=str(TEST_USER_ID))
    result = get_current_user_id(authorization=f"Bearer {token}")
    assert result == TEST_USER_ID


def test_jwks_expired_token_rejected(supabase_jwks_enabled):
    private_pem = supabase_jwks_enabled
    token = _make_es256_token(private_pem, sub=str(TEST_USER_ID), expires_in=-10)
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


def test_jwks_wrong_audience_rejected(supabase_jwks_enabled):
    private_pem = supabase_jwks_enabled
    token = _make_es256_token(private_pem, sub=str(TEST_USER_ID), audience="service_role")
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


def test_jwks_token_signed_by_wrong_key_rejected(supabase_jwks_enabled):
    """A token signed by a different EC key (not in the JWKS) must be rejected."""
    foreign_private_pem, _ = _make_ec_keypair()
    token = _make_es256_token(foreign_private_pem, sub=str(TEST_USER_ID))
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


def test_jwks_takes_precedence_over_hs256_when_both_configured():
    """When both supabase_url and supabase_jwt_secret are set, JWKS wins."""
    prev_url = settings.supabase_url
    prev_secret = settings.supabase_jwt_secret
    settings.supabase_url = "https://test-project.supabase.co"
    settings.supabase_jwt_secret = SECRET
    auth_module._reset_jwks_cache()

    private_pem, jwks = _make_ec_keypair()
    es_token = _make_es256_token(private_pem, sub=str(TEST_USER_ID))

    try:
        with patch.object(auth_module, "_fetch_jwks", return_value=jwks):
            # ES256 token verifies via JWKS path.
            assert get_current_user_id(authorization=f"Bearer {es_token}") == TEST_USER_ID
            # HS256 token would NOT verify here because JWKS path requires ES256/RS256.
            hs_token = _make_token(sub=str(TEST_USER_ID))
            with pytest.raises(HTTPException):
                get_current_user_id(authorization=f"Bearer {hs_token}")
    finally:
        settings.supabase_url = prev_url
        settings.supabase_jwt_secret = prev_secret
        auth_module._reset_jwks_cache()
