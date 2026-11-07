"""Shared query helpers for the profile library (Vesana-Bibliothek).

Used by the JSON API (``app/routers/profiles.py``) and the admin MIB overview.
All queries are sync SQLAlchemy 2.0 against the community schema.

Kanäle (2026-11-07): eine Instanz schickt ``X-Vesana-Channel: tester|stable``
(fehlt/unbekannt ⇒ stable). Welche Version eine Instanz sieht, entscheidet
AUSSCHLIESSLICH ``is_released_for`` — nicht mehr ``is_current``.
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


# ---- Kanäle ------------------------------------------------------------------

CHANNEL_TESTER = "tester"
CHANNEL_STABLE = "stable"
CHANNELS = (CHANNEL_TESTER, CHANNEL_STABLE)


def normalize_channel(value: str | None) -> str:
    """``X-Vesana-Channel`` → Kanal. Fehlt oder unbekannt ⇒ stable (alte Instanzen
    schicken den Kopf nicht und bekommen so nur, was für alle freigegeben ist)."""
    v = (value or "").strip().lower()
    return v if v in CHANNELS else CHANNEL_STABLE


def is_released_for(
    version: CommunityProfileVersion, channel: str, now: datetime | None = None
) -> bool:
    """DIE eine Regel, ob eine Version an einen Kanal ausgeliefert wird.

    Freigegeben für Kanal c ⇔ signiert (``signed_envelope`` gesetzt) UND
    (c == tester ODER ``released_all_at <= now``). Eine unsignierte Version
    sieht niemand; ohne ``released_all_at`` bleibt sie beim Tester-Kanal.
    """
    if not version.signed_envelope:
        return False
    if normalize_channel(channel) == CHANNEL_TESTER:
        return True
    released = version.released_all_at
    if released is None:
        return False
    now = now or datetime.now(UTC)
    if released.tzinfo is None:
        released = released.replace(tzinfo=UTC)
    return released <= now


def released_versions(
    profile: CommunityProfile, channel: str, now: datetime | None = None
) -> list[CommunityProfileVersion]:
    """Die für den Kanal freigegebenen Versionen, neueste zuerst."""
    now = now or datetime.now(UTC)
    versions = sorted(profile.versions or [], key=lambda v: v.created_at, reverse=True)
    return [v for v in versions if is_released_for(v, channel, now)]


def latest_released_version(
    profile: CommunityProfile, channel: str, now: datetime | None = None
) -> CommunityProfileVersion | None:
    """Die aktuelle Version für den Kanal (neueste freigegebene) oder None."""
    released = released_versions(profile, channel, now)
    return released[0] if released else None


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

    Seit der Vesana-Bibliothek (2026-11-07) gibt es keine Uploads mehr; die
    Klausel bleibt für den Altbestand. Zusätzlich muss jede ausgelieferte
    Version für den Kanal freigegeben sein (``is_released_for``).
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
    channel: str = CHANNEL_STABLE,
    now: datetime | None = None,
) -> tuple[list[CommunityProfile], int]:
    """Return (page_of_profiles, total_matching).

    Nur Profile mit einer für den Kanal freigegebenen Version zählen — ein
    Profil ohne signierte/freigegebene Version ist für diesen Kanal unsichtbar.
    Sorting is done in Python so the trending formula stays identical to the
    unit-tested pure function in ``app.services.ranking``.
    """
    now = now or datetime.now(UTC)
    sort = filters.sort if filters.sort in SORT_OPTIONS else DEFAULT_SORT
    limit = clamp_limit(filters.limit)
    offset = max(0, filters.offset)

    stmt = _apply_filters(_base_select(), filters).options(selectinload(CommunityProfile.versions))
    profiles = [
        p
        for p in db.execute(stmt).scalars().all()
        if latest_released_version(p, channel, now) is not None
    ]
    total = len(profiles)
    if sort == "name":
        profiles.sort(key=lambda p: (p.name or "").casefold())
    else:
        profiles.sort(key=lambda p: _sort_key(p, sort, now), reverse=True)
    page = profiles[offset : offset + limit]
    return page, total


def get_profile(
    db: Session, profile_id: str, channel: str = CHANNEL_STABLE
) -> CommunityProfile | None:
    """Fetch a single visible profile (None if missing/removed/hidden or if it has
    no version released for ``channel``)."""
    stmt = (
        _base_select()
        .where(CommunityProfile.id == profile_id)
        .options(selectinload(CommunityProfile.versions))
    )
    profile = db.execute(stmt).scalar_one_or_none()
    if profile is None or latest_released_version(profile, channel) is None:
        return None
    return profile


def released_profiles(
    db: Session, channel: str = CHANNEL_STABLE, *extra_where
) -> list[tuple[CommunityProfile, CommunityProfileVersion]]:
    """(Profil, freigegebene aktuelle Version) aller sichtbaren Profile mit einer
    für den Kanal freigegebenen Version, nach Name sortiert."""
    now = datetime.now(UTC)
    stmt = (
        _base_select()
        .where(*extra_where)
        .options(selectinload(CommunityProfile.versions))
        .order_by(func.lower(CommunityProfile.name))
    )
    out = []
    for profile in db.execute(stmt).scalars().all():
        version = latest_released_version(profile, channel, now)
        if version is not None:
            out.append((profile, version))
    return out


def profiles_by_mib(db: Session, channel: str = CHANNEL_STABLE) -> dict[str, list[dict[str, str]]]:
    """MIB-Modul → sichtbare Profile, deren für den Kanal AKTUELLE (neueste
    freigegebene) Version es nennt (``bundle.mibs``). Speist die Kurator-Sicht
    der MIB-Bibliothek („benutzt von …")."""
    from app.services.mib_library import mib_refs_of_bundle

    out: dict[str, list[dict[str, str]]] = {}
    for profile, version in released_profiles(db, channel):
        for ref in mib_refs_of_bundle(version.bundle_json):
            users = out.setdefault(ref["module"], [])
            if not any(u["id"] == profile.id for u in users):
                users.append({"id": profile.id, "name": profile.name})
    return out


def snmp_profiles_without_mibs(db: Session, channel: str = CHANNEL_STABLE) -> list[dict[str, str]]:
    """Sichtbare SNMP-Profile, deren aktuelle freigegebene Version KEINE MIB nennt —
    die Kurator-Liste „hier fehlt vielleicht ein Verweis" (Standard-MIBs liefert
    Vesana selbst mit, deshalb ist nicht jeder Eintrag ein Fehler)."""
    from app.services.mib_library import mib_refs_of_bundle

    return [
        {"id": profile.id, "name": profile.name}
        for profile, version in released_profiles(
            db, channel, CommunityProfile.requires_snmp.is_(True)
        )
        if not mib_refs_of_bundle(version.bundle_json)
    ]
