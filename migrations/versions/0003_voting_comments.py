"""voting + comments: votes, profile_comments, moderation_reports

Revision ID: 0003_voting_comments
Revises: 0002_profile_library
Create Date: 2026-05-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_voting_comments"
down_revision: str | None = "0002_profile_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.create_table(
        "votes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instance_uuid", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_votes"),
        sa.UniqueConstraint(
            "instance_uuid",
            "target_type",
            "target_id",
            name="uq_votes_target",
        ),
        sa.CheckConstraint("value IN (-1, 1)", name="ck_votes_value"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_votes_target",
        "votes",
        ["target_type", "target_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "profile_comments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("instance_uuid", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("vote_score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "is_helpful",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_removed",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_profile_comments"),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["community.community_profiles.id"],
            name="fk_comments_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["community.profile_comments.id"],
            name="fk_comments_parent",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_comments_profile",
        "profile_comments",
        ["profile_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_comments_parent",
        "profile_comments",
        ["parent_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "moderation_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("instance_uuid", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_moderation_reports"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_reports_status",
        "moderation_reports",
        ["status"],
        schema=SCHEMA,
    )

    op.add_column(
        "community_profiles",
        sa.Column(
            "vote_score",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("community_profiles", "vote_score", schema=SCHEMA)
    op.drop_index("ix_reports_status", table_name="moderation_reports", schema=SCHEMA)
    op.drop_table("moderation_reports", schema=SCHEMA)
    op.drop_index("ix_comments_parent", table_name="profile_comments", schema=SCHEMA)
    op.drop_index("ix_comments_profile", table_name="profile_comments", schema=SCHEMA)
    op.drop_table("profile_comments", schema=SCHEMA)
    op.drop_index("ix_votes_target", table_name="votes", schema=SCHEMA)
    op.drop_table("votes", schema=SCHEMA)
