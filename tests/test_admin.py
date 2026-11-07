"""Community admin panel tests (C8).

Pure tests cover the stats shape and the snippet helper. DB tests cover the
instances / promote / stats flows and — crucially — the authz invariant: EVERY
admin endpoint returns 401 without admin credentials. (Moderation/Meldungen
sind mit dem Umbau zur Vesana-Bibliothek entfallen.)

DB tests are skipped unless ``DATABASE_URL_TEST`` points at a reachable Postgres
(see conftest). Admin auth for the JSON API is the ``X-Admin-Authorization``
Basic header built from the conftest creds (admin / test-admin-pass); die HTML-
Seiten hängen seit v0.9.0 an einer Session-Anmeldung und leiten ohne sie auf
``/admin/login`` um.
"""

from __future__ import annotations

import base64

import pytest

from app.schemas.admin import AdminStats
from tests.conftest import insert_instance, insert_profile, requires_db

# Built from the conftest COMMUNITY_ADMIN_USER / COMMUNITY_ADMIN_PASSWORD.
_BASIC = "Basic " + base64.b64encode(b"admin:test-admin-pass").decode()
ADMIN_HEADER = {"X-Admin-Authorization": _BASIC}


# --- Pure -------------------------------------------------------------------


def test_admin_stats_shape_has_all_fields():
    stats = AdminStats(
        instances_total=3,
        instances_blocked=1,
        profiles_total=5,
        profiles_by_tier={"official": 1, "beta": 2, "community": 2},
        profiles_pending=1,
        downloads_total=10,
        imports_total=4,
        votes_total=7,
        questions_total=2,
        questions_open=1,
        reports_open=1,
        events_total=9,
    )
    dumped = stats.model_dump()
    assert set(dumped) == {
        "instances_total",
        "instances_blocked",
        "profiles_total",
        "profiles_by_tier",
        "profiles_pending",
        "downloads_total",
        "imports_total",
        "votes_total",
        "questions_total",
        "questions_open",
        "reports_open",
        "events_total",
    }


def test_target_preview_never_leaks_downvote_reason():
    # A profile target preview is built from the profile NAME only; a private
    # downvote reason stored elsewhere must never appear in it.
    from app.services.admin import _snippet

    secret = "SECRET-DOWNVOTE-REASON-do-not-leak"
    preview = _snippet("Nice profile name")
    assert secret not in preview
    assert preview == "Nice profile name"


# --- DB: instances ----------------------------------------------------------


@requires_db
def test_instances_list_and_block_unblock(db_app_client):
    c = db_app_client
    insert_instance("inst-block", "Blockee")

    listed = c.get("/api/v1/admin/instances", headers=ADMIN_HEADER)
    assert listed.status_code == 200
    uuids = {row["uuid"] for row in listed.json()}
    assert "inst-block" in uuids

    blocked = c.post(
        "/api/v1/admin/instances/inst-block/block",
        json={"blocked": True},
        headers=ADMIN_HEADER,
    )
    assert blocked.status_code == 200
    assert blocked.json()["is_blocked"] is True

    unblocked = c.post(
        "/api/v1/admin/instances/inst-block/block",
        json={"blocked": False},
        headers=ADMIN_HEADER,
    )
    assert unblocked.json()["is_blocked"] is False


# --- DB: promote ------------------------------------------------------------


@requires_db
def test_promote_beta_profile_to_official(db_app_client):
    c = db_app_client
    profile_id = insert_profile("Promote Me", tier="beta")

    import app.db as db_mod
    from app.models.community_profile import CommunityProfile

    promoted = c.post(
        f"/api/v1/admin/profiles/{profile_id}/promote",
        headers=ADMIN_HEADER,
    )
    assert promoted.status_code == 200
    assert promoted.json()["tier"] == "official"

    with db_mod.SessionLocal() as session:
        prof = session.get(CommunityProfile, profile_id)
        assert prof.tier == "official"
        assert prof.approved is True
        assert prof.review_status == "approved"


@requires_db
def test_promote_unknown_profile_404(db_app_client):
    c = db_app_client
    resp = c.post("/api/v1/admin/profiles/does-not-exist/promote", headers=ADMIN_HEADER)
    assert resp.status_code == 404


# --- DB: stats --------------------------------------------------------------


@requires_db
def test_stats_counts_are_correct(db_app_client):
    c = db_app_client
    insert_instance("inst-a", "Alpha")
    insert_instance("inst-b", "Bravo")
    insert_profile("Stat One", uploader="inst-a")
    insert_profile("Stat Two", uploader="inst-a")

    stats = c.get("/api/v1/admin/stats", headers=ADMIN_HEADER)
    assert stats.status_code == 200
    body = stats.json()
    assert body["instances_total"] == 2
    assert body["profiles_total"] == 2
    assert body["profiles_by_tier"]["community"] == 2
    assert body["profiles_pending"] == 2


# --- DB: authz invariant — 401 without admin --------------------------------


@requires_db
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/api/v1/admin/mibs", None),
        ("get", "/api/v1/admin/instances", None),
        ("post", "/api/v1/admin/instances/x/block", {"blocked": True}),
        ("post", "/api/v1/admin/profiles/x/promote", None),
        ("get", "/api/v1/admin/stats", None),
    ],
)
def test_admin_api_requires_admin_header(db_app_client, method, path, json_body):
    c = db_app_client
    resp = c.request(method.upper(), path, json=json_body)
    assert resp.status_code == 401


@requires_db
@pytest.mark.parametrize(
    "path",
    ["/admin", "/admin/instances", "/admin/profiles", "/admin/mibs", "/admin/audit"],
)
def test_admin_pages_require_a_session_login(db_app_client, path):
    """Admin-SEITEN hängen seit v0.9.0 an einer Session, nicht mehr an HTTP-Basic.

    Der Test stand noch auf 401 (Basic-Challenge) und war seither rot: ohne
    Session antwortet die Seite mit 303 auf `/admin/login`. Wichtig ist die
    Weiterleitung selbst — ein 200 mit Admin-Inhalt wäre das Loch.
    """
    c = db_app_client
    resp = c.get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/login")
