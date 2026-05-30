"""Question model: a community Q&A thread.

A question carries cached counters (``vote_score`` via the unified votes table,
``answer_count`` recomputed on answer create/delete) and a self-referential
``duplicate_of_id`` so an admin can close one question as a duplicate of another
(closed questions stay visible but accept no new answers). ``profile_id`` links
a question to a community profile; the link is severed (SET NULL) if the profile
is deleted. ``is_vesana_team`` is stamped only when the author posts with valid
admin credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.answer import Answer
    from app.models.community_profile import CommunityProfile


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        Index("ix_questions_is_closed", "is_closed"),
        Index("ix_questions_instance_uuid", "instance_uuid"),
        Index("ix_questions_profile_id", "profile_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    title_text: Mapped[str] = mapped_column(String(200), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    instance_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    # Cached SUM of this question's votes (target_type 'question'), kept in sync
    # by app.services.voting in the same transaction as each vote change.
    vote_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    # Cached count of answers, recomputed on answer create/delete.
    answer_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    is_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "community.questions.id",
            name="fk_questions_dup",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    profile_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "community.community_profiles.id",
            name="fk_questions_profile",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    is_vesana_team: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    answers: Mapped[list[Answer]] = relationship(
        "Answer",
        back_populates="question",
        foreign_keys="Answer.question_id",
        cascade="all, delete-orphan",
    )
    # Self-referential duplicate pointer (remote_side disambiguates the parent).
    # viewonly: the ``duplicate_of_id`` column is always set directly, never via
    # this relationship, so it must not take part in flush synchronisation
    # (which would emit a self-join UPDATE with an ambiguous ``id`` reference).
    duplicate_of: Mapped[Question | None] = relationship(
        "Question",
        foreign_keys=[duplicate_of_id],
        remote_side=lambda: [Question.id],
        viewonly=True,
    )
    profile: Mapped[CommunityProfile | None] = relationship(
        "CommunityProfile",
        foreign_keys=[profile_id],
    )
