"""Kurator-Befehl: MIB-Verzeichnisse in die Bibliothek legen.

    docker compose exec -T community python -m app.publish_mib --curator lukas < eintraege.json

`eintraege.json` = Liste von Objekten mit denselben Feldern wie
``POST /api/v1/admin/mibs`` (directory, license, license_allows_source,
checked_at, source_url, source_text, notes). Läuft über DENSELBEN Dienst
(``app.services.mib_library.publish``: Formatprüfung, Lizenz-Linie) und
schreibt je Modul einen Eintrag ``mib.publish`` ins Admin-Protokoll.

Warum ein Befehl neben der API: der Admin hat einen zweiten Faktor, die
JSON-API nimmt dann nur ein Bearer-Token — für eine einmalige Kuration am
Server braucht es keins.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from fastapi import HTTPException

from app.db import SessionLocal
from app.services import admin_audit as audit
from app.services import mib_library as lib


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.publish_mib")
    ap.add_argument("--curator", required=True, help="Name im Admin-Protokoll")
    ap.add_argument("--dry-run", action="store_true", help="prüfen, nichts speichern")
    args = ap.parse_args(argv)
    try:
        eintraege = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"Eingabe ist kein JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(eintraege, list):
        print("Eingabe muss eine Liste sein", file=sys.stderr)
        return 2
    fehler = 0
    with SessionLocal() as db:
        for e in eintraege:
            name = (e.get("directory") or {}).get("module", "?")
            try:
                row, neu = lib.publish(
                    db,
                    lib.PublishInput(
                        directory=e["directory"],
                        license=e["license"],
                        license_allows_source=bool(e.get("license_allows_source")),
                        checked_at=date.fromisoformat(e["checked_at"]),
                        source_url=e.get("source_url"),
                        source_text=e.get("source_text"),
                        notes=e.get("notes"),
                    ),
                    curator=f"cli:{args.curator}",
                )
            except (HTTPException, KeyError, ValueError) as exc:
                fehler += 1
                detail = exc.detail if isinstance(exc, HTTPException) else repr(exc)
                print(f"FEHLER {name}: {detail}", file=sys.stderr)
                continue
            audit.record(
                db,
                admin_user=f"cli:{args.curator}",
                action="mib.publish",
                target_type="mib",
                target_id=f"{row.module}@{row.revision or '-'}",
                summary=f"{row.module} {row.revision or ''} ({row.object_count} objects)",
                details={
                    "new": neu,
                    "license": row.license,
                    "license_allows_source": row.license_allows_source,
                    "sha256": row.sha256,
                },
            )
            was = "neu" if neu else "ersetzt"
            print(f"{was}: {row.module} {row.revision or '-'} ({row.object_count} Objekte)")
        if args.dry_run:
            db.rollback()
            print("Probelauf — nichts gespeichert.")
        else:
            db.commit()
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
