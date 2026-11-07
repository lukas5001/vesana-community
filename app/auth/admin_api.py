"""Admin-API-Zugang für die JSON-Endpunkte unter ``/api/v1/admin/*``.

Vorher in ``app.routers.community_interactions`` / ``app.routers.uploads``;
beide Router sind mit dem Umbau zur Vesana-Bibliothek entfallen, der
Admin-Zugang bleibt unverändert.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db

DbDep = Annotated[Session, Depends(get_db)]


def is_admin_request(
    db: DbDep,
    settings: Annotated[Settings, Depends(get_settings)],
    x_admin_authorization: Annotated[str | None, Header()] = None,
) -> bool:
    """True only for valid admin credentials in ``X-Admin-Authorization``.

    Zwei Formen:
    * ``Bearer <COMMUNITY_ADMIN_API_TOKEN>`` — der Maschinen-Zugang, gilt immer
      (wenn konfiguriert).
    * ``Basic base64(user:password)`` — gilt NUR, solange der Admin keinen
      zweiten Faktor eingerichtet hat. Sonst würde Benutzer+Passwort über die
      API den zweiten Faktor still umgehen.

    Never raises; absence means "not admin".
    """
    if not x_admin_authorization:
        return False
    header = x_admin_authorization.strip()
    if header.startswith("Bearer "):
        token = settings.COMMUNITY_ADMIN_API_TOKEN
        presented = header[len("Bearer ") :].strip()
        return bool(token) and hmac.compare_digest(presented.encode(), token.encode())
    if not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header[len("Basic ") :].strip()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user, _, password = raw.partition(":")
    user_ok = hmac.compare_digest(
        user.encode("utf-8"), settings.COMMUNITY_ADMIN_USER.encode("utf-8")
    )
    pass_ok = hmac.compare_digest(
        password.encode("utf-8"), settings.COMMUNITY_ADMIN_PASSWORD.encode("utf-8")
    )
    if not (user_ok and pass_ok):
        return False
    from app.services.admin_account import two_fa_enabled

    return not two_fa_enabled(db, settings.COMMUNITY_ADMIN_USER)


AdminFlag = Annotated[bool, Depends(is_admin_request)]


def require_admin_flag(is_admin: bool) -> None:
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin auth required",
            headers={"WWW-Authenticate": "Basic"},
        )
