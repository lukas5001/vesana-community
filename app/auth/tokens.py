"""Token crypto: portal login JWTs (EdDSA, verify-only) and API tokens (HS256).

Trust model
-----------
* The licence portal signs short-lived LOGIN JWTs with its Ed25519 PRIVATE key.
  vesana-community only ever VERIFIES them, using the portal's Ed25519 PUBLIC
  key with PyJWT algorithm ``EdDSA``. There is never a roundtrip to the portal.
* vesana-community issues its own long-lived API tokens, signed with its OWN
  ``SECRET_KEY`` using HS256. These are the credentials a Vesana backend stores
  and presents on subsequent requests.

Everything here is pure crypto/logic with no database access, so it is fully
unit-testable without Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings, get_settings

LOGIN_ISSUER = "vesana-licence-portal"
LOGIN_ALG = "EdDSA"
API_ALG = "HS256"
API_TYP = "api"


class TokenError(Exception):
    """Raised when a token fails verification for any reason."""


def verify_login_jwt(token: str, settings: Settings | None = None) -> dict:
    """Verify a portal-signed login JWT and return its claims.

    Checks signature (EdDSA against the portal public key), expiry (with a small
    configurable leeway), required claims and the expected issuer. The algorithm
    list is pinned to ``EdDSA`` so an attacker cannot downgrade to HS256 and pass
    the public-key PEM off as an HMAC secret.

    Raises:
        TokenError: if the token is malformed, tampered, expired, of the wrong
            issuer, or missing required claims.
    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.portal_public_key_pem,
            algorithms=[LOGIN_ALG],
            issuer=LOGIN_ISSUER,
            leeway=settings.LOGIN_TOKEN_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "jti", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(f"invalid login token: {exc}") from exc

    if not claims.get("sub"):
        raise TokenError("login token missing 'sub' (instance_uuid)")
    if not claims.get("jti"):
        raise TokenError("login token missing 'jti'")
    return claims


def issue_api_token(instance_uuid: str, settings: Settings | None = None) -> tuple[str, datetime]:
    """Issue a stateless HS256 API token for ``instance_uuid``.

    Returns a tuple of ``(token, expires_at)`` where ``expires_at`` is a
    timezone-aware UTC datetime.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.API_TOKEN_TTL_DAYS)
    payload = {
        "sub": instance_uuid,
        "typ": API_TYP,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=API_ALG)
    return token, expires_at


def verify_api_token(token: str, settings: Settings | None = None) -> dict:
    """Verify a vesana-community API token and return its claims.

    Checks the HS256 signature against ``SECRET_KEY``, expiry, and that the
    token type is ``api``. The algorithm list is pinned to HS256.

    Raises:
        TokenError: if the token is malformed, tampered, expired or not an API
            token.
    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[API_ALG],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(f"invalid api token: {exc}") from exc

    if claims.get("typ") != API_TYP:
        raise TokenError("token is not an api token")
    if not claims.get("sub"):
        raise TokenError("api token missing 'sub'")
    return claims
