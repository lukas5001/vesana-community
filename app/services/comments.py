"""Comments service: create / list / edit / soft-delete / helpful / report.

Threading is exactly one level deep. Edit + (owner) delete are limited to a
24h window measured from ``created_at``. Soft delete keeps the row (and its
replies) but hides the body. "Helpful" may be set only by the profile uploader
or an admin and sorts helpful comments to the top.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.community_profile import CommunityProfile
from app.models.instance import Instance
from app.models.moderation_report import ModerationReport
from app.models.profile_comment import ProfileComment
from app.schemas.interactions import CommentOut, CommentThread
from app.services import notifications
from app.services.voting import get_my_votes_for_targets

EDIT_WINDOW = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime) -> datetime:
    """Treat naive timestamps as UTC so window math is consistent."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def within_edit_window(created_at: datetime, now: datetime | None = None) -> bool:
    now = now or _now()
    return (_as_aware(now) - _as_aware(created_at)) <= EDIT_WINDOW


def author_display(display_name: str | None, instance_uuid: str) -> str:
    if display_name and display_name.strip():
        return display_name
    return f"instanz-{instance_uuid[:8]}"


def _display_names_for(db: Session, uuids: list[str]) -> dict[str, str | None]:
    if not uuids:
        return {}
    rows = db.execute(
        select(Instance.uuid, Instance.display_name).where(Instance.uuid.in_(uuids))
    ).all()
    return {uuid_: display_name for uuid_, display_name in rows}


def _require_profile(db: Session, profile_id: str) -> CommunityProfile:
    profile = db.get(CommunityProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile


def _require_comment(db: Session, comment_id: str) -> ProfileComment:
    comment = db.get(ProfileComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    return comment


def _to_out(
    comment: ProfileComment,
    display_names: dict[str, str | None],
    my_votes: dict[str, int],
    caller_uuid: str | None,
    reply_count: int,
) -> CommentOut:
    is_owner = caller_uuid is not None and caller_uuid == comment.instance_uuid
    can_edit = is_owner and not comment.is_removed and within_edit_window(comment.created_at)
    return CommentOut(
        id=comment.id,
        instance_uuid=comment.instance_uuid,
        author_display=author_display(
            display_names.get(comment.instance_uuid), comment.instance_uuid
        ),
        body_md=None if comment.is_removed else comment.body_md,
        parent_id=comment.parent_id,
        vote_score=comment.vote_score,
        is_helpful=comment.is_helpful,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        can_edit=can_edit,
        my_vote=my_votes.get(comment.id, 0),
        reply_count=reply_count,
    )


def create_comment(
    db: Session,
    profile_id: str,
    instance_uuid: str,
    body_md: str,
    parent_id: str | None,
) -> ProfileComment:
    profile = _require_profile(db, profile_id)
    parent: ProfileComment | None = None
    if parent_id is not None:
        parent = db.get(ProfileComment, parent_id)
        if parent is None or parent.is_removed or parent.profile_id != profile_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Parent comment not found"
            )
        # Exactly one level: the parent itself must be top-level.
        if parent.parent_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Replies may only be one level deep",
            )
    comment = ProfileComment(
        profile_id=profile_id,
        instance_uuid=instance_uuid,
        parent_id=parent_id,
        body_md=body_md,
    )
    db.add(comment)
    db.flush()
    db.refresh(comment)

    commenter_display = author_display(
        _display_names_for(db, [instance_uuid]).get(instance_uuid), instance_uuid
    )
    if parent is not None:
        # Reply -> notify the parent comment's author.
        notifications.enqueue(
            db,
            recipient_uuid=parent.instance_uuid,
            actor_uuid=instance_uuid,
            type="comment_reply",
            payload={
                "profile_id": profile_id,
                "comment_id": comment.id,
                "commenter_display": commenter_display,
            },
        )
    else:
        # Top-level comment -> notify the profile's uploader (skipped if it is
        # the commenter themselves, or for official/beta profiles with no owner).
        notifications.enqueue(
            db,
            recipient_uuid=profile.uploader_instance_uuid,
            actor_uuid=instance_uuid,
            type="profile_comment",
            payload={
                "profile_id": profile_id,
                "profile_name": profile.name,
                "commenter_display": commenter_display,
            },
        )
    return comment


def _sort_key(comment: ProfileComment):
    # Helpful first, then higher score, then oldest first (stable thread order).
    return (0 if comment.is_helpful else 1, -comment.vote_score, _as_aware(comment.created_at))


def list_thread(
    db: Session,
    profile_id: str,
    caller_uuid: str | None,
) -> list[CommentThread]:
    _require_profile(db, profile_id)
    all_comments = list(
        db.execute(select(ProfileComment).where(ProfileComment.profile_id == profile_id)).scalars()
    )
    tops = [c for c in all_comments if c.parent_id is None]
    replies_by_parent: dict[str, list[ProfileComment]] = {}
    for c in all_comments:
        if c.parent_id is not None:
            replies_by_parent.setdefault(c.parent_id, []).append(c)

    tops.sort(key=_sort_key)
    for replies in replies_by_parent.values():
        replies.sort(key=_sort_key)

    display_names = _display_names_for(db, list({c.instance_uuid for c in all_comments}))
    my_votes = get_my_votes_for_targets(db, caller_uuid, "comment", [c.id for c in all_comments])

    threads: list[CommentThread] = []
    for top in tops:
        replies = replies_by_parent.get(top.id, [])
        threads.append(
            CommentThread(
                comment=_to_out(top, display_names, my_votes, caller_uuid, len(replies)),
                replies=[_to_out(r, display_names, my_votes, caller_uuid, 0) for r in replies],
            )
        )
    return threads


def edit_comment(
    db: Session,
    comment_id: str,
    caller_uuid: str,
    body_md: str,
) -> ProfileComment:
    comment = _require_comment(db, comment_id)
    if comment.instance_uuid != caller_uuid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your comment")
    if comment.is_removed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Comment is removed")
    if not within_edit_window(comment.created_at):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Edit window (24h) has expired"
        )
    comment.body_md = body_md
    db.flush()
    db.refresh(comment)
    return comment


def soft_delete_comment(
    db: Session,
    comment_id: str,
    caller_uuid: str,
    is_admin: bool,
) -> ProfileComment:
    comment = _require_comment(db, comment_id)
    is_owner = comment.instance_uuid == caller_uuid
    if not is_admin:
        if not is_owner:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your comment")
        if not within_edit_window(comment.created_at):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Delete window (24h) has expired"
            )
    comment.is_removed = True
    db.flush()
    db.refresh(comment)
    return comment


def set_helpful(
    db: Session,
    comment_id: str,
    caller_uuid: str,
    is_admin: bool,
    helpful: bool,
) -> ProfileComment:
    comment = _require_comment(db, comment_id)
    profile = _require_profile(db, comment.profile_id)
    is_uploader = (
        profile.uploader_instance_uuid is not None and profile.uploader_instance_uuid == caller_uuid
    )
    if not (is_admin or is_uploader):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the profile uploader or an admin may mark helpful",
        )
    comment.is_helpful = helpful
    db.flush()
    db.refresh(comment)
    return comment


def report(
    db: Session,
    target_type: str,
    target_id: str,
    reporter_uuid: str,
    reason: str,
) -> ModerationReport:
    if target_type == "comment":
        _require_comment(db, target_id)
    moderation_report = ModerationReport(
        target_type=target_type,
        target_id=target_id,
        instance_uuid=reporter_uuid,
        reason=reason,
    )
    db.add(moderation_report)
    db.flush()
    db.refresh(moderation_report)
    return moderation_report
