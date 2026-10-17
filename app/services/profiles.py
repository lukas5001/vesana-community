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
from app.models.community_profile_version import CommunityProfileVersion
from app.services.ranking import trending_score

# Tiers always visible without per-profile approval. Community-tier visibility
# (approved vs "waiting for review") is layered on in C3; the seam is here.
VISIBLE_TIERS = ("official", "beta")

SORT_OPTIONS = ("trending", "popularity", "newest", "name")
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
    requires_ssh: bool | None = None
    requires_api_token: bool | None = None
    sort: str = DEFAULT_SORT
    limit: int = DEFAULT_LIMIT
    offset: int = 0


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _visibility_clause():
    """Profiles a self-hoster may see.

    Visible: curated tiers (official/beta) OR a community upload that an admin
    has APPROVED. A community profile stays hidden while ``review_status`` is
    'pending' (never reviewed) or 'rejected', and so do all of its versions and
    its bundle (every reader — browse, detail, versions, bundle download, MIB
    pages — funnels through this clause via ``_base_select``/``get_profile``).

    Why approval and not merely "not rejected" (supply-chain hardening,
    2026-10-17): any registered instance may upload, the upload scanner is
    advisory and never blocks, and a bundle carries script bodies that run as
    SYSTEM / over SSH on the instances that import it. Showing an unreviewed
    upload — and serving its bundle — made the hub a self-service delivery
    channel for malicious content before any human looked at it. ``review_status``
    is profile-wide and a new version resets it to 'pending' (see
    ``uploads.create_or_version_profile``), so pushing a poisoned v2 onto an
    already-approved profile takes the WHOLE profile offline until an admin
    re-approves it — fail-closed, by design.
    """
    return (
        CommunityProfile.is_removed.is_(False),
        or_(
            CommunityProfile.tier.in_(VISIBLE_TIERS),
            CommunityProfile.review_status == "approved",
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
    if filters.requires_ssh is not None:
        stmt = stmt.where(CommunityProfile.requires_ssh.is_(filters.requires_ssh))
    if filters.requires_api_token is not None:
        stmt = stmt.where(CommunityProfile.requires_api_token.is_(filters.requires_api_token))
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
    if sort == "name":
        profiles.sort(key=lambda p: (p.name or "").casefold())
    else:
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


def profiles_by_mib(db: Session) -> dict[str, list[dict[str, str]]]:
    """MIB-Modul → sichtbare Profile, deren AKTUELLE Version es nennt
    (``bundle.mibs``). Speist die MIB-Seiten des Hubs („benutzt von …")."""
    from app.services.mib_library import mib_refs_of_bundle

    rows = db.execute(
        select(CommunityProfile.id, CommunityProfile.name, CommunityProfileVersion.bundle_json)
        .join(
            CommunityProfileVersion,
            (CommunityProfileVersion.profile_id == CommunityProfile.id)
            & CommunityProfileVersion.is_current.is_(True),
        )
        .where(*_visibility_clause(), CommunityProfileVersion.bundle_json.has_key("mibs"))
        .order_by(func.lower(CommunityProfile.name))
    ).all()
    out: dict[str, list[dict[str, str]]] = {}
    for pid, name, bundle in rows:
        for ref in mib_refs_of_bundle(bundle):
            users = out.setdefault(ref["module"], [])
            if not any(u["id"] == pid for u in users):
                users.append({"id": pid, "name": name})
    return out


def snmp_profiles_without_mibs(db: Session) -> list[dict[str, str]]:
    """Sichtbare SNMP-Profile, deren aktuelle Version KEINE MIB nennt — die
    Kurator-Liste „hier fehlt vielleicht ein Verweis" (Standard-MIBs liefert
    Vesana selbst mit, deshalb ist nicht jeder Eintrag ein Fehler)."""
    rows = db.execute(
        select(CommunityProfile.id, CommunityProfile.name, CommunityProfileVersion.bundle_json)
        .join(
            CommunityProfileVersion,
            (CommunityProfileVersion.profile_id == CommunityProfile.id)
            & CommunityProfileVersion.is_current.is_(True),
        )
        .where(*_visibility_clause(), CommunityProfile.requires_snmp.is_(True))
        .order_by(func.lower(CommunityProfile.name))
    ).all()
    from app.services.mib_library import mib_refs_of_bundle

    return [{"id": pid, "name": name} for pid, name, b in rows if not mib_refs_of_bundle(b)]
