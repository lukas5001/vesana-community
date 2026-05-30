"""Vote model: one unified votes table for all votable targets.

A vote is one instance's ±1 on a single target (profile / comment / question /
answer). The unique key ``(instance_uuid, target_type, target_id)`` enforces
one vote per instance per target; re-voting UPDATES the row (upsert). The
optional ``reason`` is a private signal (surfaced only to the profile uploader
and admins in C8) and is never exposed in public responses.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint(
            "instance_uuid",
            "target_type",
            "target_id",
            name="uq_votes_target",
        ),
        CheckConstraint("value IN (-1, 1)", name="ck_votes_value"),
        Index("ix_votes_target", "target_type", "target_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    instance_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
