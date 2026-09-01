"""``admin_accounts`` — Sicherheitszustand des Hub-Admins (2FA).

Benutzername und Passwort des Admins kommen weiterhin aus der ``.env``
(``COMMUNITY_ADMIN_USER`` / ``COMMUNITY_ADMIN_PASSWORD``). Diese Tabelle trägt
nur, was der Admin selbst in der Oberfläche einrichtet: das verschlüsselte
TOTP-Geheimnis, den Replay-Zähler und die gehashten Backup-Codes.

Notfall (Gerät verloren, keine Backup-Codes): ``DELETE FROM
community.admin_accounts WHERE username = '<user>'`` schaltet den zweiten
Faktor ab — bewusst nur per Datenbankzugriff, nie per Umgebungsvariable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AdminAccount(Base):
    __tablename__ = "admin_accounts"

    username: Mapped[str] = mapped_column(String(128), primary_key=True)

    # AES-GCM-verschlüsseltes Base32-Geheimnis (siehe app.auth.admin_security).
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Zuletzt akzeptierter TOTP-Zähler — ein Code gilt genau einmal.
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Liste von HMAC-Hashes; ein verbrauchter Code wird entfernt.
    backup_codes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    backup_codes_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def backup_codes_left(self) -> int:
        return len(self.backup_codes or [])
