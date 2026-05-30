"""The ``instances`` table.

One row per Vesana instance (identified by its ``instance_uuid``) that has ever
signed in to the Community Hub. The portal-signed login JWT is the only way a
row is created or refreshed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Instance(Base):
    __tablename__ = "instances"

    # The instance UUID as issued/known by the licence portal. Stored as text
    # so we never depend on a particular UUID representation.
    uuid: Mapped[str] = mapped_column(String(64), primary_key=True)

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Optional base64-encoded WebP avatar supplied via the login JWT.
    avatar_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Instance uuid={self.uuid!r} display_name={self.display_name!r}>"
