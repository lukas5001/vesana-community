"""upload + review: review_status, rejection_reason, has_scripts, script_findings

Revision ID: 0005_upload_review
Revises: 0004_qa_portal
Create Date: 2026-05-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_upload_review"
down_revision: str | None = "0004_qa_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    # review_status is the source of truth for community-upload visibility.
    # Existing official/beta rows default to 'approved' so they stay visible;
    # new community uploads set 'pending' in the upload service.
    op.add_column(
        "community_profiles",
        sa.Column(
            "review_status",
            sa.Text(),
            server_default=sa.text("'approved'"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "community_profiles",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "community_profiles",
        sa.Column(
            "has_scripts",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "community_profiles",
        sa.Column("script_findings", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_community_community_profiles_review_status",
        "community_profiles",
        ["review_status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_community_community_profiles_review_status",
        table_name="community_profiles",
        schema=SCHEMA,
    )
    op.drop_column("community_profiles", "script_findings", schema=SCHEMA)
    op.drop_column("community_profiles", "has_scripts", schema=SCHEMA)
    op.drop_column("community_profiles", "rejection_reason", schema=SCHEMA)
    op.drop_column("community_profiles", "review_status", schema=SCHEMA)
