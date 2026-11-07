"""Bibliothek: signierte Hülle + Freigabe für den Stable-Kanal je Profil-Version

Revision ID: 0016_library_signing_channels
Revises: 0015_mib_submissions
Create Date: 2026-11-07

Der Hub wird zur Vesana-Bibliothek: ein schreibgeschützter, vom Hersteller
kuratierter, signierter Profil-Katalog. Je Version kommen zwei Spalten dazu:

* ``signed_envelope`` (JSONB, NULL) — die vom externen Signier-Werkzeug
  geschriebene Hülle ``{"format": "vesana-library-sig/1", "key_id", "sig",
  "payload"}``. Der Hub rechnet nie selbst Signaturen, er speichert und
  liefert nur aus. Ohne Hülle ist eine Version für KEINEN Kanal sichtbar.
* ``released_all_at`` (TIMESTAMPTZ, NULL) — ab wann der Stable-Kanal die
  Version sieht. Bestand wird mit ``created_at`` vorbelegt.

Rein additiv und wiederholbar (``IF NOT EXISTS`` / Backfill nur auf NULL).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_library_signing_channels"
down_revision: str | None = "0015_mib_submissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "community"
TABLE = f"{SCHEMA}.community_profile_versions"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS signed_envelope JSONB NULL")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS released_all_at TIMESTAMPTZ NULL")
    op.execute(f"UPDATE {TABLE} SET released_all_at = created_at WHERE released_all_at IS NULL")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS released_all_at")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS signed_envelope")
