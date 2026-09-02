"""requires_api_token = „braucht API-Zugang" (eine Art, nicht zwei)

Revision ID: 0013_api_access_eine_art
Revises: 0012_device_api_flag
Create Date: 2026-09-02

0012 hat `requires_api_token` auf den `{api_token}`-Platzhalter verengt — das
war gegenüber dem alten „jeder http_json-Check" richtig (Sonos fragt eine
offene API ab), ging aber einen Schritt zu weit: ein Profil, dessen Checks sich
mit dem Geräte-KONTO anmelden (`auth: "device_api"`, OPNsense) oder über die
vSphere-API messen, braucht sehr wohl einen API-Zugang — nur eben Benutzer +
Geheimnis statt eines Tokens.

Die Instanz-Seite führt beides gerade zu EINER Zugangsart zusammen (Vesana
Migration 268: `api_user`/`api_password`, bei Proxmox mit `=` zusammengesetzt).
Der Hub spiegelt das: `requires_api_token` heißt „braucht API-Zugang".
`requires_device_api` bleibt als feinere Auskunft daneben — wer die zwei Fälle
unterscheiden will, kann es, muss aber nicht.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_api_access_eine_art"
down_revision: str | None = "0012_device_api_flag"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_BACKFILL = """
UPDATE community_profiles p
SET requires_api_token = COALESCE(sub.api_access, FALSE)
FROM (
    SELECT v.profile_id,
           bool_or(
               (c.value ->> 'check_config') LIKE '%%{api_token}%%'
               OR lower(c.value -> 'check_config' ->> 'auth') = 'device_api'
               OR c.value ->> 'check_type' = 'vsphere'
           ) AS api_access
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
    op.execute(_BACKFILL)


def downgrade() -> None:
    # Keine Rückabwicklung: der engere Stand von 0012 war die Regression.
    pass
