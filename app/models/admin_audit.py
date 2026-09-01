"""``admin_audit_log`` — wer hat wann was im Admin getan.

Eine Zeile pro Admin-Aktion (Freigabe, Ablehnung, Tier, Sperre, Löschung,
Login, 2FA-Änderung …). Kein FK auf das Ziel: das Protokoll überlebt das
Löschen des Ziels. ``details`` trägt nur kleine, nicht-geheime Werte
(Vorher/Nachher-Felder, Gründe) — nie Passwörter, Codes oder Geheimnisse.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"
    __table_args__ = (
        Index("ix_admin_audit_created", "created_at"),
        Index("ix_admin_audit_target", "target_type", "target_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    admin_user: Mapped[str] = mapped_column(String(128), nullable=False)
    # z. B. review.approve · profile.tier · instance.block · auth.login
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Menschenlesbare Kurzfassung (Name des Profils, der Instanz …).
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
