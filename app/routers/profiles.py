"""JSON API for the profile library (browse, detail, versions, bundle)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.community_profile import CommunityProfile
from app.models.community_profile_version import CommunityProfileVersion
from app.models.instance import Instance
from app.schemas import (
    VESANA_TEAM_UPLOADER,
    ProfileDetail,
    ProfileListResponse,
    ProfileSummary,
    VersionSummary,
    check_preview_from_bundle,
)
from app.services.profiles import (
    ProfileFilters,
    get_profile,
    latest_version_tag,
    list_profiles,
)

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])

DbDep = Annotated[Session, Depends(get_db)]


def _to_summary(profile: CommunityProfile) -> ProfileSummary:
    return ProfileSummary(
        id=profile.id,
        name=profile.name,
        vendor=profile.vendor,
        category=profile.category,
        icon=profile.icon,
        tier=profile.tier,
        approved=profile.approved,
        vote_score=0,  # real votes land in C4
        download_count=profile.download_count,
        import_count=profile.import_count,
        tags=list(profile.tags or []),
        requires_agent=profile.requires_agent,
        requires_collector=profile.requires_collector,
        requires_snmp=profile.requires_snmp,
        vesana_min_version=profile.vesana_min_version,
        latest_version_tag=latest_version_tag(profile),
        updated_at=profile.updated_at,
    )


def _uploader_display(db: Session, profile: CommunityProfile) -> str:
    if profile.tier in ("official", "beta") or not profile.uploader_instance_uuid:
        return VESANA_TEAM_UPLOADER
    instance = db.get(Instance, profile.uploader_instance_uuid)
    if instance is not None and instance.display_name:
        return instance.display_name
    return VESANA_TEAM_UPLOADER


def _to_detail(db: Session, profile: CommunityProfile) -> ProfileDetail:
    current = profile.current_version
    bundle = current.bundle_json if current is not None else None
    return ProfileDetail(
        **_to_summary(profile).model_dump(),
        description_md=profile.description_md,
        created_at=profile.created_at,
        uploader=_uploader_display(db, profile),
        current_changelog_md=current.changelog_md if current is not None else None,
        check_preview=check_preview_from_bundle(bundle),
    )


@router.get("", response_model=ProfileListResponse)
@router.get("/", response_model=ProfileListResponse, include_in_schema=False)
def list_profiles_endpoint(
    db: DbDep,
    q: Annotated[str | None, Query()] = None,
    tier: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    vendor: Annotated[str | None, Query()] = None,
    requires_agent: Annotated[bool | None, Query()] = None,
    requires_collector: Annotated[bool | None, Query()] = None,
    requires_snmp: Annotated[bool | None, Query()] = None,
    sort: Annotated[str, Query()] = "trending",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProfileListResponse:
    filters = ProfileFilters(
        q=q,
        tier=tier,
        category=category,
        vendor=vendor,
        requires_agent=requires_agent,
        requires_collector=requires_collector,
        requires_snmp=requires_snmp,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    profiles, total = list_profiles(db, filters)
    return ProfileListResponse(
        items=[_to_summary(p) for p in profiles],
        total=total,
    )


@router.get("/{profile_id}", response_model=ProfileDetail)
def get_profile_endpoint(profile_id: str, db: DbDep) -> ProfileDetail:
    profile = get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return _to_detail(db, profile)


@router.get("/{profile_id}/versions", response_model=list[VersionSummary])
def list_versions_endpoint(profile_id: str, db: DbDep) -> list[VersionSummary]:
    profile = get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return [VersionSummary.model_validate(v) for v in profile.versions]


@router.get("/{profile_id}/versions/{version_id}/bundle")
def get_bundle_endpoint(profile_id: str, version_id: str, db: DbDep) -> dict[str, Any]:
    """Return the raw bundle for Vesana to import, and count the download."""
    profile = get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    version = db.execute(
        select(CommunityProfileVersion).where(
            CommunityProfileVersion.id == version_id,
            CommunityProfileVersion.profile_id == profile_id,
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    profile.download_count = profile.download_count + 1
    db.add(profile)
    db.commit()
    return version.bundle_json
