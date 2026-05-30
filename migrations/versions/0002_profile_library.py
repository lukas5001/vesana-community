"""profile library: community_profiles + community_profile_versions

Revision ID: 0002_profile_library
Revises: 0001_init
Create Date: 2026-05-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_profile_library"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "community_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description_md", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("vendor", sa.String(length=128), nullable=True),
        sa.Column("icon", sa.String(length=128), nullable=True),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column(
            "approved",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("vesana_min_version", sa.String(length=64), nullable=True),
        sa.Column(
            "requires_agent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "requires_collector",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "requires_snmp",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("uploader_instance_uuid", sa.String(length=64), nullable=True),
        sa.Column(
            "download_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "import_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String()),
            nullable=True,
        ),
        sa.Column("latest_version_id", sa.String(length=36), nullable=True),
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
        sa.Column(
            "is_removed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_community_profiles"),
        schema="community",
    )

    op.create_table(
        "community_profile_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("version_tag", sa.String(length=64), nullable=False),
        sa.Column("bundle_json", postgresql.JSONB(), nullable=False),
        sa.Column("changelog_md", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["community.community_profiles.id"],
            name="fk_community_profile_versions_profile_id_community_profiles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_community_profile_versions"),
        sa.UniqueConstraint(
            "profile_id",
            "version_tag",
            name="uq_community_profile_versions_profile_id",
        ),
        schema="community",
    )

    # Circular FK: community_profiles.latest_version_id ->
    # community_profile_versions.id. Created after both tables exist.
    op.create_foreign_key(
        "fk_community_profiles_latest_version_id",
        "community_profiles",
        "community_profile_versions",
        ["latest_version_id"],
        ["id"],
        source_schema="community",
        referent_schema="community",
        ondelete="SET NULL",
        use_alter=True,
    )

    op.create_index(
        "ix_community_community_profiles_tier",
        "community_profiles",
        ["tier"],
        schema="community",
    )
    op.create_index(
        "ix_community_community_profiles_category",
        "community_profiles",
        ["category"],
        schema="community",
    )
    op.create_index(
        "ix_community_community_profiles_vendor",
        "community_profiles",
        ["vendor"],
        schema="community",
    )
    op.create_index(
        "ix_community_community_profiles_approved",
        "community_profiles",
        ["approved"],
        schema="community",
    )
    op.create_index(
        "ix_community_community_profiles_is_removed",
        "community_profiles",
        ["is_removed"],
        schema="community",
    )
    op.create_index(
        "ix_community_community_profile_versions_profile_id",
        "community_profile_versions",
        ["profile_id"],
        schema="community",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_community_community_profile_versions_profile_id",
        table_name="community_profile_versions",
        schema="community",
    )
    op.drop_index(
        "ix_community_community_profiles_is_removed",
        table_name="community_profiles",
        schema="community",
    )
    op.drop_index(
        "ix_community_community_profiles_approved",
        table_name="community_profiles",
        schema="community",
    )
    op.drop_index(
        "ix_community_community_profiles_vendor",
        table_name="community_profiles",
        schema="community",
    )
    op.drop_index(
        "ix_community_community_profiles_category",
        table_name="community_profiles",
        schema="community",
    )
    op.drop_index(
        "ix_community_community_profiles_tier",
        table_name="community_profiles",
        schema="community",
    )
    op.drop_constraint(
        "fk_community_profiles_latest_version_id",
        "community_profiles",
        type_="foreignkey",
        schema="community",
    )
    op.drop_table("community_profile_versions", schema="community")
    op.drop_table("community_profiles", schema="community")
