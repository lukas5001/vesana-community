"""requires_ssh / requires_api_token — ehrlicher Verbindungsbedarf pro Profil

Revision ID: 0009_profile_connection_requirements
Revises: 0008_profile_match_rules
Create Date: 2026-07-26

Der Host-Assistent auf den Vesana-Instanzen zeigt pro Profilkarte, welche
Zugänge (SNMP / SSH / API-Token) man konfigurieren muss. Für Hub-Vorschläge
gab es diese Wahrheit bisher nicht — die requires_*-Metadaten aus dem Bundle
sind mager gepflegt. Ab jetzt leitet der Hub die Flags beim Upload aus den
BUNDLE-CHECKS ab (check_type snmp* / ssh* / http_json); diese Migration
ergänzt die zwei neuen Spalten und backfillt ALLE Profile aus der jeweils
aktuellen Bundle-Version (requires_snmp wird dabei nur ERGÄNZT, nie gesenkt).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_profile_connection_requirements"
down_revision: str | None = "0008_profile_match_rules"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_BACKFILL = """
UPDATE community_profiles p
SET requires_ssh = COALESCE(sub.ssh, FALSE),
    requires_api_token = COALESCE(sub.api, FALSE),
    requires_snmp = p.requires_snmp OR COALESCE(sub.snmp, FALSE)
FROM (
    SELECT v.profile_id,
           bool_or(c.value ->> 'check_type' LIKE 'ssh%%') AS ssh,
           bool_or(c.value ->> 'check_type' = 'http_json') AS api,
           bool_or(c.value ->> 'check_type' LIKE 'snmp%%') AS snmp
    FROM community_profile_versions v
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(v.bundle_json -> 'checks') = 'array'
                THEN v.bundle_json -> 'checks'
            ELSE '[]'::jsonb
        END
    ) AS c
    WHERE v.is_current
    GROUP BY v.profile_id
) AS sub
WHERE sub.profile_id = p.id
"""


def upgrade() -> None:
    op.add_column(
        "community_profiles",
        sa.Column("requires_ssh", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "community_profiles",
        sa.Column("requires_api_token", sa.Boolean(), server_default="false", nullable=False),
    )
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_column("community_profiles", "requires_api_token")
    op.drop_column("community_profiles", "requires_ssh")
