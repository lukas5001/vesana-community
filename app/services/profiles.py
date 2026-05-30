"""Shared query helpers for the profile library.

Used by both the JSON API (``app/routers/profiles.py``) and the
server-rendered pages (``app/routers/pages.py``) so browse/detail behave
identically. All queries are sync SQLAlchemy 2.0 against the community schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.community_profile import CommunityProfile
from app.services.ranking import trending_score

# Tiers always visible without per-profile approval. Community-tier visibility
# (approved vs "waiting for review") is layered on in C3; the seam is here.
VISIBLE_TIERS = ("official", "beta")

SORT_OPTIONS = ("popularity", "newest", "trending")
DEFAULT_SORT = "trending"
DEFAULT_LIMIT = 30
MAX_LIMIT = 100


@dataclass(frozen=True)
class ProfileFilters:
    q: str | None = None
    tier: str | None = None
    category: str | None = None
    vendor: str | None = None
    requires_agent: bool | None = None
    requires_collector: bool | None = None
    requires_snmp: bool | None = None
    sort: str = DEFAULT_SORT
    limit: int = DEFAULT_LIMIT
    offset: int = 0


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _visibility_clause():
    """Profiles a self-hoster may see: visible tiers OR approved, not removed."""
    return (
        CommunityProfile.is_removed.is_(False),
        or_(
            CommunityProfile.tier.in_(VISIBLE_TIERS),
            CommunityProfile.approved.is_(True),
        ),
    )


def _base_select():
    return select(CommunityProfile).where(*_visibility_clause())


def _apply_filters(stmt, filters: ProfileFilters):
    if filters.q:
        like = f"%{filters.q}%"
        stmt = stmt.where(
            or_(
                CommunityProfile.name.ilike(like),
                CommunityProfile.description_md.ilike(like),
                CommunityProfile.vendor.ilike(like),
                # tag match: array contains the literal query term
                CommunityProfile.tags.any(filters.q),  # type: ignore[attr-defined]
            )
        )
    if filters.tier:
        stmt = stmt.where(CommunityProfile.tier == filters.tier)
    if filters.category:
        stmt = stmt.where(CommunityProfile.category == filters.category)
    if filters.vendor:
        stmt = stmt.where(CommunityProfile.vendor == filters.vendor)
    if filters.requires_agent is not None:
        stmt = stmt.where(CommunityProfile.requires_agent.is_(filters.requires_agent))
    if filters.requires_collector is not None:
        stmt = stmt.where(CommunityProfile.requires_collector.is_(filters.requires_collector))
    if filters.requires_snmp is not None:
        stmt = stmt.where(CommunityProfile.requires_snmp.is_(filters.requires_snmp))
    return stmt


def _sort_key(profile: CommunityProfile, sort: str, now: datetime):
    if sort == "popularity":
        return (profile.import_count, profile.download_count, profile.created_at)
    if sort == "newest":
        return (profile.created_at,)
    # trending (default)
    return (
        trending_score(
            import_count=profile.import_count,
            download_count=profile.download_count,
            updated_at=profile.updated_at,
            now=now,
        ),
        profile.import_count,
        profile.created_at,
    )


def list_profiles(
    db: Session,
    filters: ProfileFilters,
    *,
    now: datetime | None = None,
) -> tuple[list[CommunityProfile], int]:
    """Return (page_of_profiles, total_matching).

    Sorting is done in Python so the trending formula stays identical to the
    unit-tested pure function in ``app.services.ranking``.
    """
    now = now or datetime.now(UTC)
    sort = filters.sort if filters.sort in SORT_OPTIONS else DEFAULT_SORT
    limit = clamp_limit(filters.limit)
    offset = max(0, filters.offset)

    stmt = _apply_filters(_base_select(), filters).options(selectinload(CommunityProfile.versions))

    count_stmt = _apply_filters(
        select(func.count()).select_from(CommunityProfile).where(*_visibility_clause()),
        filters,
    )
    total = db.execute(count_stmt).scalar_one()

    profiles = list(db.execute(stmt).scalars().all())
    profiles.sort(key=lambda p: _sort_key(p, sort, now), reverse=True)
    page = profiles[offset : offset + limit]
    return page, total


def get_profile(db: Session, profile_id: str) -> CommunityProfile | None:
    """Fetch a single visible profile (or None if missing/removed/hidden)."""
    stmt = (
        _base_select()
        .where(CommunityProfile.id == profile_id)
        .options(selectinload(CommunityProfile.versions))
    )
    return db.execute(stmt).scalar_one_or_none()


def latest_version_tag(profile: CommunityProfile) -> str | None:
    current = profile.current_version
    if current is not None:
        return current.version_tag
    if profile.versions:
        return profile.versions[0].version_tag
    return None
