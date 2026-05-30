"""ModerationReport model: a report raised against a votable/commentable target.

Any authenticated instance may report a target (profile / comment / ...). Rows
start with ``status='open'``; C8 (moderation) consumes and resolves them. No FK
to the target so reports survive hard target deletion for audit purposes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


class ModerationReport(Base):
    __tablename__ = "moderation_reports"
    __table_args__ = (Index("ix_reports_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    instance_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="open", default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
