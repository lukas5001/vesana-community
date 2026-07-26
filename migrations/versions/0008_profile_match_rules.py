"""profile match_rules — community-aware discovery classification

Revision ID: 0008_profile_match_rules
Revises: 0007_instance_chosen_name
Create Date: 2026-05-31

Discovery on a Vesana instance classifies a found device by matching its signals
against profile ``match_rules``. Once the built-in profiles live in the hub
(community-first), discovery must fetch those rules from here. This column holds
the rules per community profile; the Vesana classifier pulls them via the API.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_profile_match_rules"
down_revision: str | None = "0007_instance_chosen_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.add_column(
        "community_profiles",
        sa.Column("match_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("community_profiles", "match_rules", schema=SCHEMA)
