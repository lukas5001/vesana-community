"""Comment tests — pure validation/helpers + DB-backed behaviour.

Pure tests always run. DB tests require ``DATABASE_URL_TEST`` (see conftest).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.interactions import CommentIn
from app.services.comments import author_display, within_edit_window
from tests.conftest import requires_db

# --- Pure: CommentIn validation --------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", "\n\t  "])
def test_commentin_rejects_blank_body(bad):
    with pytest.raises(ValidationError):
        CommentIn(body_md=bad)


def test_commentin_rejects_oversize_body():
    with pytest.raises(ValidationError):
        CommentIn(body_md="x" * 5001)


def test_commentin_accepts_normal_body():
    comment_in = CommentIn(body_md="Looks great!")
    assert comment_in.body_md == "Looks great!"
    assert comment_in.parent_id is None


# --- Pure: edit-window helper ----------------------------------------------


def test_within_edit_window_true_just_now():
    assert within_edit_window(datetime.now(UTC)) is True


def test_within_edit_window_false_after_25h():
    past = datetime.now(UTC) - timedelta(hours=25)
    assert within_edit_window(past) is False


def test_within_edit_window_naive_created_at_treated_as_utc():
    past_naive = (datetime.now(UTC) - timedelta(hours=25)).replace(tzinfo=None)
    assert within_edit_window(past_naive) is False


# --- Pure: author_display fallback -----------------------------------------


def test_author_display_uses_display_name_when_present():
    assert author_display("Alice", "uuid-1234abcd") == "Alice"


def test_author_display_falls_back_to_handle():
    assert author_display(None, "abcd1234efgh") == "@abcd1234"


def test_author_display_blank_name_falls_back():
    # Auto/blank names map to a clean @handle — never the ugly "instanz-…".
    assert author_display("   ", "abcd1234efgh") == "@abcd1234"
    assert author_display("instanz-abcd1234", "abcd1234efgh") == "@abcd1234"
    assert author_display("Real Name", "abcd1234efgh") == "Real Name"


# --- DB helpers -------------------------------------------------------------


def _bearer(client, make_login_jwt, sub, jti, display_name="Commenter"):
    token = make_login_jwt(sub=sub, display_name=display_name, jti=jti)
    resp = client.post("/api/v1/auth/exchange", json={"token": token})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['api_token']}"}


def _seed_profile(name="CommentProfile", tier="official", uploader=None):
    import app.db as db_mod
    from app.models.community_profile import CommunityProfile

    with db_mod.SessionLocal() as session:
        profile = CommunityProfile(
            name=name, tier=tier, approved=True, uploader_instance_uuid=uploader
        )
        session.add(profile)
        session.commit()
        return profile.id


def _backdate_comment(comment_id, hours):
    import app.db as db_mod
    from app.models.profile_comment import ProfileComment

    with db_mod.SessionLocal() as session:
        comment = session.get(ProfileComment, comment_id)
        comment.created_at = datetime.now(UTC) - timedelta(hours=hours)
        session.commit()


def _post_comment(client, profile_id, headers, body_md, parent_id=None):
    payload = {"body_md": body_md}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    return client.post(f"/api/v1/profiles/{profile_id}/comments", json=payload, headers=headers)


# --- DB: create / display / threading --------------------------------------


@requires_db
def test_create_comment_appears_with_author_and_zero_score(db_app_client, make_login_jwt):
    headers = _bearer(
        db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000001", "c-1", "Dana"
    )
    profile_id = _seed_profile()

    created = _post_comment(db_app_client, profile_id, headers, "First!")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["author_display"] == "Dana"
    assert body["vote_score"] == 0
    assert body["body_md"] == "First!"
    assert body["parent_id"] is None
    assert body["can_edit"] is True

    listing = db_app_client.get(f"/api/v1/profiles/{profile_id}/comments")
    assert listing.status_code == 200, listing.text
    threads = listing.json()
    assert len(threads) == 1
    assert threads[0]["comment"]["author_display"] == "Dana"


@requires_db
def test_reply_nests_under_parent(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000002", "c-2")
    profile_id = _seed_profile()
    top = _post_comment(db_app_client, profile_id, headers, "top").json()

    reply = _post_comment(db_app_client, profile_id, headers, "reply", parent_id=top["id"])
    assert reply.status_code == 201, reply.text
    assert reply.json()["parent_id"] == top["id"]

    threads = db_app_client.get(f"/api/v1/profiles/{profile_id}/comments").json()
    assert len(threads) == 1
    assert threads[0]["comment"]["id"] == top["id"]
    assert threads[0]["comment"]["reply_count"] == 1
    assert len(threads[0]["replies"]) == 1
    assert threads[0]["replies"][0]["body_md"] == "reply"


@requires_db
def test_reply_to_reply_is_rejected(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000003", "c-3")
    profile_id = _seed_profile()
    top = _post_comment(db_app_client, profile_id, headers, "top").json()
    reply = _post_comment(db_app_client, profile_id, headers, "reply", parent_id=top["id"]).json()

    deep = _post_comment(db_app_client, profile_id, headers, "too deep", parent_id=reply["id"])
    assert deep.status_code == 400, deep.text


# --- DB: edit window --------------------------------------------------------


@requires_db
def test_edit_within_window_ok(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000004", "c-4")
    profile_id = _seed_profile()
    comment = _post_comment(db_app_client, profile_id, headers, "v1").json()

    edited = db_app_client.put(
        f"/api/v1/comments/{comment['id']}", json={"body_md": "v2 edited"}, headers=headers
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["body_md"] == "v2 edited"


@requires_db
def test_edit_after_24h_is_forbidden(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000005", "c-5")
    profile_id = _seed_profile()
    comment = _post_comment(db_app_client, profile_id, headers, "old").json()
    _backdate_comment(comment["id"], hours=25)

    edited = db_app_client.put(
        f"/api/v1/comments/{comment['id']}", json={"body_md": "late edit"}, headers=headers
    )
    assert edited.status_code == 403, edited.text


@requires_db
def test_edit_other_users_comment_is_forbidden(db_app_client, make_login_jwt):
    owner = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000006", "c-6a")
    other = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000007", "c-6b")
    profile_id = _seed_profile()
    comment = _post_comment(db_app_client, profile_id, owner, "mine").json()

    hijack = db_app_client.put(
        f"/api/v1/comments/{comment['id']}", json={"body_md": "stolen"}, headers=other
    )
    assert hijack.status_code == 403, hijack.text


# --- DB: soft delete --------------------------------------------------------


@requires_db
def test_soft_delete_hides_body_but_keeps_replies(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000008", "c-8")
    profile_id = _seed_profile()
    top = _post_comment(db_app_client, profile_id, headers, "parent").json()
    _post_comment(db_app_client, profile_id, headers, "child stays", parent_id=top["id"])

    deleted = db_app_client.request("DELETE", f"/api/v1/comments/{top['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["body_md"] is None

    threads = db_app_client.get(f"/api/v1/profiles/{profile_id}/comments").json()
    assert len(threads) == 1
    assert threads[0]["comment"]["body_md"] is None
    assert len(threads[0]["replies"]) == 1
    assert threads[0]["replies"][0]["body_md"] == "child stays"


# --- DB: helpful ------------------------------------------------------------


@requires_db
def test_helpful_by_uploader_sets_flag_and_sorts_top(db_app_client, make_login_jwt):
    uploader_sub = "22222222-0000-0000-0000-000000000009"
    uploader = _bearer(db_app_client, make_login_jwt, uploader_sub, "c-9up")
    profile_id = _seed_profile(tier="community", uploader=uploader_sub)

    first = _post_comment(db_app_client, profile_id, uploader, "first").json()
    second = _post_comment(db_app_client, profile_id, uploader, "second").json()

    marked = db_app_client.post(
        f"/api/v1/comments/{second['id']}/helpful", json={"helpful": True}, headers=uploader
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["is_helpful"] is True

    threads = db_app_client.get(f"/api/v1/profiles/{profile_id}/comments").json()
    # Helpful comment sorts to the top regardless of creation order.
    assert threads[0]["comment"]["id"] == second["id"]
    assert threads[1]["comment"]["id"] == first["id"]


@requires_db
def test_helpful_by_random_instance_is_forbidden(db_app_client, make_login_jwt):
    uploader_sub = "22222222-0000-0000-0000-00000000000a"
    uploader = _bearer(db_app_client, make_login_jwt, uploader_sub, "c-10up")
    rando = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-00000000000b", "c-10r")
    profile_id = _seed_profile(tier="community", uploader=uploader_sub)
    comment = _post_comment(db_app_client, profile_id, uploader, "hi").json()

    resp = db_app_client.post(
        f"/api/v1/comments/{comment['id']}/helpful", json={"helpful": True}, headers=rando
    )
    assert resp.status_code == 403, resp.text


# --- DB: comment votes ------------------------------------------------------


@requires_db
def test_vote_on_comment_updates_cached_score(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-00000000000c", "c-11a")
    voter = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-00000000000d", "c-11b")
    profile_id = _seed_profile()
    comment = _post_comment(db_app_client, profile_id, author, "vote me").json()

    voted = db_app_client.post(
        f"/api/v1/comments/{comment['id']}/vote", json={"value": 1}, headers=voter
    )
    assert voted.status_code == 200, voted.text
    assert voted.json()["vote_score"] == 1

    threads = db_app_client.get(f"/api/v1/profiles/{profile_id}/comments").json()
    assert threads[0]["comment"]["vote_score"] == 1


# --- DB: report -------------------------------------------------------------


@requires_db
def test_report_creates_open_row(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-00000000000e", "c-12a")
    reporter = _bearer(
        db_app_client, make_login_jwt, "22222222-0000-0000-0000-00000000000f", "c-12b"
    )
    profile_id = _seed_profile()
    comment = _post_comment(db_app_client, profile_id, author, "report me").json()

    reported = db_app_client.post(
        f"/api/v1/comments/{comment['id']}/report", json={"reason": "spam"}, headers=reporter
    )
    assert reported.status_code == 200, reported.text
    assert reported.json() == {"status": "open"}

    from sqlalchemy import select

    import app.db as db_mod
    from app.models.moderation_report import ModerationReport

    with db_mod.SessionLocal() as session:
        rows = (
            session.execute(
                select(ModerationReport).where(ModerationReport.target_id == comment["id"])
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].status == "open"
    assert rows[0].reason == "spam"


@requires_db
def test_downvote_reason_not_in_comments_listing(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000010", "c-13a")
    voter = _bearer(db_app_client, make_login_jwt, "22222222-0000-0000-0000-000000000011", "c-13b")
    profile_id = _seed_profile()
    comment = _post_comment(db_app_client, profile_id, author, "body").json()
    db_app_client.post(
        f"/api/v1/comments/{comment['id']}/vote",
        json={"value": -1, "reason": "hidden-downvote-reason"},
        headers=voter,
    )

    listing = db_app_client.get(f"/api/v1/profiles/{profile_id}/comments")
    assert listing.status_code == 200, listing.text
    assert "hidden-downvote-reason" not in listing.text
