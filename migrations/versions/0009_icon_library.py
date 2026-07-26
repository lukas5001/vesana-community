"""icon library — mirrored vendor/product icons served to instances

Revision ID: 0009_icon_library
Revises: 0008_profile_match_rules
Create Date: 2026-07-26

The hub mirrors dashboard-icons + simple-icons into ``library_icons`` so Vesana
instances can search and import vendor logos server-side (no third-party CDN in
the customer's runtime path). Bodies live in the DB — small collection, no
extra volume, upstream removals do not silently break served icons.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_icon_library"
down_revision: str | None = "0008_profile_match_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.create_table(
        "library_icons",
        sa.Column("slug", sa.String(length=128), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("categories", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("monochrome", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("dark_file_format", sa.String(length=8), nullable=True),
        sa.Column("dark_body", sa.LargeBinary(), nullable=True),
        sa.Column("dark_sha256", sa.String(length=64), nullable=True),
        sa.Column("upstream_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_community_library_icons_name", "library_icons", ["name"], schema=SCHEMA)
    op.create_index("ix_community_library_icons_source", "library_icons", ["source"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("library_icons", schema=SCHEMA)
