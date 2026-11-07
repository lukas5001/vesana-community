"""JSON API for the profile library (browse, detail, versions, bundle, signed).

Vesana-Bibliothek (2026-11-07): schreibgeschützter, signierter Katalog. Jede
Instanz schickt ``X-Vesana-Channel: tester|stable`` (fehlt/unbekannt ⇒ stable);
welche Version sie sieht, entscheidet allein
``app.services.profiles.is_released_for``. Die Antwortformen bleiben
rückwärtskompatibel — ältere Vesana-Instanzen rufen list/detail/bundle weiter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
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
from app.services import profile_lint as profile_lint_service
from app.services.profiles import (
    ProfileFilters,
    get_profile,
    is_released_for,
    latest_released_version,
    list_profiles,
    normalize_channel,
    released_profiles,
    released_versions,
)

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])

DbDep = Annotated[Session, Depends(get_db)]


def get_channel(
    x_vesana_channel: Annotated[str | None, Header(alias="X-Vesana-Channel")] = None,
) -> str:
    return normalize_channel(x_vesana_channel)


ChannelDep = Annotated[str, Depends(get_channel)]


def _to_summary(
    profile: CommunityProfile, channel: str, now: datetime | None = None
) -> ProfileSummary:
    latest = latest_released_version(profile, channel, now)
    return ProfileSummary(
        id=profile.id,
        name=profile.name,
        vendor=profile.vendor,
        category=profile.category,
        icon=profile.icon,
        tier=profile.tier,
        approved=profile.approved,
        review_status=profile.review_status,
        has_scripts=profile.has_scripts,
        vote_score=profile.vote_score,
        download_count=profile.download_count,
        import_count=profile.import_count,
        tags=list(profile.tags or []),
        requires_agent=profile.requires_agent,
        requires_collector=profile.requires_collector,
        requires_snmp=profile.requires_snmp,
        requires_ssh=profile.requires_ssh,
        requires_api_token=profile.requires_api_token,
        requires_device_api=profile.requires_device_api,
        vesana_min_version=profile.vesana_min_version,
        latest_version_tag=latest.version_tag if latest is not None else None,
        latest_version_id=latest.id if latest is not None else None,
        updated_at=profile.updated_at,
    )


def _uploader_display(db: Session, profile: CommunityProfile) -> str:
    if profile.tier in ("official", "beta") or not profile.uploader_instance_uuid:
        return VESANA_TEAM_UPLOADER
    instance = db.get(Instance, profile.uploader_instance_uuid)
    if instance is not None and instance.display_name:
        return instance.display_name
    return VESANA_TEAM_UPLOADER


def _to_detail(db: Session, profile: CommunityProfile, channel: str) -> ProfileDetail:
    current = latest_released_version(profile, channel)
    bundle = current.bundle_json if current is not None else None
    return ProfileDetail(
        **_to_summary(profile, channel).model_dump(),
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
    channel: ChannelDep,
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
    profiles, total = list_profiles(db, filters, channel=channel)
    return ProfileListResponse(
        items=[_to_summary(p, channel) for p in profiles],
        total=total,
    )


@router.get("/match-rules")
def profile_match_rules(db: DbDep, channel: ChannelDep) -> list[dict[str, Any]]:
    """Discovery match_rules of all visible profiles (community-first classifier).

    A Vesana instance fetches this to classify discovered devices against the
    library and suggest a profile to import. Nur Profile mit einer für den Kanal
    freigegebenen Version — sonst schlüge die Discovery ein Profil vor, das die
    Instanz gar nicht laden darf. Registered BEFORE the ``/{profile_id}`` route
    so "match-rules" is not read as an id.
    """
    rows = released_profiles(
        db,
        channel,
        CommunityProfile.review_status == "approved",
        CommunityProfile.match_rules.isnot(None),
    )
    # Defensive: skip rows whose match_rules is an empty/JSON-null value (these
    # deserialize to a falsy Python value) so the classifier never receives a
    # profile it cannot match against.
    return [
        {"community_id": p.id, "name": p.name, "tier": p.tier, "match_rules": p.match_rules}
        for p, _version in rows
        if p.match_rules
    ]


@router.get("/{profile_id}", response_model=ProfileDetail)
def get_profile_endpoint(profile_id: str, db: DbDep, channel: ChannelDep) -> ProfileDetail:
    profile = get_profile(db, profile_id, channel)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return _to_detail(db, profile, channel)


@router.get("/{profile_id}/versions", response_model=list[VersionSummary])
def list_versions_endpoint(profile_id: str, db: DbDep, channel: ChannelDep) -> list[VersionSummary]:
    """Nur die für den Kanal freigegebenen Versionen. ``is_current`` heißt hier
    „aktuelle Version für DEINEN Kanal" (die neueste freigegebene)."""
    profile = get_profile(db, profile_id, channel)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    versions = released_versions(profile, channel)
    current_id = versions[0].id if versions else None
    return [
        VersionSummary(
            id=v.id,
            version_tag=v.version_tag,
            is_current=v.id == current_id,
            created_at=v.created_at,
            changelog_md=v.changelog_md,
        )
        for v in versions
    ]


def _released_version_or_404(
    db: Session, profile_id: str, version_id: str, channel: str
) -> tuple[CommunityProfile, CommunityProfileVersion]:
    profile = get_profile(db, profile_id, channel)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    version = db.execute(
        select(CommunityProfileVersion).where(
            CommunityProfileVersion.id == version_id,
            CommunityProfileVersion.profile_id == profile_id,
        )
    ).scalar_one_or_none()
    # Unsigniert oder für den Kanal (noch) nicht freigegeben ⇒ wie nicht vorhanden.
    if version is None or not is_released_for(version, channel):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return profile, version


def _count_download(db: Session, profile: CommunityProfile) -> None:
    profile.download_count = profile.download_count + 1
    db.add(profile)
    db.commit()


@router.get("/{profile_id}/versions/{version_id}/bundle")
def get_bundle_endpoint(
    profile_id: str, version_id: str, db: DbDep, channel: ChannelDep
) -> dict[str, Any]:
    """Legacy: the raw bundle for older Vesana instances, and count the download.

    Nur für Versionen, die für den Kanal freigegeben sind — alte Instanzen
    schicken keinen Kanal-Kopf und gelten als stable."""
    profile, version = _released_version_or_404(db, profile_id, version_id, channel)
    bundle = version.bundle_json
    _count_download(db, profile)
    return bundle


@router.get("/{profile_id}/versions/{version_id}/signed")
def get_signed_endpoint(
    profile_id: str, version_id: str, db: DbDep, channel: ChannelDep
) -> dict[str, Any]:
    """Die gespeicherte signierte Hülle der Version, unverändert.

    Die Instanz prüft die Signatur selbst und importiert nur den geprüften
    ``payload``. Zählt den Download wie ``/bundle``."""
    profile, version = _released_version_or_404(db, profile_id, version_id, channel)
    envelope = version.signed_envelope
    if not envelope:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not signed")
    _count_download(db, profile)
    return envelope


# Version + Fingerabdruck der Profil-Prüfung (vorher in ``uploads.py``). Vesana
# vergleicht nachts den eigenen Fingerabdruck damit — bleibt deshalb erhalten.
lint_router = APIRouter(tags=["profiles"])


@lint_router.get("/api/v1/profile-lint")
def profile_lint_info() -> dict:
    """Version + Fingerabdruck der Profil-Prüfung. Vesana vergleicht nachts den
    eigenen Fingerabdruck damit — die wortgleiche Kopie darf nicht abdriften."""
    return profile_lint_service.info()
