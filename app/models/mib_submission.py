"""MibSubmission: ein Vorschlag einer Instanz für die MIB-Bibliothek.

Eine Instanz schickt das Verzeichnis einer selbst hochgeladenen MIB (auf
Wunsch mit Originaltext) zur Prüfung. Bis ein Admin entscheidet, sieht es
niemand außer ihm; die Lizenz-Linie der Bibliothek (`mib_module.py`) gilt
erst beim Freigeben, wenn derselbe Dienst wie der Kurator-Befehl veröffentlicht.

Nach der Entscheidung (oder dem Zurückziehen) werden Verzeichnis und
Originaltext gelöscht: ein Vorschlag ist kein zweites Archiv für Dateien,
deren Lizenz die Weitergabe vielleicht nicht erlaubt.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db import Base

#: Zustände eines Vorschlags. Nur ``pending`` lässt sich noch ändern.
STATUSES = ("pending", "approved", "rejected", "withdrawn")


class MibSubmission(Base):
    __tablename__ = "mib_submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="status",
        ),
        Index("ix_community_mib_submissions_status", "status", "created_at"),
        Index("ix_community_mib_submissions_instance", "instance_uuid", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    module: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    enterprise: Mapped[str | None] = mapped_column(String(64), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # NULL nach der Entscheidung (siehe Modul-Docstring).
    directory: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    object_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    descriptions_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_source_text: Mapped[bool] = column_property(source_text.isnot(None))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # Was der Einsender angibt — der Admin prüft und entscheidet selbst.
    license_claim: Mapped[str] = mapped_column(String(200), nullable=False)
    allows_source_claim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Begründung einer Ablehnung — die Instanz zeigt sie dem Einsender.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
