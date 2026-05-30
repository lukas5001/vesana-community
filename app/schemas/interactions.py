"""Pydantic schemas for voting + comments (C4).

Downvote reasons are accepted on the way in (``VoteIn.reason``) and stored, but
are NEVER echoed back in any response model here — they are a private signal for
the profile uploader + admins (surfaced in C8).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class VoteIn(BaseModel):
    value: int
    reason: str | None = None

    @field_validator("value")
    @classmethod
    def _value_must_be_plus_minus_one(cls, v: int) -> int:
        if v not in (-1, 1):
            raise ValueError("value must be -1 or 1")
        return v


class VoteResult(BaseModel):
    target_type: str
    target_id: str
    value: int
    vote_score: int


def _require_non_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be empty after stripping")
    return v


class CommentIn(BaseModel):
    body_md: str = Field(min_length=1, max_length=5000)
    parent_id: str | None = None

    @field_validator("body_md")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)


class CommentEdit(BaseModel):
    body_md: str = Field(min_length=1, max_length=5000)

    @field_validator("body_md")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)


class HelpfulIn(BaseModel):
    helpful: bool


class ReportIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)


class CommentOut(BaseModel):
    id: str
    instance_uuid: str
    author_display: str
    body_md: str | None  # None when the comment is removed (soft delete)
    parent_id: str | None
    vote_score: int
    is_helpful: bool
    created_at: datetime
    updated_at: datetime
    can_edit: bool  # caller owns it AND within the 24h window
    my_vote: int  # caller's own ±1 on this comment, or 0
    reply_count: int


class CommentThread(BaseModel):
    comment: CommentOut
    replies: list[CommentOut]
