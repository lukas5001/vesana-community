"""community events: per-instance notification feed (C6a)

Revision ID: 0006_community_events
Revises: 0005_upload_review
Create Date: 2026-05-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_community_events"
down_revision: str | None = "0005_upload_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.create_table(
        "community_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instance_uuid", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "is_read",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_community_events")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_events_recipient_unread",
        "community_events",
        ["instance_uuid", "is_read"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_events_recipient_created",
        "community_events",
        ["instance_uuid", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_events_recipient_created",
        table_name="community_events",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_events_recipient_unread",
        table_name="community_events",
        schema=SCHEMA,
    )
    op.drop_table("community_events", schema=SCHEMA)
