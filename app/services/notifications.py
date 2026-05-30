"""Notification service (C6a): enqueue community events for instances.

An "event" is a small notification row destined for exactly one recipient
instance (``instance_uuid``). ``enqueue`` inserts it in the SAME session as the
action that triggered it, so the caller's transaction commits it atomically with
that action.

The payload carries only non-sensitive render data (ids + display strings).
Never put secrets, tokens, downvote reasons, or another instance's private data
in the payload.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.community_event import CommunityEvent


def enqueue(
    db: Session,
    *,
    recipient_uuid: str | None,
    actor_uuid: str | None,
    type: str,
    payload: dict | None = None,
) -> None:
    """Insert a community event for ``recipient_uuid`` in the current session.

    No-op when there is no recipient (e.g. official/beta profiles whose
    uploader is ``None``) or when the recipient is the actor (no self-notify).
    The row is added + flushed but NOT committed here; the caller's transaction
    commits it atomically with the triggering action.
    """
    if not recipient_uuid:
        return
    if recipient_uuid == actor_uuid:
        return

    event = CommunityEvent(
        instance_uuid=recipient_uuid,
        type=type,
        payload_json=payload or {},
    )
    db.add(event)
    db.flush()
