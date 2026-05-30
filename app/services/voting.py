"""Voting service.

One unified votes table backs every votable target. Casting a vote upserts on
the unique ``(instance_uuid, target_type, target_id)`` key, then recomputes the
target's cached ``vote_score`` (SUM of all its votes) in the SAME transaction.
Callers are responsible for committing.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.community_profile import CommunityProfile
from app.models.profile_comment import ProfileComment
from app.models.vote import Vote

# All target types the unified votes table can carry. Only the two with a cached
# score column are wired up so far; question/answer land in a later chunk.
VALID_TARGET_TYPES = {"profile", "comment", "question", "answer"}
_SCORED_TARGET_TYPES = {"profile", "comment"}


def _load_scored_target(db: Session, target_type: str, target_id: str):
    """Return the target row that carries a cached ``vote_score``.

    Raises 404 if the target does not exist (or is a removed comment), and 400
    if ``target_type`` has no cached-score table wired up yet.
    """
    if target_type == "profile":
        row = db.get(CommunityProfile, target_id)
        if row is None or row.is_removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        return row
    if target_type == "comment":
        row = db.get(ProfileComment, target_id)
        if row is None or row.is_removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
        return row
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported vote target_type: {target_type}",
    )


def _recompute_score(db: Session, target_type: str, target_id: str) -> int:
    total = db.execute(
        select(func.coalesce(func.sum(Vote.value), 0)).where(
            Vote.target_type == target_type,
            Vote.target_id == target_id,
        )
    ).scalar_one()
    total = int(total)
    target = _load_scored_target(db, target_type, target_id)
    target.vote_score = total
    db.flush()
    return total


def cast_vote(
    db: Session,
    instance_uuid: str,
    target_type: str,
    target_id: str,
    value: int,
    reason: str | None,
) -> int:
    """Upsert the caller's vote and recompute the cached score. Returns score."""
    if value not in (-1, 1):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="value must be -1 or 1",
        )
    # Validates the target exists (and is a known, scored type).
    _load_scored_target(db, target_type, target_id)

    existing = db.execute(
        select(Vote).where(
            Vote.instance_uuid == instance_uuid,
            Vote.target_type == target_type,
            Vote.target_id == target_id,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            Vote(
                instance_uuid=instance_uuid,
                target_type=target_type,
                target_id=target_id,
                value=value,
                reason=reason,
            )
        )
    else:
        existing.value = value
        existing.reason = reason
    db.flush()
    return _recompute_score(db, target_type, target_id)


def remove_vote(
    db: Session,
    instance_uuid: str,
    target_type: str,
    target_id: str,
) -> int:
    """Delete the caller's vote (if any) and recompute the cached score."""
    # Validates the target exists (and is a known, scored type).
    _load_scored_target(db, target_type, target_id)

    existing = db.execute(
        select(Vote).where(
            Vote.instance_uuid == instance_uuid,
            Vote.target_type == target_type,
            Vote.target_id == target_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        db.delete(existing)
        db.flush()
    return _recompute_score(db, target_type, target_id)


def get_my_vote(
    db: Session,
    instance_uuid: str | None,
    target_type: str,
    target_id: str,
) -> int:
    """Return the caller's vote value (±1) on a target, or 0."""
    if not instance_uuid:
        return 0
    value = db.execute(
        select(Vote.value).where(
            Vote.instance_uuid == instance_uuid,
            Vote.target_type == target_type,
            Vote.target_id == target_id,
        )
    ).scalar_one_or_none()
    return int(value) if value is not None else 0


def get_my_votes_for_targets(
    db: Session,
    instance_uuid: str | None,
    target_type: str,
    target_ids: list[str],
) -> dict[str, int]:
    """Batch-fetch the caller's votes for many targets of one type."""
    if not instance_uuid or not target_ids:
        return {}
    rows = db.execute(
        select(Vote.target_id, Vote.value).where(
            Vote.instance_uuid == instance_uuid,
            Vote.target_type == target_type,
            Vote.target_id.in_(target_ids),
        )
    ).all()
    return {target_id: int(value) for target_id, value in rows}
