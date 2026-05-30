"""Q&A portal tests — pure validation/helpers + DB-backed behaviour.

Pure tests always run. DB tests require ``DATABASE_URL_TEST`` (see conftest).
One test asserts exactly one invariant; assertions are on BODY values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.qa import AnswerIn, QuestionIn
from app.services.comments import author_display
from tests.conftest import requires_db

ADMIN_HEADER = {
    # base64("admin:test-admin-pass")
    "X-Admin-Authorization": "Basic YWRtaW46dGVzdC1hZG1pbi1wYXNz",
}

# --- Pure: QuestionIn / AnswerIn validation --------------------------------


def test_questionin_rejects_short_title():
    with pytest.raises(ValidationError):
        QuestionIn(title_text="hi", body_md="a real body")


def test_questionin_rejects_blank_body():
    with pytest.raises(ValidationError):
        QuestionIn(title_text="A valid title", body_md="   ")


def test_questionin_accepts_normal():
    q = QuestionIn(title_text="How do I monitor SNMP?", body_md="Steps please")
    assert q.title_text == "How do I monitor SNMP?"
    assert q.tags == []
    assert q.profile_id is None


def test_questionin_rejects_more_than_eight_tags():
    with pytest.raises(ValidationError):
        QuestionIn(title_text="A valid title", body_md="body", tags=[f"t{i}" for i in range(9)])


def test_questionin_accepts_eight_tags():
    q = QuestionIn(title_text="A valid title", body_md="body", tags=[f"t{i}" for i in range(8)])
    assert len(q.tags) == 8


def test_answerin_rejects_blank_body():
    with pytest.raises(ValidationError):
        AnswerIn(body_md="   ")


def test_answerin_accepts_normal_body():
    assert AnswerIn(body_md="Here is how").body_md == "Here is how"


# --- Pure: author_display reuse --------------------------------------------


def test_author_display_fallback_for_qa():
    assert author_display(None, "abcd1234efgh") == "instanz-abcd1234"


# --- DB helpers -------------------------------------------------------------


def _bearer(client, make_login_jwt, sub, jti, display_name="Asker"):
    token = make_login_jwt(sub=sub, display_name=display_name, jti=jti)
    resp = client.post("/api/v1/auth/exchange", json={"token": token})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['api_token']}"}


def _seed_profile(name="QAProfile", tier="official"):
    import app.db as db_mod
    from app.models.community_profile import CommunityProfile

    with db_mod.SessionLocal() as session:
        profile = CommunityProfile(name=name, tier=tier, approved=True)
        session.add(profile)
        session.commit()
        return profile.id


def _backdate_question(question_id, hours):
    import app.db as db_mod
    from app.models.question import Question

    with db_mod.SessionLocal() as session:
        question = session.get(Question, question_id)
        question.created_at = datetime.now(UTC) - timedelta(hours=hours)
        session.commit()


def _post_question(
    client,
    headers,
    title="How do I configure agents?",
    body="Body here",
    tags=None,
    profile_id=None,
    extra_headers=None,
):
    payload = {"title_text": title, "body_md": body}
    if tags is not None:
        payload["tags"] = tags
    if profile_id is not None:
        payload["profile_id"] = profile_id
    hdrs = dict(headers)
    if extra_headers:
        hdrs.update(extra_headers)
    return client.post("/api/v1/questions", json=payload, headers=hdrs)


def _post_answer(client, question_id, headers, body="An answer", extra_headers=None):
    hdrs = dict(headers)
    if extra_headers:
        hdrs.update(extra_headers)
    return client.post(
        f"/api/v1/questions/{question_id}/answers", json={"body_md": body}, headers=hdrs
    )


# --- DB: question create + list --------------------------------------------


@requires_db
def test_create_question_appears_in_list(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000001", "q-1")
    created = _post_question(db_app_client, headers, title="Unique title alpha")
    assert created.status_code == 201, created.text
    assert created.json()["title_text"] == "Unique title alpha"
    assert created.json()["answer_count"] == 0

    listing = db_app_client.get("/api/v1/questions")
    assert listing.status_code == 200, listing.text
    titles = [q["title_text"] for q in listing.json()]
    assert "Unique title alpha" in titles


# --- DB: answer bumps count + nests ----------------------------------------


@requires_db
def test_answer_bumps_count_and_nests(db_app_client, make_login_jwt):
    asker = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000002", "q-2a")
    answerer = _bearer(
        db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000003", "q-2b"
    )
    question = _post_question(db_app_client, asker).json()

    ans = _post_answer(db_app_client, question["id"], answerer, body="Try profiles: [ai]")
    assert ans.status_code == 201, ans.text
    assert ans.json()["body_md"] == "Try profiles: [ai]"

    detail = db_app_client.get(f"/api/v1/questions/{question['id']}").json()
    assert detail["answer_count"] == 1
    assert len(detail["answers"]) == 1
    assert detail["answers"][0]["body_md"] == "Try profiles: [ai]"


# --- DB: voting on question + answer ---------------------------------------


@requires_db
def test_vote_question_updates_cache_no_dup(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000004", "q-3a")
    voter = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000005", "q-3b")
    question = _post_question(db_app_client, author).json()

    first = db_app_client.post(
        f"/api/v1/questions/{question['id']}/vote", json={"value": 1}, headers=voter
    )
    assert first.json()["vote_score"] == 1
    again = db_app_client.post(
        f"/api/v1/questions/{question['id']}/vote", json={"value": 1}, headers=voter
    )
    assert again.json()["vote_score"] == 1  # upsert, no double-count

    detail = db_app_client.get(f"/api/v1/questions/{question['id']}").json()
    assert detail["vote_score"] == 1


@requires_db
def test_vote_answer_updates_cache(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000006", "q-4a")
    voter = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000007", "q-4b")
    question = _post_question(db_app_client, author).json()
    answer = _post_answer(db_app_client, question["id"], author).json()

    voted = db_app_client.post(
        f"/api/v1/answers/{answer['id']}/vote", json={"value": 1}, headers=voter
    )
    assert voted.status_code == 200, voted.text
    assert voted.json()["vote_score"] == 1

    detail = db_app_client.get(f"/api/v1/questions/{question['id']}").json()
    assert detail["answers"][0]["vote_score"] == 1


# --- DB: accept answer ------------------------------------------------------


@requires_db
def test_accept_by_author_sorts_first_and_flips_only_one(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000008", "q-5a")
    other = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000009", "q-5b")
    question = _post_question(db_app_client, author).json()
    first = _post_answer(db_app_client, question["id"], other, body="first answer").json()
    second = _post_answer(db_app_client, question["id"], other, body="second answer").json()

    a1 = db_app_client.post(f"/api/v1/answers/{first['id']}/accept", headers=author)
    assert a1.status_code == 200, a1.text
    assert a1.json()["is_accepted"] is True

    # Accepting the second must flip the first false (only one accepted).
    a2 = db_app_client.post(f"/api/v1/answers/{second['id']}/accept", headers=author)
    assert a2.status_code == 200, a2.text
    assert a2.json()["is_accepted"] is True

    detail = db_app_client.get(f"/api/v1/questions/{question['id']}").json()
    accepted = [a for a in detail["answers"] if a["is_accepted"]]
    assert len(accepted) == 1
    assert accepted[0]["id"] == second["id"]
    # Accepted answer sorts first.
    assert detail["answers"][0]["id"] == second["id"]


@requires_db
def test_accept_by_non_author_is_forbidden(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-00000000000a", "q-6a")
    other = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-00000000000b", "q-6b")
    question = _post_question(db_app_client, author).json()
    answer = _post_answer(db_app_client, question["id"], other).json()

    resp = db_app_client.post(f"/api/v1/answers/{answer['id']}/accept", headers=other)
    assert resp.status_code == 403, resp.text


# --- DB: closed question ----------------------------------------------------


@requires_db
def test_answer_on_closed_question_is_conflict(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-00000000000c", "q-7a")
    other = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-00000000000d", "q-7b")
    question = _post_question(db_app_client, author).json()

    closed = db_app_client.post(
        f"/api/v1/questions/{question['id']}/close-duplicate",
        json={"reason": "dup"},
        headers={**author, **ADMIN_HEADER},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["is_closed"] is True

    resp = _post_answer(db_app_client, question["id"], other)
    assert resp.status_code == 409, resp.text


# --- DB: close as duplicate (admin only) ------------------------------------


@requires_db
def test_close_duplicate_admin_ok(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-00000000000e", "q-8a")
    dup_target = _post_question(db_app_client, author, title="Canonical question").json()
    question = _post_question(db_app_client, author, title="Duplicate question").json()

    resp = db_app_client.post(
        f"/api/v1/questions/{question['id']}/close-duplicate",
        json={"duplicate_of_id": dup_target["id"], "reason": "same thing"},
        headers={**author, **ADMIN_HEADER},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_closed"] is True
    assert body["duplicate_of_id"] == dup_target["id"]
    assert body["closed_reason"] == "same thing"


@requires_db
def test_close_duplicate_non_admin_is_forbidden(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-00000000000f", "q-9")
    question = _post_question(db_app_client, author).json()

    resp = db_app_client.post(
        f"/api/v1/questions/{question['id']}/close-duplicate",
        json={"reason": "dup"},
        headers=author,
    )
    assert resp.status_code == 403, resp.text


# --- DB: similar search -----------------------------------------------------


@requires_db
def test_similar_matches_by_ilike(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000010", "q-10")
    _post_question(db_app_client, author, title="SNMP monitoring on switches")
    _post_question(db_app_client, author, title="Totally unrelated topic")

    resp = db_app_client.get("/api/v1/questions/similar", params={"title": "snmp"})
    assert resp.status_code == 200, resp.text
    titles = [q["title_text"] for q in resp.json()]
    assert "SNMP monitoring on switches" in titles
    assert "Totally unrelated topic" not in titles


# --- DB: is_vesana_team stamping --------------------------------------------


@requires_db
def test_is_vesana_team_stamped_with_admin_header(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000011", "q-11")
    created = _post_question(db_app_client, author, extra_headers=ADMIN_HEADER)
    assert created.status_code == 201, created.text
    assert created.json()["is_vesana_team"] is True


@requires_db
def test_is_vesana_team_not_stamped_without_admin_header(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000012", "q-12")
    created = _post_question(db_app_client, author)
    assert created.status_code == 201, created.text
    assert created.json()["is_vesana_team"] is False


# --- DB: edit window --------------------------------------------------------


@requires_db
def test_edit_own_question_within_window_ok(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000013", "q-13")
    question = _post_question(db_app_client, author).json()

    edited = db_app_client.put(
        f"/api/v1/questions/{question['id']}",
        json={"title_text": "Edited title text", "body_md": "new body", "tags": []},
        headers=author,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title_text"] == "Edited title text"


@requires_db
def test_edit_own_question_after_24h_is_forbidden(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000014", "q-14")
    question = _post_question(db_app_client, author).json()
    _backdate_question(question["id"], hours=25)

    edited = db_app_client.put(
        f"/api/v1/questions/{question['id']}",
        json={"title_text": "Late edit title", "body_md": "late", "tags": []},
        headers=author,
    )
    assert edited.status_code == 403, edited.text


@requires_db
def test_edit_other_users_question_is_forbidden(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000015", "q-15a")
    other = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000016", "q-15b")
    question = _post_question(db_app_client, author).json()

    hijack = db_app_client.put(
        f"/api/v1/questions/{question['id']}",
        json={"title_text": "Stolen title", "body_md": "stolen", "tags": []},
        headers=other,
    )
    assert hijack.status_code == 403, hijack.text


# --- DB: profile-linked question --------------------------------------------


@requires_db
def test_profile_linked_question_shows_on_profile_page(db_app_client, make_login_jwt):
    author = _bearer(db_app_client, make_login_jwt, "33330000-0000-0000-0000-000000000017", "q-16")
    profile_id = _seed_profile()
    question = _post_question(
        db_app_client, author, title="Question about this profile", profile_id=profile_id
    ).json()
    assert question["profile_id"] == profile_id

    page = db_app_client.get(f"/p/{profile_id}")
    assert page.status_code == 200
    assert "Question about this profile" in page.text
