"""LibraryIcon model: a mirrored vendor/product icon served to Vesana instances.

The hub mirrors two upstream open-source icon collections (homarr-labs/
dashboard-icons and simple-icons) into this table so that self-hosted Vesana
instances can browse and import icons without talking to third-party CDNs.
The sync job (``app.sync_icon_library``) fills and refreshes the rows; the
API (``app.routers.icon_library``) serves index + files read-only.

Bodies live in the database on purpose: the collection is small (tens of MB),
survives container rebuilds without extra volumes, and nothing disappears when
an upstream project removes a logo — removals become a deliberate curation
decision instead of silent breakage.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LibraryIcon(Base):
    __tablename__ = "library_icons"
    __table_args__ = (
        Index("ix_community_library_icons_name", "name"),
        Index("ix_community_library_icons_source", "source"),
    )

    # Upstream slug ("proxmox", "ubiquiti-unifi"). Primary key: one entry per
    # slug across all sources — the sync merge rule decides which source wins.
    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    categories: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    # 'dashboard-icons' | 'simple-icons'
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # True for single-color glyphs (simple-icons) that render well via
    # currentColor masking — maps to Vesana's IconAsset.use_current_color.
    monochrome: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)

    # Light-mode body (the default file shown on light backgrounds).
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)  # 'svg' | 'png'
    body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Optional dark-mode variant (light-colored artwork for dark backgrounds).
    dark_file_format: Mapped[str | None] = mapped_column(String(8), nullable=True)
    dark_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    dark_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Upstream change marker (dashboard-icons metadata timestamp) so the sync
    # can skip unchanged entries without re-downloading bodies.
    upstream_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
