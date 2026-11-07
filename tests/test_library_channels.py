"""Vesana-Bibliothek: Kanäle, signierte Hülle, entfernte Routen.

Regel (``app.services.profiles.is_released_for``): eine Version ist für Kanal c
freigegeben ⇔ signiert UND (c == tester ODER released_all_at <= jetzt).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.community_profile_version import CommunityProfileVersion
from app.services.profiles import is_released_for, normalize_channel

from .conftest import requires_db

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
PAST = datetime.now(UTC) - timedelta(days=1)
FUTURE = datetime.now(UTC) + timedelta(days=7)
TESTER = {"X-Vesana-Channel": "tester"}
STABLE = {"X-Vesana-Channel": "stable"}


def _envelope(tag: str) -> dict:
    return {
        "format": "vesana-library-sig/1",
        "key_id": "test-key-1",
        "sig": f"c2lnLXt{tag}",
        "payload": '{"profile":{"name":"X"},"schema_version":1,"checks":[]}',
    }


# --------------------------------------------------------------------------- #
# Reine Logik
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "stable"),
        ("", "stable"),
        ("stable", "stable"),
        ("tester", "tester"),
        (" Tester ", "tester"),
        ("beta", "stable"),
        ("nightly", "stable"),
    ],
)
def test_normalize_channel_defaults_to_stable(raw, expected) -> None:
    assert normalize_channel(raw) == expected


def _v(*, signed: bool, released: datetime | None) -> CommunityProfileVersion:
    return CommunityProfileVersion(
        version_tag="1",
        bundle_json={},
        signed_envelope=_envelope("1") if signed else None,
        released_all_at=released,
    )


def test_unsigned_version_is_released_for_nobody() -> None:
    v = _v(signed=False, released=NOW - timedelta(days=30))
    assert not is_released_for(v, "tester", NOW)
    assert not is_released_for(v, "stable", NOW)


def test_tester_sees_signed_version_immediately_stable_only_after_release() -> None:
    v = _v(signed=True, released=NOW + timedelta(hours=1))
    assert is_released_for(v, "tester", NOW)
    assert not is_released_for(v, "stable", NOW)
    assert is_released_for(v, "stable", NOW + timedelta(hours=1))


def test_signed_without_release_date_stays_with_tester() -> None:
    v = _v(signed=True, released=None)
    assert is_released_for(v, "tester", NOW)
    assert not is_released_for(v, "stable", NOW)


def test_unknown_channel_is_treated_as_stable() -> None:
    v = _v(signed=True, released=NOW + timedelta(hours=1))
    assert not is_released_for(v, "beta", NOW)


# --------------------------------------------------------------------------- #
# DB-gestützt
# --------------------------------------------------------------------------- #
def _make(
    name: str,
    versions: list[tuple[str, bool, datetime | None]],
    *,
    match_rules: dict | None = None,
    tier: str = "official",
) -> tuple[str, list[str]]:
    """Profil mit Versionen ``(tag, signiert, released_all_at)``, älteste zuerst."""
    from app.db import SessionLocal
    from app.models.community_profile import CommunityProfile

    with SessionLocal() as db:
        profile = CommunityProfile(
            name=name,
            tier=tier,
            vendor="Acme",
            category="Network",
            approved=True,
            review_status="approved",
            match_rules=match_rules,
        )
        db.add(profile)
        db.flush()
        ids = []
        base = datetime.now(UTC) - timedelta(days=len(versions) + 1)
        for idx, (tag, signed, released) in enumerate(versions):
            version = CommunityProfileVersion(
                profile_id=profile.id,
                version_tag=tag,
                bundle_json={"schema_version": 1, "checks": [], "tag": tag},
                changelog_md=f"Stand {tag}",
                created_at=base + timedelta(days=idx),
                is_current=idx == len(versions) - 1,
                signed_envelope=_envelope(tag) if signed else None,
                released_all_at=released,
            )
            db.add(version)
            db.flush()
            ids.append(version.id)
        profile.latest_version_id = ids[-1]
        db.commit()
        return profile.id, ids


@requires_db
def test_list_latest_version_follows_the_channel(db_app_client) -> None:
    pid, (v1, v2) = _make("Switch", [("1.0", True, PAST), ("2.0", True, FUTURE)])
    c = db_app_client

    stable = c.get("/api/v1/profiles", headers=STABLE).json()
    tester = c.get("/api/v1/profiles", headers=TESTER).json()
    legacy = c.get("/api/v1/profiles").json()  # alte Instanz: kein Kopf ⇒ stable

    assert [
        (i["id"], i["latest_version_id"], i["latest_version_tag"]) for i in stable["items"]
    ] == [(pid, v1, "1.0")]
    assert [(i["latest_version_id"], i["latest_version_tag"]) for i in tester["items"]] == [
        (v2, "2.0")
    ]
    assert legacy == stable


@requires_db
def test_profiles_without_released_version_are_hidden_per_channel(db_app_client) -> None:
    _make("Nur unsigniert", [("1.0", False, PAST)])
    _make("Nur Tester", [("1.0", True, FUTURE)])
    _make("Für alle", [("1.0", True, PAST)])
    c = db_app_client

    stable = c.get("/api/v1/profiles", headers=STABLE).json()
    tester = c.get("/api/v1/profiles", headers=TESTER).json()
    assert {i["name"] for i in stable["items"]} == {"Für alle"}
    assert stable["total"] == 1
    assert {i["name"] for i in tester["items"]} == {"Für alle", "Nur Tester"}
    assert tester["total"] == 2


@requires_db
def test_detail_and_versions_use_released_versions_only(db_app_client) -> None:
    pid, (v1, v2, v3) = _make(
        "Router", [("1.0", True, PAST), ("2.0", True, FUTURE), ("3.0", False, None)]
    )
    c = db_app_client

    detail = c.get(f"/api/v1/profiles/{pid}", headers=STABLE).json()
    assert detail["latest_version_id"] == v1
    assert detail["current_changelog_md"] == "Stand 1.0"
    detail_t = c.get(f"/api/v1/profiles/{pid}", headers=TESTER).json()
    assert detail_t["latest_version_id"] == v2

    vs = c.get(f"/api/v1/profiles/{pid}/versions", headers=STABLE).json()
    assert [(v["id"], v["is_current"]) for v in vs] == [(v1, True)]
    vt = c.get(f"/api/v1/profiles/{pid}/versions", headers=TESTER).json()
    assert [(v["id"], v["is_current"]) for v in vt] == [(v2, True), (v1, False)]


@requires_db
def test_unsigned_only_profile_is_404_for_both_channels(db_app_client) -> None:
    pid, (vid,) = _make("Geheim", [("1.0", False, PAST)])
    for headers in (TESTER, STABLE, {}):
        assert db_app_client.get(f"/api/v1/profiles/{pid}", headers=headers).status_code == 404
        assert (
            db_app_client.get(
                f"/api/v1/profiles/{pid}/versions/{vid}/bundle", headers=headers
            ).status_code
            == 404
        )
        assert (
            db_app_client.get(
                f"/api/v1/profiles/{pid}/versions/{vid}/signed", headers=headers
            ).status_code
            == 404
        )


@requires_db
def test_signed_returns_the_stored_envelope_and_counts_download(db_app_client) -> None:
    pid, (vid,) = _make("Signiert", [("1.0", True, PAST)])
    c = db_app_client

    r = c.get(f"/api/v1/profiles/{pid}/versions/{vid}/signed")
    assert r.status_code == 200
    assert r.json() == _envelope("1.0")

    detail = c.get(f"/api/v1/profiles/{pid}").json()
    assert detail["download_count"] == 1


@requires_db
def test_signed_404s_when_not_released_for_channel_or_unknown(db_app_client) -> None:
    pid, (v1, v2, v3) = _make(
        "Kanal", [("1.0", True, PAST), ("2.0", True, FUTURE), ("3.0", False, None)]
    )
    c = db_app_client

    assert c.get(f"/api/v1/profiles/{pid}/versions/{v2}/signed", headers=STABLE).status_code == 404
    assert c.get(f"/api/v1/profiles/{pid}/versions/{v2}/signed").status_code == 404
    assert c.get(f"/api/v1/profiles/{pid}/versions/{v2}/signed", headers=TESTER).status_code == 200
    assert c.get(f"/api/v1/profiles/{pid}/versions/{v3}/signed", headers=TESTER).status_code == 404
    assert c.get(f"/api/v1/profiles/{pid}/versions/nope/signed", headers=TESTER).status_code == 404
    # Fremde Version unter falscher Profil-ID ⇒ 404.
    other, (ov,) = _make("Anderes", [("1.0", True, PAST)])
    assert c.get(f"/api/v1/profiles/{pid}/versions/{ov}/signed").status_code == 404

    detail = c.get(f"/api/v1/profiles/{pid}", headers=TESTER).json()
    assert detail["download_count"] == 1  # nur der eine erfolgreiche Abruf zählt


@requires_db
def test_legacy_bundle_respects_channel(db_app_client) -> None:
    pid, (v1, v2) = _make("Alt", [("1.0", True, PAST), ("2.0", True, FUTURE)])
    c = db_app_client

    # Alte Instanz ohne Kopf = stable: v1 ja, v2 (nur Tester) nein.
    r = c.get(f"/api/v1/profiles/{pid}/versions/{v1}/bundle")
    assert r.status_code == 200 and r.json()["tag"] == "1.0"
    assert c.get(f"/api/v1/profiles/{pid}/versions/{v2}/bundle").status_code == 404
    assert c.get(f"/api/v1/profiles/{pid}/versions/{v2}/bundle", headers=STABLE).status_code == 404
    r = c.get(f"/api/v1/profiles/{pid}/versions/{v2}/bundle", headers=TESTER)
    assert r.status_code == 200 and r.json()["tag"] == "2.0"


@requires_db
def test_match_rules_only_for_profiles_with_released_version(db_app_client) -> None:
    rules = {"sys_object_id_prefix": "1.3.6.1.4.1.41112"}
    _make("Stabil", [("1.0", True, PAST)], match_rules=rules)
    _make("Tester-only", [("1.0", True, FUTURE)], match_rules=rules)
    _make("Unsigniert", [("1.0", False, PAST)], match_rules=rules)
    c = db_app_client

    stable = {r["name"] for r in c.get("/api/v1/profiles/match-rules").json()}
    tester = {r["name"] for r in c.get("/api/v1/profiles/match-rules", headers=TESTER).json()}
    assert stable == {"Stabil"}
    assert tester == {"Stabil", "Tester-only"}


@requires_db
def test_profiles_by_mib_uses_the_released_version(db_app_client) -> None:
    from app.db import SessionLocal
    from app.services.profiles import profiles_by_mib

    pid, (v1, v2) = _make("SNMP", [("1.0", True, PAST), ("2.0", False, None)])
    with SessionLocal() as db:
        v1_row = db.get(CommunityProfileVersion, v1)
        v1_row.bundle_json = {"schema_version": 1, "checks": [], "mibs": [{"module": "OLD-MIB"}]}
        v2_row = db.get(CommunityProfileVersion, v2)
        v2_row.bundle_json = {"schema_version": 1, "checks": [], "mibs": [{"module": "NEW-MIB"}]}
        db.commit()
        users = profiles_by_mib(db)
    # Die unsignierte v2 zählt nicht — maßgeblich ist die ausgelieferte v1.
    assert set(users) == {"OLD-MIB"}


# --------------------------------------------------------------------------- #
# Entfernte Website/Q&A/Uploads/Anmeldung
# --------------------------------------------------------------------------- #
def test_root_redirects_to_github(client) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://github.com/vesana-org/community"


def test_robots_disallows_everything(client) -> None:
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /" in r.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/browse"),
        ("get", "/p/some-id"),
        ("get", "/p/some-id/download"),
        ("get", "/questions"),
        ("get", "/questions/x"),
        ("get", "/ask"),
        ("get", "/upload"),
        ("get", "/icons"),
        ("get", "/mibs"),
        ("get", "/mibs/IF-MIB"),
        ("get", "/account"),
        ("get", "/auth"),
        ("get", "/logout"),
        ("post", "/api/v1/auth/exchange"),
        ("post", "/api/v1/auth/refresh"),
        ("post", "/api/v1/profiles/upload"),
        ("post", "/api/v1/profiles"),
        ("post", "/api/v1/profiles/x/vote"),
        ("get", "/api/v1/profiles/x/comments"),
        ("post", "/api/v1/profiles/x/comments"),
        ("get", "/api/v1/questions"),
        ("post", "/api/v1/questions"),
        ("get", "/api/v1/notifications"),
        ("post", "/api/v1/mib-submissions"),
        ("get", "/api/v1/mib-submissions"),
        ("get", "/api/v1/admin/review-queue"),
        ("get", "/api/v1/admin/reports"),
        ("get", "/api/v1/admin/mib-submissions"),
        ("get", "/admin/review"),
        ("get", "/admin/moderation"),
        ("get", "/admin/questions"),
    ],
)
def test_removed_routes_are_gone(client, method, path) -> None:
    r = getattr(client, method)(path, follow_redirects=False)
    assert r.status_code in (404, 405), (path, r.status_code)
    # 405 nur, wenn derselbe Pfad noch eine LESE-Route hat (z. B. GET /api/v1/profiles).
    if r.status_code == 405:
        assert method == "post"
