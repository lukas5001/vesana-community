"""FastAPI application factory and HTML index for vesana-community."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.routers import (
    admin_api,
    admin_auth,
    admin_pages,
    auth,
    community_interactions,
    health,
    notifications,
    pages,
    profiles,
    qa,
    uploads,
)
from app.version import VERSION

_BASE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _BASE_DIR / "static"


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Vesana Community Hub", version=VERSION)

    # Signed, httponly session cookie keyed on SECRET_KEY (itsdangerous).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="vesana_community_session",
        https_only=settings.SESSION_COOKIE_SECURE,
        same_site="lax",
    )

    # Strict CSP is safe here: the site has NO inline scripts/styles/handlers
    # (CSS + JS are external files); only avatar <img> uses a data: URI.
    _CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "object-src 'none'"
    )

    @app.middleware("http")
    async def _security_headers(request, call_next):
        """Baseline security headers on every response."""
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(profiles.router)
    app.include_router(community_interactions.router)
    app.include_router(qa.router)
    app.include_router(uploads.router)
    app.include_router(notifications.router)
    # Admin: session-based login page + server-rendered admin pages + JSON API.
    app.include_router(admin_auth.router)
    app.include_router(admin_api.router)
    app.include_router(admin_pages.router)
    # Pages router owns "/" (the browse view) and "/p/{id}" (detail).
    app.include_router(pages.router)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


app = create_app()
