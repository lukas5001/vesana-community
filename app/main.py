"""FastAPI application factory and HTML index for vesana-community."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.auth.deps import get_session_instance
from app.config import get_settings
from app.models.instance import Instance
from app.routers import auth, health
from app.version import VERSION

_BASE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _BASE_DIR / "static"
_TEMPLATES_DIR = _BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


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

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        instance: Annotated[Instance | None, Depends(get_session_instance)],
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"instance": instance, "version": VERSION},
        )

    return app


app = create_app()
