"""Tests for community-side notifications (C6a): events + poll + mark-read.

Pure tests always run. DB tests require ``DATABASE_URL_TEST`` (see conftest).
One test asserts exactly one invariant; assertions are on BODY values. All
events are emitted by the real services in the same transaction as the action.
"""

from __future__ import annotations

import uuid

from app.schemas.notifications import MarkReadIn
from tests.conftest import requires_db

# base64("admin:test-admin-pass") — matches the conftest admin env.
ADMIN_HEADER = {"X-Admin-Authorization": "Basic YWRtaW46dGVzdC1hZG1pbi1wYXNz"}


# --------------------------------------------------------------------------- #
# Pure (no DB)                                                                 #
# --------------------------------------------------------------------------- #


class _RecordingSession:
    """Minimal stand-in that records what enqueue would add to a session."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def test_enqueue_skips_self_notification():
    from app.services.notifications import enqueue

    db = _RecordingSession()
    enqueue(db, recipient_uuid="same", actor_uuid="same", type="profile_comment", payload={})
    assert db.added == []


def test_enqueue_skips_when_recipient_falsy():
    from app.services.notifications import enqueue

    db = _RecordingSession()
    enqueue(db, recipient_uuid=None, actor_uuid="someone", type="profile_approved", payload={})
    assert db.added == []


def test_enqueue_inserts_for_distinct_recipient():
    from app.services.notifications import enqueue

    db = _RecordingSession()
    enqueue(
        db,
        recipient_uuid="recipient",
        actor_uuid="actor",
        type="qa_answer",
        payload={"question_id": "q1"},
    )
    assert len(db.added) == 1
    event = db.added[0]
    assert event.instance_uuid == "recipient"
    assert event.type == "qa_answer"
    assert event.payload_json == {"question_id": "q1"}


def test_mark_read_in_defaults():
    m = MarkReadIn()
    assert m.ids is None
    assert m.all is False


def test_mark_read_in_with_ids():
    m = MarkReadIn(ids=["a", "b"])
    assert m.ids == ["a", "b"]
    assert m.all is False


# --------------------------------------------------------------------------- #
# DB helpers                                                                   #
# --------------------------------------------------------------------------- #


def _bearer(client, make_login_jwt, instance_uuid: str) -> dict:
    token = make_login_jwt(sub=instance_uuid, display_name=f"Inst {instance_uuid[:6]}")
    resp = client.post("/api/v1/auth/exchange", json={"token": token})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['api_token']}"}


def _bundle(name: str = "Nginx Basic", vendor: str | None = "Nginx") -> dict:
    return {
        "schema_version": 1,
        "profile": {
            "name": name,
            "description": "Basic monitoring",
            "vendor": vendor,
            "category": "web",
        },
        "checks": [{"name": "HTTP up", "check_type": "http", "check_config": {"x": "y"}}],
    }


def _upload(client, headers, name="Nginx Basic", version_tag="v1") -> str:
    resp = client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _bundle(name=name), "version_tag": version_tag},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["profile_id"]


def _comment(client, headers, profile_id, body="nice", parent_id=None):
    payload = {"body_md": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    return client.post(f"/api/v1/profiles/{profile_id}/comments", json=payload, headers=headers)


def _ask(client, headers, title="How do I monitor SNMP?", body="Steps please"):
    return client.post(
        "/api/v1/questions", json={"title_text": title, "body_md": body}, headers=headers
    )


def _answer(client, headers, question_id, body="Because."):
    return client.post(
        f"/api/v1/questions/{question_id}/answers", json={"body_md": body}, headers=headers
    )


def _list(client, headers, **params):
    resp = client.get("/api/v1/notifications", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# DB-backed                                                                    #
# --------------------------------------------------------------------------- #


@requires_db
def test_comment_on_profile_notifies_owner(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    profile_id = _upload(client, headers_a)

    resp = _comment(client, headers_b, profile_id, body="great profile")
    assert resp.status_code == 201, resp.text

    a_feed = _list(client, headers_a)
    assert len(a_feed["items"]) == 1
    assert a_feed["items"][0]["type"] == "profile_comment"
    assert a_feed["items"][0]["payload"]["profile_id"] == profile_id


@requires_db
def test_comment_on_profile_does_not_notify_commenter(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    profile_id = _upload(client, headers_a)
    _comment(client, headers_b, profile_id)

    b_feed = _list(client, headers_b)
    assert b_feed["items"] == []


@requires_db
def test_comment_on_own_profile_no_self_notification(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    profile_id = _upload(client, headers_a)

    resp = _comment(client, headers_a, profile_id, body="self comment")
    assert resp.status_code == 201, resp.text

    a_feed = _list(client, headers_a)
    assert a_feed["items"] == []
    assert a_feed["unread_count"] == 0


@requires_db
def test_reply_notifies_parent_comment_author(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    c = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    headers_c = _bearer(client, make_login_jwt, c)
    profile_id = _upload(client, headers_a)

    parent = _comment(client, headers_b, profile_id, body="B comment")
    assert parent.status_code == 201, parent.text
    parent_id = parent.json()["id"]

    reply = _comment(client, headers_c, profile_id, body="C reply", parent_id=parent_id)
    assert reply.status_code == 201, reply.text

    b_feed = _list(client, headers_b)
    reply_events = [i for i in b_feed["items"] if i["type"] == "comment_reply"]
    assert len(reply_events) == 1
    assert reply_events[0]["payload"]["comment_id"] == reply.json()["id"]


@requires_db
def test_answer_notifies_question_author(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    question_id = _ask(client, headers_a).json()["id"]

    ans = _answer(client, headers_b, question_id)
    assert ans.status_code == 201, ans.text

    a_feed = _list(client, headers_a)
    assert len(a_feed["items"]) == 1
    assert a_feed["items"][0]["type"] == "qa_answer"
    assert a_feed["items"][0]["payload"]["question_id"] == question_id


@requires_db
def test_accept_answer_notifies_answerer(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    question_id = _ask(client, headers_a).json()["id"]
    answer_id = _answer(client, headers_b, question_id).json()["id"]

    acc = client.post(f"/api/v1/answers/{answer_id}/accept", headers=headers_a)
    assert acc.status_code == 200, acc.text

    b_feed = _list(client, headers_b)
    accepted = [i for i in b_feed["items"] if i["type"] == "answer_accepted"]
    assert len(accepted) == 1
    assert accepted[0]["payload"]["answer_id"] == answer_id


@requires_db
def test_approve_notifies_uploader(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    profile_id = _upload(client, headers_a)

    appr = client.post(f"/api/v1/admin/review/{profile_id}/approve", headers=ADMIN_HEADER)
    assert appr.status_code == 200, appr.text

    a_feed = _list(client, headers_a)
    assert len(a_feed["items"]) == 1
    assert a_feed["items"][0]["type"] == "profile_approved"
    assert a_feed["items"][0]["payload"]["profile_id"] == profile_id


@requires_db
def test_reject_notifies_uploader_with_reason(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    profile_id = _upload(client, headers_a)

    rej = client.post(
        f"/api/v1/admin/review/{profile_id}/reject",
        json={"reason": "duplicate"},
        headers=ADMIN_HEADER,
    )
    assert rej.status_code == 200, rej.text

    a_feed = _list(client, headers_a)
    assert len(a_feed["items"]) == 1
    assert a_feed["items"][0]["type"] == "profile_rejected"
    assert a_feed["items"][0]["payload"]["reason"] == "duplicate"


@requires_db
def test_unread_only_filters_and_unread_count(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    profile_id = _upload(client, headers_a)
    _comment(client, headers_b, profile_id)

    feed = _list(client, headers_a, unread_only=True)
    assert feed["unread_count"] == 1
    assert len(feed["items"]) == 1
    assert feed["items"][0]["is_read"] is False


@requires_db
def test_mark_read_all_zeroes_unread(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    profile_id = _upload(client, headers_a)
    _comment(client, headers_b, profile_id)

    resp = client.post("/api/v1/notifications/mark-read", json={"all": True}, headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["marked"] == 1

    feed = _list(client, headers_a)
    assert feed["unread_count"] == 0


@requires_db
def test_mark_read_ids_only_marks_owned(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    profile_id = _upload(client, headers_a)
    _comment(client, headers_b, profile_id)

    event_id = _list(client, headers_a)["items"][0]["id"]
    resp = client.post(
        "/api/v1/notifications/mark-read",
        json={"ids": [event_id, "not-mine-id"]},
        headers=headers_a,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["marked"] == 1

    assert _list(client, headers_a)["unread_count"] == 0


@requires_db
def test_other_instance_cannot_see_or_mark_events(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    other = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    headers_other = _bearer(client, make_login_jwt, other)
    profile_id = _upload(client, headers_a)
    _comment(client, headers_b, profile_id)

    a_event_id = _list(client, headers_a)["items"][0]["id"]

    other_feed = _list(client, headers_other)
    assert other_feed["items"] == []
    assert other_feed["unread_count"] == 0

    resp = client.post(
        "/api/v1/notifications/mark-read",
        json={"ids": [a_event_id]},
        headers=headers_other,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["marked"] == 0

    assert _list(client, headers_a)["unread_count"] == 1


@requires_db
def test_payload_contains_only_expected_keys(db_app_client, make_login_jwt):
    client = db_app_client
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    headers_a = _bearer(client, make_login_jwt, a)
    headers_b = _bearer(client, make_login_jwt, b)
    profile_id = _upload(client, headers_a)
    _comment(client, headers_b, profile_id)

    payload = _list(client, headers_a)["items"][0]["payload"]
    assert set(payload.keys()) == {"profile_id", "profile_name", "commenter_display"}
