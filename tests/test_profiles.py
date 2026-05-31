"""Tests for the profile library (pure logic + DB-backed API)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.schemas import (
    CheckPreview,
    ProfileSummary,
    check_preview_from_bundle,
)
from app.services.ranking import RECENCY_BOOST, trending_score

from .conftest import requires_db

NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Pure-logic tests (no DB)
# --------------------------------------------------------------------------- #
def test_trending_score_is_deterministic() -> None:
    kwargs = dict(import_count=5, download_count=10, updated_at=NOW, now=NOW)
    assert trending_score(**kwargs) == trending_score(**kwargs)


def test_trending_score_weights_imports_above_downloads() -> None:
    old = NOW - timedelta(days=90)  # no recency boost either way
    imports_heavy = trending_score(import_count=10, download_count=0, updated_at=old, now=NOW)
    downloads_heavy = trending_score(import_count=0, download_count=10, updated_at=old, now=NOW)
    assert imports_heavy > downloads_heavy


def test_trending_score_monotonic_in_imports() -> None:
    old = NOW - timedelta(days=90)
    lo = trending_score(import_count=1, download_count=0, updated_at=old, now=NOW)
    hi = trending_score(import_count=2, download_count=0, updated_at=old, now=NOW)
    assert hi > lo


def test_trending_score_recency_boost_applies_within_window() -> None:
    recent = NOW - timedelta(days=5)
    old = NOW - timedelta(days=90)
    fresh = trending_score(import_count=0, download_count=0, updated_at=recent, now=NOW)
    stale = trending_score(import_count=0, download_count=0, updated_at=old, now=NOW)
    assert fresh - stale == RECENCY_BOOST


def test_check_preview_strips_sensitive_fields() -> None:
    bundle = {
        "checks": [
            {
                "name": "DB ping",
                "check_type": "postgres",
                "config": {"password": "s3cret", "host": "10.0.0.5"},
                "command": "psql -c 'select 1'",
            }
        ]
    }
    preview = check_preview_from_bundle(bundle)
    assert len(preview) == 1
    c = preview[0]
    assert c.name == "DB ping"
    assert c.check_type == "postgres"
    # 🔒 Security: the preview may now surface SAFE config params, but secret
    # values, the command body and the host/IP must never appear anywhere.
    assert c.params == []  # every config key here is secret/host → all dropped
    dumped = repr(c.model_dump())
    assert "s3cret" not in dumped
    assert "10.0.0.5" not in dumped
    assert "psql" not in dumped


def test_check_preview_surfaces_safe_fields() -> None:
    bundle = {
        "checks": [
            {
                "name": "CPU load",
                "check_type": "snmp",
                "description": "CPU via SNMP",
                "threshold_warn": 80,
                "threshold_crit": 95,
                "config": {
                    "oid": "1.3.6.1.4",
                    "interval_seconds": 60,
                    "snmp_community": "public",  # secret → dropped
                    "host": "10.0.0.9",  # network target → dropped
                },
            }
        ]
    }
    c = check_preview_from_bundle(bundle)[0]
    assert c.interval_seconds == 60
    assert c.threshold_warn == "80"
    assert c.threshold_crit == "95"
    assert c.description == "CPU via SNMP"
    keys = {p.key for p in c.params}
    assert "oid" in keys  # safe param surfaces
    assert "snmp_community" not in keys  # secret dropped
    assert "host" not in keys  # network target dropped


def test_check_preview_handles_type_alias_and_bad_data() -> None:
    bundle = {
        "checks": [
            {"name": "A", "type": "http"},  # 'type' alias
            {"name": "B"},  # missing type -> skipped
            "not-a-dict",  # skipped
            {"check_type": "ping"},  # missing name -> skipped
        ]
    }
    preview = check_preview_from_bundle(bundle)
    assert preview == [CheckPreview(name="A", check_type="http")]


def test_check_preview_empty_for_missing_or_malformed_bundle() -> None:
    assert check_preview_from_bundle(None) == []
    assert check_preview_from_bundle({}) == []
    assert check_preview_from_bundle({"checks": "nope"}) == []


def test_profile_summary_serialization() -> None:
    summary = ProfileSummary(
        id="abc",
        name="UniFi Switch",
        vendor="Ubiquiti",
        category="Network",
        icon="🔌",
        tier="official",
        approved=True,
        download_count=3,
        import_count=7,
        tags=["snmp", "network"],
        requires_snmp=True,
        vesana_min_version="0.30.0",
        latest_version_tag="1.0.0",
        updated_at=NOW,
    )
    data = summary.model_dump()
    assert data["name"] == "UniFi Switch"
    assert data["vote_score"] == 0  # read-only until C4
    assert data["tags"] == ["snmp", "network"]
    assert data["requires_snmp"] is True
    assert data["latest_version_tag"] == "1.0.0"


# --------------------------------------------------------------------------- #
# DB-backed tests (require Postgres via DATABASE_URL_TEST)
# --------------------------------------------------------------------------- #
def _make_profile(
    db,
    *,
    name: str,
    tier: str = "official",
    vendor: str = "Acme",
    category: str = "Network",
    import_count: int = 0,
    download_count: int = 0,
    approved: bool = True,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    version_tag: str = "1.0.0",
    checks: list[dict] | None = None,
):
    from app.models.community_profile import CommunityProfile
    from app.models.community_profile_version import CommunityProfileVersion

    profile = CommunityProfile(
        name=name,
        tier=tier,
        vendor=vendor,
        category=category,
        description_md=f"Description for {name}",
        import_count=import_count,
        download_count=download_count,
        approved=approved,
        tags=["sample"],
    )
    if created_at is not None:
        profile.created_at = created_at
    if updated_at is not None:
        profile.updated_at = updated_at
    db.add(profile)
    db.flush()
    version = CommunityProfileVersion(
        profile_id=profile.id,
        version_tag=version_tag,
        bundle_json={
            "schema_version": 1,
            "checks": checks or [{"name": "Ping", "check_type": "ping", "config": {"secret": "x"}}],
        },
        changelog_md="Initial.",
        is_current=True,
    )
    db.add(version)
    db.flush()
    profile.latest_version_id = version.id
    db.commit()
    pid, vid = profile.id, version.id
    return pid, vid


def _seed(rows):
    """Open a session on the (test-bound) engine, run ``rows(session)``."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        return rows(db)


@requires_db
def test_list_returns_official_profiles(db_app_client) -> None:
    _seed(lambda db: _make_profile(db, name="UniFi Switch", tier="official", vendor="Ubiquiti"))

    r = db_app_client.get("/api/v1/profiles")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    names = [item["name"] for item in body["items"]]
    assert "UniFi Switch" in names
    item = body["items"][0]
    assert item["tier"] == "official"
    assert item["vote_score"] == 0
    assert item["latest_version_tag"] == "1.0.0"


@requires_db
def test_list_filters_by_tier(db_app_client) -> None:
    def rows(db):
        _make_profile(db, name="Official One", tier="official")
        _make_profile(db, name="Beta One", tier="beta", approved=False)

    _seed(rows)

    r = db_app_client.get("/api/v1/profiles", params={"tier": "beta"})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Beta One"
    assert body["items"][0]["tier"] == "beta"


@requires_db
def test_list_filters_by_vendor(db_app_client) -> None:
    def rows(db):
        _make_profile(db, name="A", vendor="Ubiquiti")
        _make_profile(db, name="B", vendor="Synology")

    _seed(rows)

    r = db_app_client.get("/api/v1/profiles", params={"vendor": "Synology"})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["vendor"] == "Synology"


@requires_db
def test_sort_newest_vs_trending_differ(db_app_client) -> None:
    old = datetime(2026, 1, 1, tzinfo=UTC)
    recent = datetime(2026, 5, 29, tzinfo=UTC)

    def rows(db):
        # "Popular Old" has tons of imports but is old (no recency boost).
        _make_profile(
            db,
            name="Popular Old",
            import_count=100,
            created_at=old,
            updated_at=old,
            version_tag="1.0.0",
        )
        # "Fresh New" is brand new with no engagement.
        _make_profile(
            db,
            name="Fresh New",
            import_count=0,
            created_at=recent,
            updated_at=recent,
            version_tag="2.0.0",
        )

    _seed(rows)

    newest = db_app_client.get("/api/v1/profiles", params={"sort": "newest"}).json()
    trending = db_app_client.get("/api/v1/profiles", params={"sort": "trending"}).json()

    assert newest["items"][0]["name"] == "Fresh New"
    assert trending["items"][0]["name"] == "Popular Old"


@requires_db
def test_detail_returns_description_and_check_preview(db_app_client) -> None:
    pid, _ = _seed(
        lambda db: _make_profile(
            db,
            name="Detail Me",
            checks=[
                {
                    "name": "HTTP up",
                    "check_type": "http",
                    "config": {"token": "secret"},
                }
            ],
        )
    )

    r = db_app_client.get(f"/api/v1/profiles/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["description_md"] == "Description for Detail Me"
    assert body["uploader"] == "Vesana Team"
    assert body["check_preview"] == [{"name": "HTTP up", "check_type": "http"}]


@requires_db
def test_bundle_returns_json_and_increments_download_count(db_app_client) -> None:
    pid, vid = _seed(lambda db: _make_profile(db, name="Bundle Me", download_count=0))

    r = db_app_client.get(f"/api/v1/profiles/{pid}/versions/{vid}/bundle")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["schema_version"] == 1
    assert "checks" in bundle

    # download_count incremented and reflected in the detail view.
    detail = db_app_client.get(f"/api/v1/profiles/{pid}").json()
    assert detail["download_count"] == 1


@requires_db
def test_detail_404_for_unknown_id(db_app_client) -> None:
    r = db_app_client.get("/api/v1/profiles/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "Profile not found"


@requires_db
def test_bundle_404_for_unknown_version(db_app_client) -> None:
    pid, _ = _seed(lambda db: _make_profile(db, name="Has Versions"))

    r = db_app_client.get(f"/api/v1/profiles/{pid}/versions/nope/bundle")
    assert r.status_code == 404


@requires_db
def test_seed_is_idempotent(db_app_client) -> None:
    from app.db import SessionLocal
    from app.seed import seed

    with SessionLocal() as db:
        first = seed(db)
    with SessionLocal() as db:
        second = seed(db)
    assert first == second

    r = db_app_client.get("/api/v1/profiles", params={"limit": 100})
    body = r.json()
    # Re-running seed must not duplicate profiles.
    assert body["total"] == first
    names = {item["name"] for item in body["items"]}
    assert "UniFi Switch" in names
