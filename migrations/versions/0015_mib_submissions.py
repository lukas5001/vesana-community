"""MIB-Vorschläge — Instanzen schlagen ein übersetztes MIB-Verzeichnis vor

Revision ID: 0015_mib_submissions
Revises: 0014_mib_library
Create Date: 2026-10-18

Eine Vesana-Instanz schickt das Verzeichnis einer selbst hochgeladenen MIB
(dazu auf Wunsch den Originaltext) in eine Prüf-Warteschlange. Sichtbar wird
nichts davon, bevor ein Admin die Lizenz geprüft und freigegeben hat; dann
legt derselbe Dienst wie der Kurator-Befehl das Modul in die Bibliothek.
Nach der Entscheidung werden Verzeichnis und Originaltext des Vorschlags
gelöscht — aufbewahrt bleibt nur, was die Bibliothek selbst trägt.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_mib_submissions"
down_revision: str | None = "0014_mib_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.create_table(
        "mib_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instance_uuid", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("enterprise", sa.String(length=64), nullable=True),
        sa.Column("organization", sa.String(length=512), nullable=True),
        sa.Column("directory", postgresql.JSONB(), nullable=True),
        sa.Column("object_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("descriptions_included", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("license_claim", sa.String(length=200), nullable=False),
        sa.Column("allows_source_claim", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name=op.f("ck_mib_submissions_status"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_community_mib_submissions_status",
        "mib_submissions",
        ["status", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_community_mib_submissions_instance",
        "mib_submissions",
        ["instance_uuid", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("mib_submissions", schema=SCHEMA)
