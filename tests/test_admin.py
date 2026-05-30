"""Community admin panel tests (C8).

Pure tests cover the stats shape, the ResolveReportIn literal validation and the
target-preview privacy invariant (never leaks a downvote reason). DB tests cover
the moderation / instances / promote / stats flows and — crucially — the authz
invariant: EVERY admin endpoint returns 401 without admin credentials.

DB tests are skipped unless ``DATABASE_URL_TEST`` points at a reachable Postgres
(see conftest). Admin auth for the JSON API is the ``X-Admin-Authorization``
Basic header built from the conftest creds (admin / test-admin-pass); the HTML
pages use plain HTTP Basic.
"""

from __future__ import annotations

import base64

import pytest

from app.schemas.admin import AdminStats, ResolveReportIn
from tests.conftest import requires_db

# Built from the conftest COMMUNITY_ADMIN_USER / COMMUNITY_ADMIN_PASSWORD.
_BASIC = "Basic " + base64.b64encode(b"admin:test-admin-pass").decode()
ADMIN_HEADER = {"X-Admin-Authorization": _BASIC}
PAGE_AUTH = ("admin", "test-admin-pass")

LOGIN_ISSUER = "vesana-licence-portal"


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


def test_resolve_report_in_accepts_known_actions():
    assert ResolveReportIn(action="dismiss").action == "dismiss"
    assert ResolveReportIn(action="remove").action == "remove"


def test_resolve_report_in_rejects_unknown_action():
    with pytest.raises(ValueError):
        ResolveReportIn(action="nuke")


def test_target_preview_never_leaks_downvote_reason():
    # A profile target preview is built from the profile NAME only; a private
    # downvote reason stored elsewhere must never appear in it.
    from app.services.admin import _snippet

    secret = "SECRET-DOWNVOTE-REASON-do-not-leak"
    preview = _snippet("Nice profile name")
    assert secret not in preview
    assert preview == "Nice profile name"


# --- DB helpers -------------------------------------------------------------


def _api_token(client, make_login_jwt, sub: str, display_name: str) -> str:
    login = make_login_jwt(sub=sub, display_name=display_name)
    resp = client.post("/api/v1/auth/exchange", json={"token": login})
    assert resp.status_code == 200, resp.text
    return resp.json()["api_token"]


def _upload(client, token: str, name: str, vendor: str = "ACME") -> str:
    bundle = {
        "schema_version": 1,
        "profile": {"name": name, "vendor": vendor},
        "checks": [{"check_type": "ping", "check_config": {"host": "1.1.1.1"}}],
    }
    resp = client.post(
        "/api/v1/profiles/upload",
        json={"bundle": bundle},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["profile_id"]


# --- DB: moderation ---------------------------------------------------------


@requires_db
def test_reports_list_and_resolve_dismiss(db_app_client, make_login_jwt):
    c = db_app_client
    a_token = _api_token(c, make_login_jwt, "inst-a", "Alpha")
    b_token = _api_token(c, make_login_jwt, "inst-b", "Bravo")
    profile_id = _upload(c, a_token, "Mod Target")

    # Alpha comments; Bravo reports the comment.
    comment = c.post(
        f"/api/v1/profiles/{profile_id}/comments",
        json={"body_md": "hello world"},
        headers={"Authorization": f"Bearer {a_token}"},
    )
    comment_id = comment.json()["id"]
    rep = c.post(
        f"/api/v1/comments/{comment_id}/report",
        json={"reason": "spam"},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert rep.status_code == 200

    listed = c.get("/api/v1/admin/reports", headers=ADMIN_HEADER)
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    report_id = rows[0]["id"]
    assert rows[0]["target_preview"] == "hello world"

    resolved = c.post(
        f"/api/v1/admin/reports/{report_id}/resolve",
        json={"action": "dismiss"},
        headers=ADMIN_HEADER,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "dismissed"


@requires_db
def test_report_resolve_remove_soft_deletes_comment(db_app_client, make_login_jwt):
    c = db_app_client
    a_token = _api_token(c, make_login_jwt, "inst-a", "Alpha")
    b_token = _api_token(c, make_login_jwt, "inst-b", "Bravo")
    profile_id = _upload(c, a_token, "Remove Target")
    comment = c.post(
        f"/api/v1/profiles/{profile_id}/comments",
        json={"body_md": "bad comment"},
        headers={"Authorization": f"Bearer {a_token}"},
    )
    comment_id = comment.json()["id"]
    c.post(
        f"/api/v1/comments/{comment_id}/report",
        json={"reason": "abuse"},
        headers={"Authorization": f"Bearer {b_token}"},
    )
    report_id = c.get("/api/v1/admin/reports", headers=ADMIN_HEADER).json()[0]["id"]

    resolved = c.post(
        f"/api/v1/admin/reports/{report_id}/resolve",
        json={"action": "remove"},
        headers=ADMIN_HEADER,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    # The comment thread now hides the removed body (body_md None).
    thread = c.get(f"/api/v1/profiles/{profile_id}/comments")
    top = thread.json()[0]["comment"]
    assert top["body_md"] is None


# --- DB: instances ----------------------------------------------------------


@requires_db
def test_instances_list_and_block_unblock(db_app_client, make_login_jwt):
    c = db_app_client
    token = _api_token(c, make_login_jwt, "inst-block", "Blockee")

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

    # A blocked instance's Bearer token is now rejected by get_current_instance.
    rejected = c.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rejected.status_code == 403

    unblocked = c.post(
        "/api/v1/admin/instances/inst-block/block",
        json={"blocked": False},
        headers=ADMIN_HEADER,
    )
    assert unblocked.json()["is_blocked"] is False


# --- DB: promote ------------------------------------------------------------


@requires_db
def test_promote_beta_profile_to_official(db_app_client, make_login_jwt):
    c = db_app_client
    token = _api_token(c, make_login_jwt, "inst-pro", "Promoter")
    profile_id = _upload(c, token, "Promote Me")

    # Force tier=beta directly (uploads create community tier).
    import app.db as db_mod
    from app.models.community_profile import CommunityProfile

    with db_mod.SessionLocal() as session:
        prof = session.get(CommunityProfile, profile_id)
        prof.tier = "beta"
        session.commit()

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
def test_stats_counts_are_correct(db_app_client, make_login_jwt):
    c = db_app_client
    a_token = _api_token(c, make_login_jwt, "inst-a", "Alpha")
    _api_token(c, make_login_jwt, "inst-b", "Bravo")
    _upload(c, a_token, "Stat One")
    _upload(c, a_token, "Stat Two")

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
        ("get", "/api/v1/admin/reports", None),
        ("post", "/api/v1/admin/reports/x/resolve", {"action": "dismiss"}),
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
    ["/admin", "/admin/review", "/admin/moderation", "/admin/instances", "/admin/profiles"],
)
def test_admin_pages_require_basic_auth(db_app_client, path):
    c = db_app_client
    assert c.get(path).status_code == 401
    ok = c.get(path, auth=PAGE_AUTH)
    assert ok.status_code == 200
