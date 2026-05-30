"""Voting tests — pure schema/validation + DB-backed behaviour.

Pure tests always run. DB tests require ``DATABASE_URL_TEST`` to point at a
reachable Postgres (see tests/conftest.py); they are skipped otherwise.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.interactions import VoteIn
from tests.conftest import requires_db

# --- Pure: VoteIn validation -----------------------------------------------


@pytest.mark.parametrize("bad", [0, 2, -2, 5, -3])
def test_votein_rejects_non_plus_minus_one(bad):
    with pytest.raises(ValidationError):
        VoteIn(value=bad)


@pytest.mark.parametrize("good", [1, -1])
def test_votein_accepts_plus_minus_one(good):
    vote = VoteIn(value=good)
    assert vote.value == good


def test_votein_reason_optional_defaults_none():
    assert VoteIn(value=1).reason is None


# --- DB helpers -------------------------------------------------------------


def _bearer(client, make_login_jwt, *, sub, display_name="Voter", jti):
    token = make_login_jwt(sub=sub, display_name=display_name, jti=jti)
    resp = client.post("/api/v1/auth/exchange", json={"token": token})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['api_token']}"}


def _seed_profile(name="VoteProfile", tier="official"):
    import app.db as db_mod
    from app.models.community_profile import CommunityProfile

    with db_mod.SessionLocal() as session:
        profile = CommunityProfile(name=name, tier=tier, approved=True)
        session.add(profile)
        session.commit()
        return profile.id


# --- DB: profile voting -----------------------------------------------------


@requires_db
def test_vote_on_profile_updates_score(db_app_client, make_login_jwt):
    headers = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000001", jti="v-1"
    )
    profile_id = _seed_profile()

    resp = db_app_client.post(
        f"/api/v1/profiles/{profile_id}/vote", json={"value": 1}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_type"] == "profile"
    assert body["target_id"] == profile_id
    assert body["value"] == 1
    assert body["vote_score"] == 1


@requires_db
def test_revote_opposite_changes_score_no_duplicate(db_app_client, make_login_jwt):
    headers = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000002", jti="v-2"
    )
    profile_id = _seed_profile()

    up = db_app_client.post(
        f"/api/v1/profiles/{profile_id}/vote", json={"value": 1}, headers=headers
    )
    assert up.json()["vote_score"] == 1

    down = db_app_client.post(
        f"/api/v1/profiles/{profile_id}/vote", json={"value": -1}, headers=headers
    )
    assert down.status_code == 200, down.text
    assert down.json()["vote_score"] == -1

    # Exactly one row in votes for this instance/target (upsert, no dup).
    from sqlalchemy import func, select

    import app.db as db_mod
    from app.models.vote import Vote

    with db_mod.SessionLocal() as session:
        count = session.execute(
            select(func.count()).select_from(Vote).where(Vote.target_id == profile_id)
        ).scalar_one()
    assert count == 1


@requires_db
def test_remove_vote_resets_score_to_zero(db_app_client, make_login_jwt):
    headers = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000003", jti="v-3"
    )
    profile_id = _seed_profile()

    db_app_client.post(f"/api/v1/profiles/{profile_id}/vote", json={"value": 1}, headers=headers)
    removed = db_app_client.request(
        "DELETE", f"/api/v1/profiles/{profile_id}/vote", headers=headers
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["vote_score"] == 0


@requires_db
def test_two_instances_sum_into_score(db_app_client, make_login_jwt):
    h1 = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000004", jti="v-4a"
    )
    h2 = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000005", jti="v-4b"
    )
    profile_id = _seed_profile()

    db_app_client.post(f"/api/v1/profiles/{profile_id}/vote", json={"value": 1}, headers=h1)
    second = db_app_client.post(
        f"/api/v1/profiles/{profile_id}/vote", json={"value": 1}, headers=h2
    )
    assert second.json()["vote_score"] == 2


@requires_db
def test_vote_requires_auth(db_app_client):
    profile_id = _seed_profile()
    resp = db_app_client.post(f"/api/v1/profiles/{profile_id}/vote", json={"value": 1})
    assert resp.status_code == 401, resp.text


@requires_db
def test_vote_on_missing_profile_is_404(db_app_client, make_login_jwt):
    headers = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000006", jti="v-6"
    )
    resp = db_app_client.post(
        "/api/v1/profiles/does-not-exist/vote", json={"value": 1}, headers=headers
    )
    assert resp.status_code == 404, resp.text


@requires_db
def test_vote_value_two_is_rejected(db_app_client, make_login_jwt):
    headers = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000007", jti="v-7"
    )
    profile_id = _seed_profile()
    resp = db_app_client.post(
        f"/api/v1/profiles/{profile_id}/vote", json={"value": 2}, headers=headers
    )
    assert resp.status_code == 422, resp.text


@requires_db
def test_downvote_reason_not_in_public_profile_view(db_app_client, make_login_jwt):
    headers = _bearer(
        db_app_client, make_login_jwt, sub="11111111-aaaa-0000-0000-000000000008", jti="v-8"
    )
    profile_id = _seed_profile()
    db_app_client.post(
        f"/api/v1/profiles/{profile_id}/vote",
        json={"value": -1, "reason": "secret-private-reason"},
        headers=headers,
    )
    # The public profile detail must not leak the downvote reason anywhere.
    detail = db_app_client.get(f"/api/v1/profiles/{profile_id}")
    assert detail.status_code == 200, detail.text
    assert "secret-private-reason" not in detail.text
    page = db_app_client.get(f"/p/{profile_id}")
    assert page.status_code == 200
    assert "secret-private-reason" not in page.text
