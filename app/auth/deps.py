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
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from sqlalchemy.orm import Session

from app.auth.tokens import TokenError, verify_api_token
from app.config import Settings, get_settings
from app.db import get_db
from app.models.instance import Instance

_bearer = HTTPBearer(auto_error=False)
_basic = HTTPBasic(auto_error=False)


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


def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Require valid HTTP Basic admin credentials (timing-safe comparison).

    Returns the admin username on success.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin auth required",
            headers={"WWW-Authenticate": "Basic"},
        )

    # Compare both fields with constant-time comparison to avoid leaking either
    # the username or the password length/content via timing.
    user_ok = hmac.compare_digest(
        credentials.username.encode("utf-8"),
        settings.COMMUNITY_ADMIN_USER.encode("utf-8"),
    )
    pass_ok = hmac.compare_digest(
        credentials.password.encode("utf-8"),
        settings.COMMUNITY_ADMIN_PASSWORD.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


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
