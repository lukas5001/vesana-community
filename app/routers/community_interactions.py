"""Voting + comments API for community profiles (C4).

Writes require a Bearer API token (``get_current_instance``); the acting
identity is always ``instance.uuid``. Reading the comment thread works
unauthenticated (optional Bearer), in which case ``my_vote``/``can_edit`` are
neutral. Admin-only powers (force-delete any comment, set helpful on
official/beta profiles) are granted via HTTP Basic credentials supplied in a
dedicated ``X-Admin-Authorization`` header so they can coexist with the Bearer
token the write itself needs.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.auth.deps import get_current_instance
from app.auth.tokens import TokenError, verify_api_token
from app.config import Settings, get_settings
from app.db import get_db
from app.models.instance import Instance
from app.models.profile_comment import ProfileComment
from app.schemas.interactions import (
    CommentEdit,
    CommentIn,
    CommentOut,
    CommentThread,
    HelpfulIn,
    ReportIn,
    VoteIn,
    VoteResult,
)
from app.services import comments as comments_service
from app.services import voting as voting_service
from app.services.comments import author_display, within_edit_window

router = APIRouter(tags=["interactions"])

DbDep = Annotated[Session, Depends(get_db)]
CurrentInstance = Annotated[Instance, Depends(get_current_instance)]


def get_optional_instance(
    db: DbDep,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Instance | None:
    """Optional Bearer auth.

    Returns the calling Instance when a valid, non-blocked API token is present,
    otherwise ``None`` (unauthenticated reads are allowed).
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer ") :].strip()
    if not token:
        return None
    try:
        claims = verify_api_token(token, settings=settings)
    except TokenError:
        return None
    instance = db.get(Instance, claims["sub"])
    if instance is None or instance.is_blocked:
        return None
    return instance


OptionalInstance = Annotated[Instance | None, Depends(get_optional_instance)]


def is_admin_request(
    settings: Annotated[Settings, Depends(get_settings)],
    x_admin_authorization: Annotated[str | None, Header()] = None,
) -> bool:
    """True only for valid admin HTTP Basic credentials in the
    ``X-Admin-Authorization`` header. Never raises; absence means "not admin"."""
    if not x_admin_authorization or not x_admin_authorization.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(x_admin_authorization[len("Basic ") :].strip()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user, _, password = raw.partition(":")
    user_ok = hmac.compare_digest(
        user.encode("utf-8"), settings.COMMUNITY_ADMIN_USER.encode("utf-8")
    )
    pass_ok = hmac.compare_digest(
        password.encode("utf-8"), settings.COMMUNITY_ADMIN_PASSWORD.encode("utf-8")
    )
    return user_ok and pass_ok


AdminFlag = Annotated[bool, Depends(is_admin_request)]


def _comment_out(
    db: Session,
    comment: ProfileComment,
    caller_uuid: str | None,
    reply_count: int = 0,
) -> CommentOut:
    instance = db.get(Instance, comment.instance_uuid)
    is_owner = caller_uuid is not None and caller_uuid == comment.instance_uuid
    can_edit = is_owner and not comment.is_removed and within_edit_window(comment.created_at)
    my_vote = voting_service.get_my_vote(db, caller_uuid, "comment", comment.id)
    return CommentOut(
        id=comment.id,
        instance_uuid=comment.instance_uuid,
        author_display=author_display(
            instance.display_name if instance else None, comment.instance_uuid
        ),
        body_md=None if comment.is_removed else comment.body_md,
        parent_id=comment.parent_id,
        vote_score=comment.vote_score,
        is_helpful=comment.is_helpful,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        can_edit=can_edit,
        my_vote=my_vote,
        reply_count=reply_count,
    )


# ---- Profile votes ---------------------------------------------------------


@router.post("/api/v1/profiles/{profile_id}/vote", response_model=VoteResult)
def vote_profile(
    profile_id: str,
    payload: VoteIn,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.cast_vote(
        db, instance.uuid, "profile", profile_id, payload.value, payload.reason
    )
    db.commit()
    return VoteResult(
        target_type="profile", target_id=profile_id, value=payload.value, vote_score=score
    )


@router.delete("/api/v1/profiles/{profile_id}/vote", response_model=VoteResult)
def unvote_profile(
    profile_id: str,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.remove_vote(db, instance.uuid, "profile", profile_id)
    db.commit()
    return VoteResult(target_type="profile", target_id=profile_id, value=0, vote_score=score)


# ---- Comments --------------------------------------------------------------


@router.get("/api/v1/profiles/{profile_id}/comments", response_model=list[CommentThread])
def list_comments(
    profile_id: str,
    db: DbDep,
    instance: OptionalInstance,
) -> list[CommentThread]:
    caller_uuid = instance.uuid if instance else None
    return comments_service.list_thread(db, profile_id, caller_uuid)


@router.post(
    "/api/v1/profiles/{profile_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    profile_id: str,
    payload: CommentIn,
    instance: CurrentInstance,
    db: DbDep,
) -> CommentOut:
    comment = comments_service.create_comment(
        db, profile_id, instance.uuid, payload.body_md, payload.parent_id
    )
    db.commit()
    return _comment_out(db, comment, instance.uuid, reply_count=0)


@router.put("/api/v1/comments/{comment_id}", response_model=CommentOut)
def edit_comment(
    comment_id: str,
    payload: CommentEdit,
    instance: CurrentInstance,
    db: DbDep,
) -> CommentOut:
    comment = comments_service.edit_comment(db, comment_id, instance.uuid, payload.body_md)
    db.commit()
    return _comment_out(db, comment, instance.uuid)


@router.delete("/api/v1/comments/{comment_id}", response_model=CommentOut)
def delete_comment(
    comment_id: str,
    instance: CurrentInstance,
    is_admin: AdminFlag,
    db: DbDep,
) -> CommentOut:
    comment = comments_service.soft_delete_comment(db, comment_id, instance.uuid, is_admin)
    db.commit()
    return _comment_out(db, comment, instance.uuid)


# ---- Comment votes ---------------------------------------------------------


@router.post("/api/v1/comments/{comment_id}/vote", response_model=VoteResult)
def vote_comment(
    comment_id: str,
    payload: VoteIn,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.cast_vote(
        db, instance.uuid, "comment", comment_id, payload.value, payload.reason
    )
    db.commit()
    return VoteResult(
        target_type="comment", target_id=comment_id, value=payload.value, vote_score=score
    )


@router.delete("/api/v1/comments/{comment_id}/vote", response_model=VoteResult)
def unvote_comment(
    comment_id: str,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.remove_vote(db, instance.uuid, "comment", comment_id)
    db.commit()
    return VoteResult(target_type="comment", target_id=comment_id, value=0, vote_score=score)


# ---- Helpful + report ------------------------------------------------------


@router.post("/api/v1/comments/{comment_id}/helpful", response_model=CommentOut)
def mark_helpful(
    comment_id: str,
    payload: HelpfulIn,
    instance: CurrentInstance,
    is_admin: AdminFlag,
    db: DbDep,
) -> CommentOut:
    comment = comments_service.set_helpful(db, comment_id, instance.uuid, is_admin, payload.helpful)
    db.commit()
    return _comment_out(db, comment, instance.uuid)


@router.post("/api/v1/comments/{comment_id}/report")
def report_comment(
    comment_id: str,
    payload: ReportIn,
    instance: CurrentInstance,
    db: DbDep,
) -> dict[str, str]:
    moderation_report = comments_service.report(
        db, "comment", comment_id, instance.uuid, payload.reason
    )
    db.commit()
    return {"status": moderation_report.status}
