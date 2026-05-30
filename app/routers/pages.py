"""Server-rendered pages for community.vesana.org (browse + detail)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_session_instance
from app.db import get_db
from app.models.community_profile import CommunityProfile
from app.models.instance import Instance
from app.schemas import VESANA_TEAM_UPLOADER, check_preview_from_bundle
from app.services.profiles import (
    SORT_OPTIONS,
    ProfileFilters,
    get_profile,
    latest_version_tag,
    list_profiles,
)
from app.templating import templates
from app.version import VERSION

router = APIRouter(tags=["pages"])

DbDep = Annotated[Session, Depends(get_db)]
SessionInstance = Annotated[Instance | None, Depends(get_session_instance)]


def _distinct_values(db: Session, column) -> list[str]:
    rows = db.execute(
        select(column)
        .where(
            column.is_not(None),
            CommunityProfile.is_removed.is_(False),
        )
        .distinct()
        .order_by(column)
    ).scalars()
    return [r for r in rows if r]


@router.get("/", response_class=HTMLResponse)
@router.get("/browse", response_class=HTMLResponse, include_in_schema=False)
def browse_page(
    request: Request,
    db: DbDep,
    instance: SessionInstance,
    q: Annotated[str | None, Query()] = None,
    tier: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    vendor: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "trending",
) -> HTMLResponse:
    filters = ProfileFilters(q=q, tier=tier, category=category, vendor=vendor, sort=sort, limit=100)
    profiles, total = list_profiles(db, filters)
    cards = [
        {
            "id": p.id,
            "name": p.name,
            "vendor": p.vendor,
            "category": p.category,
            "icon": p.icon,
            "tier": p.tier,
            "import_count": p.import_count,
            "download_count": p.download_count,
            "vote_score": 0,
            "tags": list(p.tags or []),
            "version_tag": latest_version_tag(p),
        }
        for p in profiles
    ]
    context = {
        "instance": instance,
        "version": VERSION,
        "profiles": cards,
        "total": total,
        "q": q or "",
        "active_tier": tier or "",
        "active_category": category or "",
        "active_vendor": vendor or "",
        "active_sort": sort if sort in SORT_OPTIONS else "trending",
        "sort_options": SORT_OPTIONS,
        "categories": _distinct_values(db, CommunityProfile.category),
        "vendors": _distinct_values(db, CommunityProfile.vendor),
    }
    return templates.TemplateResponse(request, "browse.html", context)


@router.get("/p/{profile_id}", response_class=HTMLResponse)
def detail_page(
    profile_id: str,
    request: Request,
    db: DbDep,
    instance: SessionInstance,
) -> HTMLResponse:
    profile = get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    current = profile.current_version
    bundle = current.bundle_json if current is not None else None
    if profile.tier in ("official", "beta") or not profile.uploader_instance_uuid:
        uploader = VESANA_TEAM_UPLOADER
    else:
        uploader_instance = db.get(Instance, profile.uploader_instance_uuid)
        uploader = (
            uploader_instance.display_name
            if uploader_instance is not None and uploader_instance.display_name
            else VESANA_TEAM_UPLOADER
        )
    context = {
        "instance": instance,
        "version": VERSION,
        "profile": profile,
        "uploader": uploader,
        "check_preview": check_preview_from_bundle(bundle),
        "current_version": current,
        "latest_version_tag": latest_version_tag(profile),
        "now": datetime.now(UTC),
    }
    return templates.TemplateResponse(request, "detail.html", context)
