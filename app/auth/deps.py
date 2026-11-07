"""FastAPI auth dependencies.

* ``require_admin`` — Admin-SESSION (Login-Seite + optional TOTP), mit Idle-Ablauf.
* ``verify_admin_credentials`` — timing-sicherer Vergleich Benutzer + Passwort.

Die Anmeldung von Instanzen (Portal-Login-JWT, API-Token, Sitzung) ist mit dem
Umbau zur Vesana-Bibliothek (2026-11-07) entfallen — die Bibliothek wird ohne
Anmeldung gelesen.
"""

from __future__ import annotations

import hmac
import time
from urllib.parse import quote

from fastapi import HTTPException, Request, status

from app.config import Settings, get_settings


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
    """Require an authenticated admin SESSION (set by the admin login flow).

    The HTML admin area is browser-only, so admin auth lives in the signed
    session cookie (``is_admin``) — NOT HTTP Basic. Unauthenticated requests are
    redirected to the login page via a 303 (so a browser lands on the form
    instead of a credentials popup). Eine Sitzung ohne Aktivität länger als
    ``COMMUNITY_ADMIN_IDLE_MINUTES`` läuft ab (``?reason=expired``).
    """
    sess = request.session
    if sess.get("is_admin"):
        seen = float(sess.get("admin_seen_at") or 0)
        idle = get_settings().COMMUNITY_ADMIN_IDLE_MINUTES * 60
        if seen and time.time() - seen > idle:
            for key in ("is_admin", "admin_user", "admin_seen_at", "totp_setup"):
                sess.pop(key, None)
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="admin session expired",
                headers={"Location": f"/admin/login?reason=expired&next={quote(request.url.path)}"},
            )
        sess["admin_seen_at"] = time.time()
        return sess.get("admin_user") or "admin"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail="admin login required",
        headers={"Location": f"/admin/login?next={quote(request.url.path)}"},
    )
