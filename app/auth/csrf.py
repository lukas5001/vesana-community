"""CSRF-Schutz für die Admin-Formulare (Double-Submit über die signierte Session).

Der Token liegt in der Session (``csrf``) und steht als verstecktes Feld in
jedem Admin-Formular (``_csrf.html``). ``require_csrf`` ist eine Dependency an
JEDEM schreibenden Admin-Handler: Formularwert und Sessionwert müssen
zeichengleich sein (timing-safe), sonst 403 — auch mit gültiger Admin-Sitzung.

SameSite=Lax und ``form-action 'self'`` in der CSP decken die Standardfälle
bereits ab; der Token macht das explizit und unabhängig von Browser-Defaults.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated

from fastapi import Form, HTTPException, Request, status

SESSION_KEY = "csrf"


def csrf_token(request: Request) -> str:
    """Token aus der Session — beim ersten Aufruf erzeugt."""
    try:
        sess = request.session
    except (AssertionError, KeyError, AttributeError):
        return ""
    token = sess.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        sess[SESSION_KEY] = token
    return token


def rotate_csrf_token(request: Request) -> str:
    request.session[SESSION_KEY] = secrets.token_urlsafe(32)
    return request.session[SESSION_KEY]


def require_csrf(
    request: Request,
    csrf: Annotated[str, Form()] = "",
) -> None:
    expected = request.session.get(SESSION_KEY) or ""
    if not expected or not csrf or not hmac.compare_digest(expected, csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="csrf token missing or invalid",
        )
