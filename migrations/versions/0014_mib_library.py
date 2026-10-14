"""MIB-Bibliothek — kuratierte Objekt-Verzeichnisse für Instanzen

Revision ID: 0014_mib_library
Revises: 0013_api_access_eine_art
Create Date: 2026-10-14

Instanzen holen hier das abgeleitete Objekt-Verzeichnis eines MIB-Moduls,
wenn ein Profil es verweist (`bundle.mibs`), wenn im Tab „MIBs" gesucht wird
oder ein Scan einen Hersteller-Zweig ohne lokale MIB trifft (Vesana-Plan
mib-und-rate.md §2.5/§6). Nur Kuratoren veröffentlichen; Originaltext nur bei
erlaubender Lizenz.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_mib_library"
down_revision: str | None = "0013_api_access_eine_art"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.create_table(
        "mib_modules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("module", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("enterprise", sa.String(length=64), nullable=True),
        sa.Column("organization", sa.String(length=512), nullable=True),
        sa.Column("directory", postgresql.JSONB(), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("descriptions_included", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("license", sa.String(length=200), nullable=False),
        sa.Column("license_allows_source", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("checked_at", sa.Date(), nullable=False),
        sa.Column("curator", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("module", "revision", name="uq_community_mib_modules_module_revision"),
        schema=SCHEMA,
    )
    op.create_index("ix_community_mib_modules_module", "mib_modules", ["module"], schema=SCHEMA)
    op.create_index(
        "ix_community_mib_modules_enterprise", "mib_modules", ["enterprise"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("mib_modules", schema=SCHEMA)
