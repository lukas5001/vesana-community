"""Pydantic schemas for the Q&A portal (C5).

``VoteResult`` is reused from the interactions schemas (votes are unified across
profiles / comments / questions / answers). ``my_vote``/``can_edit`` are
caller-relative read fields, neutral for anonymous reads.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _require_non_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be empty after stripping")
    return v


class QuestionIn(BaseModel):
    title_text: str = Field(min_length=5, max_length=200)
    body_md: str = Field(min_length=1, max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=8)
    profile_id: str | None = None

    @field_validator("title_text")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)

    @field_validator("body_md")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)


class QuestionEdit(BaseModel):
    title_text: str = Field(min_length=5, max_length=200)
    body_md: str = Field(min_length=1, max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("title_text")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)

    @field_validator("body_md")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)


class AnswerIn(BaseModel):
    body_md: str = Field(min_length=1, max_length=20000)

    @field_validator("body_md")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        return _require_non_blank(v)


class AnswerOut(BaseModel):
    id: str
    instance_uuid: str
    author_display: str
    is_vesana_team: bool
    body_md: str
    vote_score: int
    is_accepted: bool
    my_vote: int  # caller's own ±1 on this answer, or 0
    can_edit: bool  # caller owns it AND within the 24h window
    created_at: datetime
    updated_at: datetime


class QuestionSummary(BaseModel):
    id: str
    title_text: str
    author_display: str
    is_vesana_team: bool
    tags: list[str]
    vote_score: int
    answer_count: int
    is_closed: bool
    has_accepted: bool
    created_at: datetime
    profile_id: str | None


class QuestionDetail(QuestionSummary):
    body_md: str
    closed_reason: str | None
    duplicate_of_id: str | None
    my_vote: int  # caller's own ±1 on this question, or 0
    can_edit: bool  # caller owns it AND within the 24h window
    answers: list[AnswerOut]


class SimilarQuestion(BaseModel):
    id: str
    title_text: str
    answer_count: int
    is_closed: bool
