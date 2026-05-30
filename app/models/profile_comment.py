"""ProfileComment model: one-level threaded comments on a community profile.

Threading is intentionally exactly one level deep — a reply's ``parent_id``
must point at a top-level comment (enforced in the service layer). ``vote_score``
is a cached SUM of the comment's votes, recomputed in the same transaction as
each vote change. Deletes are soft (``is_removed``) so the thread structure and
any replies survive.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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


def _new_uuid() -> str:
    return str(uuid.uuid4())


class ProfileComment(Base):
    __tablename__ = "profile_comments"
    __table_args__ = (
        Index("ix_comments_profile", "profile_id"),
        Index("ix_comments_parent", "parent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    profile_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "community.community_profiles.id",
            ondelete="CASCADE",
            name="fk_comments_profile",
        ),
        nullable=False,
    )
    instance_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "community.profile_comments.id",
            ondelete="CASCADE",
            name="fk_comments_parent",
        ),
        nullable=True,
    )
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    vote_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    is_helpful: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    is_removed: Mapped[bool] = mapped_column(
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

    # Self-referential one-level thread. remote_side disambiguates which side of
    # the FK is the "parent" for the relationship.
    parent: Mapped[ProfileComment | None] = relationship(
        "ProfileComment",
        back_populates="replies",
        remote_side=lambda: [ProfileComment.id],
    )
    replies: Mapped[list[ProfileComment]] = relationship(
        "ProfileComment",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
