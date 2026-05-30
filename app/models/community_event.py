"""CommunityEvent model: a notification destined for a single instance (C6a).

One row per notification. ``instance_uuid`` is the RECIPIENT — the instance that
should see the notice (no FK, re-seed safe, mirrors the other instance_uuid
columns). ``payload_json`` carries only small, non-sensitive render data (ids +
display strings); never secrets, tokens or downvote reasons.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


class CommunityEvent(Base):
    __tablename__ = "community_events"
    __table_args__ = (
        Index("ix_events_recipient_unread", "instance_uuid", "is_read"),
        Index("ix_events_recipient_created", "instance_uuid", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    # The RECIPIENT instance uuid — only this instance may ever read the event.
    instance_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
