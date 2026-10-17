"""MibModule: ein kuratiertes MIB-Modul der Hub-Bibliothek.

Instanzen holen von hier das abgeleitete OBJEKT-VERZEICHNIS eines Moduls (OIDs,
Namen, Typen, Wertelisten, Einheiten, Tabellenaufbau — Schnittstellen-Fakten),
wenn ein Profil es verweist (`bundle.mibs`), wenn der Nutzer im Tab „MIBs"
sucht oder ein Scan einen Hersteller-Zweig ohne lokale MIB trifft.

Lizenz-Linie (Plan mib-und-rate.md §6.2): der Originaltext (`source_text`) und
die DESCRIPTION-Texte im Verzeichnis liegen nur hier, wenn die Lizenz der Datei
die Weitergabe erlaubt (`license_allows_source`). Veröffentlichen dürfen NUR
Kuratoren (Admin-API) — es gibt keinen öffentlichen Upload.

Eine Zeile je (Modul, Revision); ausgeliefert wird die neueste nicht entfernte
Revision. Entfernen ist weich (`removed_at`), damit eine verwiesene Revision
nachvollziehbar bleibt.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db import Base


class MibModule(Base):
    __tablename__ = "mib_modules"
    __table_args__ = (
        UniqueConstraint("module", "revision", name="uq_community_mib_modules_module_revision"),
        Index("ix_community_mib_modules_module", "module"),
        Index("ix_community_mib_modules_enterprise", "enterprise"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module: Mapped[str] = mapped_column(String(128), nullable=False)
    # Leerstring = Modul ohne REVISION-Klausel (SMIv1) — NULL taugt nicht für
    # den Unique-Constraint.
    revision: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    # Hersteller-Zweig 1.3.6.1.4.1.<n>, abgeleitet beim Veröffentlichen.
    enterprise: Mapped[str | None] = mapped_column(String(64), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(512), nullable=True)
    directory: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    object_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    descriptions_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Liegt der Originaltext wirklich vor? Als SQL-Ausdruck, damit Listen den
    # (großen) Text nicht laden müssen, um „herunterladbar" zu sagen.
    has_source_text: Mapped[bool] = column_property(source_text.isnot(None))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    license: Mapped[str] = mapped_column(String(200), nullable=False)
    license_allows_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    checked_at: Mapped[date] = mapped_column(Date, nullable=False)
    curator: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
