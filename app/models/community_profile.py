"""CommunityProfile model: a shareable Vesana monitoring profile."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.community_profile_version import CommunityProfileVersion


def _new_uuid() -> str:
    return str(uuid.uuid4())


class CommunityProfile(Base):
    __tablename__ = "community_profiles"
    __table_args__ = (
        Index("ix_community_community_profiles_tier", "tier"),
        Index("ix_community_community_profiles_category", "category"),
        Index("ix_community_community_profiles_vendor", "vendor"),
        Index("ix_community_community_profiles_approved", "approved"),
        Index("ix_community_community_profiles_is_removed", "is_removed"),
        Index("ix_community_community_profiles_review_status", "review_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    approved: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, default=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vesana_min_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requires_agent: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, default=False
    )
    requires_collector: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, default=False
    )
    requires_snmp: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, default=False
    )
    # NULL for official/beta profiles; the instance uuid (no FK, re-seed safe).
    uploader_instance_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    download_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False, default=0
    )
    import_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False, default=0
    )
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    # Discovery classification rules (mirrors Vesana's profiles.match_rules).
    # Served to instances so community-first discovery can suggest this profile.
    match_rules: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    latest_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "community.community_profile_versions.id",
            name="fk_community_profiles_latest_version_id",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_removed: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, default=False
    )
    # Cached vote score (SUM of votes.value for this profile), kept in sync by
    # app.services.voting in the same transaction as each vote change (C4).
    vote_score: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False, default=0)

    # Review workflow (C3). ``review_status`` is the source of truth for upload
    # visibility: 'pending' | 'approved' | 'rejected'. Existing official/beta
    # rows default to 'approved'. The legacy ``approved`` bool above is kept in
    # sync by the upload/review service (approved == (review_status=='approved')).
    review_status: Mapped[str] = mapped_column(
        Text, server_default="approved", nullable=False, default="approved"
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Heuristic script-gate output (C3): NOT a sandbox, just a flag + findings.
    has_scripts: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, default=False
    )
    script_findings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    versions: Mapped[list[CommunityProfileVersion]] = relationship(
        "CommunityProfileVersion",
        back_populates="profile",
        foreign_keys="CommunityProfileVersion.profile_id",
        cascade="all, delete-orphan",
        order_by="CommunityProfileVersion.created_at.desc()",
    )
    latest_version: Mapped[CommunityProfileVersion | None] = relationship(
        "CommunityProfileVersion",
        foreign_keys=[latest_version_id],
        post_update=True,
        viewonly=True,
    )

    @property
    def current_version(self) -> CommunityProfileVersion | None:
        """Return the version flagged ``is_current`` (the importable one)."""
        for version in self.versions:
            if version.is_current:
                return version
        return None
