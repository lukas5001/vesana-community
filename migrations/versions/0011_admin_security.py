"""Admin-Neubau: 2FA-Konto, Admin-Protokoll, Sperrgrund an Instanzen

Revision ID: 0011_admin_security
Revises: 0010_profile_conn_flags
Create Date: 2026-09-01

* ``admin_accounts`` — TOTP-Geheimnis (verschlüsselt), Replay-Zähler,
  gehashte Backup-Codes. Benutzer/Passwort bleiben in der ``.env``.
* ``admin_audit_log`` — jede Admin-Aktion mit Ziel, Kurzfassung, IP.
* ``instances.blocked_reason`` / ``blocked_at`` — warum und seit wann eine
  Instanz gesperrt ist (vorher nur ein Bool ohne Gedächtnis).

Alles additiv; ``downgrade`` entfernt es wieder.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_admin_security"
down_revision: str | None = "0010_profile_conn_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.create_table(
        "admin_accounts",
        sa.Column("username", sa.String(128), primary_key=True),
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_last_counter", sa.BigInteger(), nullable=True),
        sa.Column("backup_codes", postgresql.JSONB(), nullable=True),
        sa.Column("backup_codes_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("admin_user", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_admin_audit_created", "admin_audit_log", ["created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_admin_audit_target", "admin_audit_log", ["target_type", "target_id"], schema=SCHEMA
    )

    op.add_column("instances", sa.Column("blocked_reason", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "instances",
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("instances", "blocked_at", schema=SCHEMA)
    op.drop_column("instances", "blocked_reason", schema=SCHEMA)
    op.drop_index("ix_admin_audit_target", table_name="admin_audit_log", schema=SCHEMA)
    op.drop_index("ix_admin_audit_created", table_name="admin_audit_log", schema=SCHEMA)
    op.drop_table("admin_audit_log", schema=SCHEMA)
    op.drop_table("admin_accounts", schema=SCHEMA)
