"""Admin-Anmeldung: Passwort, dann (falls eingerichtet) der zweite Faktor.

Ablauf
------
1. ``POST /admin/login`` prüft Benutzer + Passwort (timing-safe). Ist für das
   Konto TOTP aktiv, landet nur ein ``admin_pending``-Marker in der Session
   und es geht weiter zu ``/admin/login/verify`` — die Sitzung ist bis dahin
   KEINE Admin-Sitzung.
2. ``POST /admin/login/verify`` nimmt einen TOTP-Code oder einen Backup-Code.
   Erst dann wird die Sitzung zur Admin-Sitzung (``_complete_login``): alle
   Admin-Schlüssel werden frisch gesetzt, der CSRF-Token rotiert.

Schutz: pro IP höchstens 5 Fehlversuche in 5 Minuten (Passwort UND Code),
ein ausstehender zweiter Schritt verfällt nach 5 Minuten bzw. nach 5 falschen
Codes. Jeder Erfolg und jeder Fehlschlag steht im Admin-Protokoll.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.csrf import rotate_csrf_token
from app.auth.deps import verify_admin_credentials
from app.config import get_settings
from app.db import get_db
from app.i18n import normalize_lang, translate
from app.services import admin_account
from app.services import admin_audit as audit
from app.templating import templates

router = APIRouter(tags=["admin"])

DbDep = Annotated[Session, Depends(get_db)]

# Best-effort in-memory brute-force throttle, keyed by client IP. Resets on
# process restart — adequate for a single low-traffic admin.
_MAX_FAILS = 5
_WINDOW_S = 300.0
_attempts: dict[str, tuple[int, float]] = {}

# Der zweite Schritt muss innerhalb dieser Zeit erfolgen.
_PENDING_TTL_S = 300.0
_PENDING_MAX_FAILS = 5

_SESSION_KEYS = (
    "is_admin",
    "admin_user",
    "admin_seen_at",
    "admin_pending",
    "admin_pending_at",
    "admin_pending_fails",
    "admin_pending_next",
    "totp_setup",
)


def _t(request: Request, key: str, **kw) -> str:
    return translate(normalize_lang(request.cookies.get("lang")), key, **kw)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _is_locked(ip: str) -> bool:
    rec = _attempts.get(ip)
    if rec is None:
        return False
    count, started = rec
    if time.monotonic() - started > _WINDOW_S:
        _attempts.pop(ip, None)
        return False
    return count >= _MAX_FAILS


def _record_failure(ip: str) -> None:
    count, started = _attempts.get(ip, (0, time.monotonic()))
    if time.monotonic() - started > _WINDOW_S:
        count, started = 0, time.monotonic()
    _attempts[ip] = (count + 1, started)


def _safe_next(value: str | None) -> str:
    """Nur Ziele innerhalb des Admin-Bereichs — nie ein offener Redirect."""
    if value and value.startswith("/admin") and not value.startswith("//"):
        return value
    return "/admin"


def _clear_admin_session(request: Request) -> None:
    for key in _SESSION_KEYS:
        request.session.pop(key, None)


def _complete_login(request: Request, username: str) -> None:
    """Frische Admin-Sitzung: alte Admin-Schlüssel weg, CSRF-Token rotiert."""
    _clear_admin_session(request)
    request.session["is_admin"] = True
    request.session["admin_user"] = username
    request.session["admin_seen_at"] = time.time()
    rotate_csrf_token(request)


def _login_page(request: Request, *, error: str | None = None, status_code: int = 200):
    reason = request.query_params.get("reason")
    notice = None
    if reason == "expired":
        notice = _t(request, "adminlogin.expired")
    elif reason == "logout":
        notice = _t(request, "adminlogin.logged_out")
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"error": error, "notice": notice, "next": _safe_next(request.query_params.get("next"))},
        status_code=status_code,
    )


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if request.session.get("is_admin"):
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    return _login_page(request)


@router.post("/admin/login")
def admin_login_submit(
    request: Request,
    db: DbDep,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str | None, Form()] = None,
):
    ip = client_ip(request)
    if _is_locked(ip):
        return _login_page(
            request,
            error=_t(request, "adminlogin.locked"),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    settings = get_settings()
    if not verify_admin_credentials(username, password, settings):
        _record_failure(ip)
        audit.record(
            db,
            admin_user=(username or "?")[:128],
            action="auth.login_failed",
            summary=_t(request, "audit.summary.password_failed"),
            details={"step": "password"},
            ip=ip,
        )
        db.commit()
        return _login_page(
            request,
            error=_t(request, "adminlogin.err"),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    _attempts.pop(ip, None)
    target = _safe_next(next)
    user = settings.COMMUNITY_ADMIN_USER

    if admin_account.two_fa_enabled(db, user):
        _clear_admin_session(request)
        request.session["admin_pending"] = user
        request.session["admin_pending_at"] = time.time()
        request.session["admin_pending_fails"] = 0
        request.session["admin_pending_next"] = target
        return RedirectResponse(url="/admin/login/verify", status_code=status.HTTP_303_SEE_OTHER)

    _complete_login(request, user)
    audit.record(
        db,
        admin_user=user,
        action="auth.login",
        summary=_t(request, "audit.summary.login_password"),
        details={"method": "password"},
        ip=ip,
    )
    db.commit()
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


def _pending_user(request: Request) -> str | None:
    user = request.session.get("admin_pending")
    started = request.session.get("admin_pending_at") or 0
    if not user or time.time() - float(started) > _PENDING_TTL_S:
        return None
    return user


@router.get("/admin/login/verify", response_class=HTMLResponse)
def admin_verify_page(request: Request):
    if request.session.get("is_admin"):
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    if _pending_user(request) is None:
        _clear_admin_session(request)
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "admin/login_2fa.html", {})


@router.post("/admin/login/verify")
def admin_verify_submit(
    request: Request,
    db: DbDep,
    code: Annotated[str, Form()] = "",
):
    ip = client_ip(request)
    user = _pending_user(request)
    if user is None:
        _clear_admin_session(request)
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    if _is_locked(ip):
        return templates.TemplateResponse(
            request,
            "admin/login_2fa.html",
            {"error": _t(request, "adminlogin.locked")},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    settings = get_settings()
    result = admin_account.verify_second_factor(db, user, code, settings)
    if not result.ok:
        _record_failure(ip)
        fails = int(request.session.get("admin_pending_fails") or 0) + 1
        request.session["admin_pending_fails"] = fails
        audit.record(
            db,
            admin_user=user,
            action="auth.login_failed",
            summary=_t(request, "audit.summary.code_failed"),
            details={"step": "second_factor", "attempt": fails},
            ip=ip,
        )
        db.commit()
        if fails >= _PENDING_MAX_FAILS:
            _clear_admin_session(request)
            return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "admin/login_2fa.html",
            {"error": _t(request, "adminlogin.code_err")},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    _attempts.pop(ip, None)
    target = _safe_next(request.session.get("admin_pending_next"))
    _complete_login(request, user)
    details: dict = {"method": result.method}
    if result.method == "backup":
        details["backup_codes_left"] = result.backup_codes_left
        request.session["flash"] = [
            {
                "kind": "warn",
                "key": "admin.flash.backup_used",
                "kw": {"n": result.backup_codes_left},
            }
        ]
    audit.record(
        db,
        admin_user=user,
        action="auth.login",
        summary=_t(request, "audit.summary.login_2fa", method=result.method),
        details=details,
        ip=ip,
    )
    db.commit()
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/logout")
def admin_logout(request: Request, db: DbDep) -> RedirectResponse:
    user = request.session.get("admin_user")
    if request.session.get("is_admin") and user:
        audit.record(
            db,
            admin_user=user,
            action="auth.logout",
            summary=_t(request, "audit.summary.logout"),
            ip=client_ip(request),
        )
        db.commit()
    _clear_admin_session(request)
    return RedirectResponse(url="/admin/login?reason=logout", status_code=status.HTTP_303_SEE_OTHER)
