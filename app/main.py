"""FastAPI application factory and HTML index for vesana-community."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.routers import auth, community_interactions, health, pages, profiles
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
        https_only=False,
        same_site="lax",
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(profiles.router)
    app.include_router(community_interactions.router)
    # Pages router owns "/" (the browse view) and "/p/{id}" (detail).
    app.include_router(pages.router)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


app = create_app()
