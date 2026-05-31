"""instance chosen display name (community-side, SSO-independent)

Revision ID: 0007_instance_chosen_name
Revises: 0006_community_events
Create Date: 2026-05-31

The SSO login JWT overwrites ``instances.display_name`` on every sign-in, so a
name the user picks on the community site would be lost. ``chosen_name`` is a
separate, nullable column that SSO never touches; the effective display name is
``chosen_name or display_name``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_instance_chosen_name"
down_revision: str | None = "0006_community_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"


def upgrade() -> None:
    op.add_column(
        "instances",
        sa.Column("chosen_name", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("instances", "chosen_name", schema=SCHEMA)
