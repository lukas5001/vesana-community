"""Was von der öffentlichen Website übrig bleibt (Vesana-Bibliothek, 2026-11-07).

Die Website mit Profil-Browser, Q&A und Uploads ist entfallen; Fragen und
Austausch laufen im öffentlichen GitHub-Repo. ``/`` leitet dorthin, ``/lang``
bleibt für die Sprachwahl im Admin-Bereich.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.i18n import normalize_lang

router = APIRouter(include_in_schema=False)

COMMUNITY_URL = "https://github.com/vesana-org/community"


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url=COMMUNITY_URL, status_code=status.HTTP_302_FOUND)


@router.get("/lang/{code}")
def set_language(code: str, request: Request) -> RedirectResponse:
    """Set the UI language cookie (de/en) and return to where the user was."""
    code = normalize_lang(code)
    # Open-redirect safe: only honour the referer's PATH on our own site.
    target = "/admin"
    referer = request.headers.get("referer", "")
    if referer:
        try:
            parsed = urlparse(referer)
            if parsed.path.startswith("/") and not parsed.path.startswith("//"):
                target = parsed.path + (("?" + parsed.query) if parsed.query else "")
        except ValueError:
            target = "/admin"
    resp = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        "lang",
        code,
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="lax",
        secure=get_settings().SESSION_COOKIE_SECURE,
    )
    return resp
