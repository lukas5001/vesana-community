"""Session-based admin login — replaces the HTTP Basic browser popup.

The admin area is browser-only, so admin auth lives in the signed session cookie
(``is_admin``). This module renders a real login page and gates it with a
best-effort per-IP brute-force throttle. Credentials are checked timing-safe in
``app.auth.deps.verify_admin_credentials``.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.deps import verify_admin_credentials
from app.config import get_settings
from app.templating import templates

router = APIRouter(tags=["admin"])

# Best-effort in-memory brute-force throttle, keyed by client IP. Resets on
# process restart — adequate for a single low-traffic admin; a shared store
# would be the hardening step if the admin area ever scales out.
_MAX_FAILS = 5
_WINDOW_S = 300.0
_attempts: dict[str, tuple[int, float]] = {}


def _client_ip(request: Request) -> str:
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


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if request.session.get("is_admin"):
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "admin/login.html", {})


@router.post("/admin/login")
def admin_login_submit(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    ip = _client_ip(request)
    if _is_locked(ip):
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"error": "Too many attempts. Please wait a few minutes and try again."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    settings = get_settings()
    if verify_admin_credentials(username, password, settings):
        _attempts.pop(ip, None)
        request.session["is_admin"] = True
        request.session["admin_user"] = settings.COMMUNITY_ADMIN_USER
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)

    _record_failure(ip)
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"error": "Invalid username or password."},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@router.get("/admin/logout")
def admin_logout(request: Request) -> RedirectResponse:
    request.session.pop("is_admin", None)
    request.session.pop("admin_user", None)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
