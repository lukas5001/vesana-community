"""q&a portal: questions, answers

Revision ID: 0004_qa_portal
Revises: 0003_voting_comments
Create Date: 2026-05-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_qa_portal"
down_revision: str | None = "0003_voting_comments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title_text", sa.String(length=200), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("instance_uuid", sa.String(length=64), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("vote_score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("answer_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "is_closed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("duplicate_of_id", sa.String(length=36), nullable=True),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column(
            "is_vesana_team",
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
        sa.PrimaryKeyConstraint("id", name="pk_questions"),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["community.community_profiles.id"],
            name="fk_questions_profile",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"],
            ["community.questions.id"],
            name="fk_questions_dup",
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_questions_is_closed", "questions", ["is_closed"], schema=SCHEMA)
    op.create_index("ix_questions_instance_uuid", "questions", ["instance_uuid"], schema=SCHEMA)
    op.create_index("ix_questions_profile_id", "questions", ["profile_id"], schema=SCHEMA)

    op.create_table(
        "answers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("instance_uuid", sa.String(length=64), nullable=False),
        sa.Column("vote_score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "is_accepted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_vesana_team",
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
        sa.PrimaryKeyConstraint("id", name="pk_answers"),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["community.questions.id"],
            name="fk_answers_question",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_answers_question_id", "answers", ["question_id"], schema=SCHEMA)
    # At most one accepted answer per question (partial unique index).
    op.create_index(
        "uq_answers_one_accepted",
        "answers",
        ["question_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_accepted"),
    )


def downgrade() -> None:
    op.drop_index("uq_answers_one_accepted", table_name="answers", schema=SCHEMA)
    op.drop_index("ix_answers_question_id", table_name="answers", schema=SCHEMA)
    op.drop_table("answers", schema=SCHEMA)
    op.drop_index("ix_questions_profile_id", table_name="questions", schema=SCHEMA)
    op.drop_index("ix_questions_instance_uuid", table_name="questions", schema=SCHEMA)
    op.drop_index("ix_questions_is_closed", table_name="questions", schema=SCHEMA)
    op.drop_table("questions", schema=SCHEMA)
