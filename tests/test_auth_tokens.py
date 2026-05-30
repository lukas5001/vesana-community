"""Pure crypto tests for token verification — no database required.

Covers login-JWT verification (valid / tampered / expired / wrong issuer /
algorithm-confusion) and the API-token issue->verify roundtrip (including
expiry rejection).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import timedelta

import jwt
import pytest

from app.auth.tokens import (
    TokenError,
    issue_api_token,
    verify_api_token,
    verify_login_jwt,
)


# --- Login JWT verification -------------------------------------------------
def test_valid_login_jwt_verifies(make_login_jwt, settings):
    sub = "11111111-1111-1111-1111-111111111111"
    token = make_login_jwt(sub=sub, display_name="Alice", jti="jti-valid-1")

    claims = verify_login_jwt(token, settings=settings)

    assert claims["sub"] == sub
    assert claims["display_name"] == "Alice"
    assert claims["jti"] == "jti-valid-1"
    assert claims["iss"] == "vesana-licence-portal"


def test_tampered_signature_is_rejected(make_login_jwt, settings):
    token = make_login_jwt(jti="jti-tamper")
    # Flip a character in the signature segment.
    head, payload, sig = token.split(".")
    bad_char = "A" if sig[0] != "A" else "B"
    tampered = f"{head}.{payload}.{bad_char}{sig[1:]}"

    with pytest.raises(TokenError):
        verify_login_jwt(tampered, settings=settings)


def test_expired_login_jwt_is_rejected(make_login_jwt, settings):
    # Expired well beyond the leeway window.
    token = make_login_jwt(
        jti="jti-expired",
        iat_delta=timedelta(minutes=-10),
        exp_delta=timedelta(minutes=-5),
    )
    with pytest.raises(TokenError):
        verify_login_jwt(token, settings=settings)


def test_wrong_issuer_is_rejected(make_login_jwt, settings):
    token = make_login_jwt(jti="jti-issuer", iss="evil-issuer")
    with pytest.raises(TokenError):
        verify_login_jwt(token, settings=settings)


def test_algorithm_confusion_hs256_is_rejected(settings, portal_public_pem):
    """An HS256 token whose HMAC secret is the portal PUBLIC-key PEM must NOT
    pass — verify pins algorithms to EdDSA only.

    Modern PyJWT refuses to *encode* HS256 with a PEM key, so we hand-craft the
    forged token (the classic alg-confusion attack a malicious client could
    assemble by hand) and confirm our verifier rejects it.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "vesana-licence-portal",
        "sub": "attacker",
        "display_name": "Mallory",
        "jti": "jti-confusion",
        "iat": 0,
        "exp": 9999999999,
    }

    def _b64(obj: dict) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=")

    signing_input = _b64(header) + b"." + _b64(payload)
    sig = hmac.new(portal_public_pem.encode(), signing_input, hashlib.sha256).digest()
    forged = (signing_input + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode()

    with pytest.raises(TokenError):
        verify_login_jwt(forged, settings=settings)


def test_login_jwt_missing_jti_is_rejected(make_login_jwt, settings):
    # Build a token whose jti is empty; the "require" option should reject it.
    token = make_login_jwt(jti="", extra_claims={"jti": ""})
    with pytest.raises(TokenError):
        verify_login_jwt(token, settings=settings)


# --- API token roundtrip ----------------------------------------------------
def test_api_token_roundtrip(settings):
    sub = "22222222-2222-2222-2222-222222222222"
    token, expires_at = issue_api_token(sub, settings=settings)

    claims = verify_api_token(token, settings=settings)
    assert claims["sub"] == sub
    assert claims["typ"] == "api"
    assert claims["exp"] == int(expires_at.timestamp())


def test_expired_api_token_is_rejected(settings):
    # Mint a token whose exp is already in the past, signed with the same secret.
    expired = jwt.encode(
        {"sub": "x", "typ": "api", "iat": 0, "exp": 1},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        verify_api_token(expired, settings=settings)


def test_api_token_wrong_secret_is_rejected(settings):
    token, _ = issue_api_token("z", settings=settings)
    # Verify against a different secret -> signature mismatch.
    bad = settings.model_copy(update={"SECRET_KEY": "totally-different-secret"})
    with pytest.raises(TokenError):
        verify_api_token(token, settings=bad)


def test_non_api_token_type_is_rejected(settings):
    # A token with the right signature but wrong typ must be rejected.
    bad = jwt.encode(
        {"sub": "y", "typ": "login", "iat": 0, "exp": 9999999999},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        verify_api_token(bad, settings=settings)
