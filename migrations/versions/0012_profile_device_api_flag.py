"""requires_device_api + ehrlicher requires_api_token

Revision ID: 0012_device_api_flag
Revises: 0011_admin_security
Create Date: 2026-09-02

Zwei Dinge, eine Wurzel — der Bedarf steht in der check_config, nicht am
Check-TYP:

1. **Neu `requires_device_api`.** Seit Vesana PR #1495 kann ein `http_json`-
   Check sich mit dem Geräte-API-KONTO des Hosts anmelden (`auth:
   "device_api"`, Benutzer + Passwort statt Token) — das OPNsense-Profil v4
   lebt davon. Der Hub kannte diesen Bedarf nicht, also sagte der
   Host-Assistent der Instanzen nichts, und der Nutzer legte den Host ohne
   Konto an.

2. **`requires_api_token` war zu grob.** Abgeleitet wurde
   `check_type == 'http_json'` ODER `{api_token}` — jeder Check gegen eine
   UNAUTHENTIFIZIERTE Geräte-API (Sonos auf :1400) bekam damit eine
   API-Token-Pille. Dieselbe Korrektur hat die Instanz-Seite 08/2026 schon
   gemacht (`lib/checkNeeds.ts`); der Hub zog nach. Jetzt zählt allein der
   `{api_token}`-Platzhalter.

Beides wird für ALLE Profile aus der jeweils aktuellen Bundle-Version
nachgerechnet.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_device_api_flag"
down_revision: str | None = "0011_admin_security"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_BACKFILL = """
UPDATE community_profiles p
SET requires_device_api = COALESCE(sub.device_api, FALSE),
    requires_api_token = COALESCE(sub.api_token, FALSE)
FROM (
    SELECT v.profile_id,
           bool_or(lower(c.value -> 'check_config' ->> 'auth') = 'device_api') AS device_api,
           bool_or((c.value ->> 'check_config') LIKE '%%{api_token}%%') AS api_token
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
        sa.Column("requires_device_api", sa.Boolean(), server_default="false", nullable=False),
    )
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_column("community_profiles", "requires_device_api")
