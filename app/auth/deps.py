"""FastAPI auth dependencies.

* ``get_current_instance`` — verifies a Bearer API token, loads the Instance and
  rejects blocked instances on every request.
* ``require_admin`` — HTTP Basic auth from ``.env``, compared timing-safe.
* ``get_session_instance`` — resolves the logged-in instance from the signed
  session cookie (used by the HTML UI), or ``None`` if not logged in.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy.orm import Session

from app.auth.tokens import TokenError, verify_api_token
from app.config import Settings, get_settings
from app.db import get_db
from app.models.instance import Instance

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_instance(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Instance:
    """Resolve and authorise the calling Instance from a Bearer API token.

    Rejects with 401 if the token is missing/invalid, the instance is unknown,
    or the instance is blocked.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("missing bearer token")

    try:
        claims = verify_api_token(credentials.credentials, settings=settings)
    except TokenError as exc:
        raise _unauthorized(str(exc)) from exc

    instance = db.get(Instance, claims["sub"])
    if instance is None:
        raise _unauthorized("unknown instance")
    if instance.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="instance is blocked",
        )
    return instance


def verify_admin_credentials(username: str, password: str, settings: Settings) -> bool:
    """Timing-safe check of admin username + password against the configured pair.

    Both fields are compared with ``hmac.compare_digest`` so neither the username
    nor the password content/length leaks via response timing.
    """
    user_ok = hmac.compare_digest(
        (username or "").encode("utf-8"),
        settings.COMMUNITY_ADMIN_USER.encode("utf-8"),
    )
    pass_ok = hmac.compare_digest(
        (password or "").encode("utf-8"),
        settings.COMMUNITY_ADMIN_PASSWORD.encode("utf-8"),
    )
    return user_ok and pass_ok


def require_admin(request: Request) -> str:
    """Require an authenticated admin SESSION (set by ``POST /admin/login``).

    The HTML admin area is browser-only, so admin auth lives in the signed
    session cookie (``is_admin``) — NOT HTTP Basic. Unauthenticated requests are
    redirected to the login page via a 303 (so a browser lands on the form
    instead of a credentials popup).
    """
    if request.session.get("is_admin"):
        return request.session.get("admin_user") or "admin"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail="admin login required",
        headers={"Location": "/admin/login"},
    )


def get_session_instance(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Instance | None:
    """Return the logged-in Instance from the signed session cookie, or None.

    Blocked instances are treated as logged out.
    """
    instance_uuid = request.session.get("instance_uuid")
    if not instance_uuid:
        return None
    instance = db.get(Instance, instance_uuid)
    if instance is None or instance.is_blocked:
        return None
    return instance
