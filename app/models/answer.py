"""Answer model: a reply to a community question.

``vote_score`` is a cached SUM of this answer's votes (target_type 'answer'),
kept in sync by app.services.voting in the same transaction as each vote change.
Only one answer per question may be ``is_accepted`` (enforced app-side by
flipping the others false in one transaction AND by a Postgres partial unique
index ``uq_answers_one_accepted``). ``is_vesana_team`` is stamped only when the
author posts with valid admin credentials.
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.question import Question


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_question_id", "question_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    question_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "community.questions.id",
            name="fk_answers_question",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    instance_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    vote_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    is_accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
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

    question: Mapped[Question] = relationship(
        "Question",
        back_populates="answers",
        foreign_keys=[question_id],
    )
