"""CommunityProfileVersion model: a single versioned profile bundle."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.community_profile import CommunityProfile


def _new_uuid() -> str:
    return str(uuid.uuid4())


class CommunityProfileVersion(Base):
    __tablename__ = "community_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "version_tag",
            name="uq_community_profile_versions_profile_id",
        ),
        Index(
            "ix_community_community_profile_versions_profile_id",
            "profile_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    profile_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "community.community_profiles.id",
            name="fk_community_profile_versions_profile_id_community_profiles",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    version_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changelog_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, default=False
    )

    profile: Mapped[CommunityProfile] = relationship(
        "CommunityProfile",
        back_populates="versions",
        foreign_keys=[profile_id],
    )
