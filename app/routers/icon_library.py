"""JSON API for the icon library (search index + file serving).

Read-only and public, like the profile browse API: the content is
public-domain/openly-licensed artwork mirrored from dashboard-icons and
simple-icons, so there is nothing to protect. Instances proxy these endpoints
server-side (the customer's browser never talks to the hub directly).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, defer

from app.db import get_db
from app.models.library_icon import LibraryIcon

router = APIRouter(prefix="/api/v1/icon-library", tags=["icon-library"])

DbDep = Annotated[Session, Depends(get_db)]

_MEDIA_TYPES = {
    "svg": "image/svg+xml",
    "png": "image/png",
}


class IconLibraryItem(BaseModel):
    slug: str
    name: str
    aliases: list[str] = []
    categories: list[str] = []
    source: str
    monochrome: bool = False
    file_format: str
    file_size_bytes: int
    sha256: str
    has_dark: bool = False
    dark_sha256: str | None = None


class IconLibraryListResponse(BaseModel):
    items: list[IconLibraryItem]
    total: int


def _to_item(icon: LibraryIcon) -> IconLibraryItem:
    return IconLibraryItem(
        slug=icon.slug,
        name=icon.name,
        aliases=list(icon.aliases or []),
        categories=list(icon.categories or []),
        source=icon.source,
        monochrome=icon.monochrome,
        file_format=icon.file_format,
        file_size_bytes=icon.file_size_bytes,
        sha256=icon.sha256,
        has_dark=bool(icon.dark_sha256),
        dark_sha256=icon.dark_sha256,
    )


@router.get("", response_model=IconLibraryListResponse)
def list_icons(
    db: DbDep,
    q: str | None = Query(None, max_length=128, description="Substring match on slug/name/aliases"),
    source: str | None = Query(None, description="'dashboard-icons' | 'simple-icons'"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> IconLibraryListResponse:
    """Search the icon index. Bodies are deferred — the list stays lightweight."""

    stmt = select(LibraryIcon).options(defer(LibraryIcon.body), defer(LibraryIcon.dark_body))
    if source:
        stmt = stmt.where(LibraryIcon.source == source)
    if q:
        term = q.strip().lower()
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                func.lower(LibraryIcon.slug).like(like),
                func.lower(LibraryIcon.name).like(like),
                func.lower(func.array_to_string(LibraryIcon.aliases, " ")).like(like),
            )
        )
        # Prefix matches first ("syno" → Synology before "Asustor Synology-…").
        prefix_rank = case(
            (func.lower(LibraryIcon.slug).like(f"{term}%"), 0),
            (func.lower(LibraryIcon.name).like(f"{term}%"), 0),
            else_=1,
        )
        order = (prefix_rank, func.lower(LibraryIcon.name))
    else:
        order = (func.lower(LibraryIcon.name),)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(*order).limit(limit).offset(offset)).scalars().all()
    return IconLibraryListResponse(items=[_to_item(r) for r in rows], total=int(total))


class IconLibraryStats(BaseModel):
    total: int
    last_synced_at: str | None = None
    sources: dict[str, int] = {}


@router.get("/stats", response_model=IconLibraryStats)
def library_stats(db: DbDep) -> IconLibraryStats:
    """Tiny ops endpoint: how many icons, per source, when last synced."""

    rows = db.execute(select(LibraryIcon.source, func.count()).group_by(LibraryIcon.source)).all()
    last = db.execute(select(func.max(LibraryIcon.updated_at))).scalar()
    sources = {src: int(cnt) for src, cnt in rows}
    return IconLibraryStats(
        total=sum(sources.values()),
        last_synced_at=last.isoformat() if last else None,
        sources=sources,
    )


@router.get("/{slug}/file")
def serve_icon_file(
    slug: str,
    request: Request,
    db: DbDep,
    variant: str = Query("light", description="'light' | 'dark' — dark falls back to light"),
) -> Response:
    """Serve the raw icon body. ETag per variant + immutable caching.

    ``variant=dark`` silently falls back to the light body when no dark variant
    exists — mirrors Vesana's own icon file endpoint so clients never need to
    know beforehand.
    """

    icon = db.execute(
        select(LibraryIcon).where(LibraryIcon.slug == slug.strip().lower())
    ).scalar_one_or_none()
    if icon is None:
        raise HTTPException(status_code=404, detail="Icon not found")

    serve_dark = variant.lower() == "dark" and icon.dark_body is not None
    body = icon.dark_body if serve_dark else icon.body
    file_format = (icon.dark_file_format if serve_dark else icon.file_format) or "svg"
    etag = f'"{icon.dark_sha256 if serve_dark else icon.sha256}"'

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=body,
        media_type=_MEDIA_TYPES.get(file_format, "application/octet-stream"),
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            # Defense in depth: never execute anything if an SVG is opened
            # directly in a browser tab.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )
