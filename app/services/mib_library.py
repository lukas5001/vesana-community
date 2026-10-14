"""MIB-Bibliothek: Veröffentlichen (Kurator), Suchen und Ausliefern.

EINE Stelle für alles, was ein Modul in die Bibliothek bringt oder herausgibt —
die öffentliche API und die Admin-API rufen nur hier.

Regeln:
* Gespeichert wird nur, was ``MibDirectory`` (Format 1) annimmt.
* 🔒 Lizenz-Linie: erlaubt die Lizenz die Weitergabe NICHT, werden Originaltext
  UND alle DESCRIPTION-Texte vor dem Speichern entfernt — auch wenn der
  Kurator sie mitschickt. Die Instanz zeigt dann „Original-MIB hochladen, um
  die Beschreibung zu sehen".
* Ausgeliefert wird je Modul die NEUESTE nicht entfernte Revision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from app.mib_directory import NAME_RE, MibDirectory, enterprise_of
from app.models.mib_module import MibModule

MAX_DIRECTORY_BYTES = 12 * 1024 * 1024
MAX_SOURCE_BYTES = 6 * 1024 * 1024


def revision_key(revision: str) -> str:
    """Sortierschlüssel einer REVISION: SMIv1/v2 erlauben ``YYMMDDHHMMZ``
    (11 Zeichen, 20. Jahrhundert) und ``YYYYMMDDHHMMZ`` (13). Ohne Angleichung
    sortierte ``9901010000Z`` hinter ``202001010000Z``."""
    r = (revision or "").strip().upper()
    if len(r) == 11 and r.endswith("Z") and r[:10].isdigit():
        return "19" + r
    return r


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


@dataclass
class PublishInput:
    directory: dict[str, Any]
    license: str
    license_allows_source: bool
    checked_at: date
    source_url: str | None = None
    source_text: str | None = None
    notes: str | None = None


def _strip_descriptions(directory: MibDirectory) -> MibDirectory:
    objs = [o.model_copy(update={"description": None}) for o in directory.objects]
    return directory.model_copy(update={"objects": objs, "descriptions_included": False})


def publish(db: Session, data: PublishInput, *, curator: str) -> tuple[MibModule, bool]:
    """Legt ein Modul an oder ersetzt dieselbe (Modul, Revision). Liefert
    ``(zeile, neu)``."""
    if not data.license or not data.license.strip():
        raise _bad("license is required")
    try:
        raw = json.dumps(data.directory)
    except (TypeError, ValueError) as exc:
        raise _bad("directory is not JSON-serializable") from exc
    if len(raw.encode("utf-8")) > MAX_DIRECTORY_BYTES:
        raise HTTPException(status_code=413, detail="directory too large")
    try:
        directory = MibDirectory.model_validate(data.directory)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise _bad(f"directory invalid at {where or '?'}: {first.get('msg', 'invalid')}") from exc

    source_text = data.source_text
    if not data.license_allows_source:
        directory = _strip_descriptions(directory)
        source_text = None
    if source_text is not None and len(source_text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise HTTPException(status_code=413, detail="source text too large")

    stored = directory.model_dump(mode="json")
    sha = hashlib.sha256(json.dumps(stored, sort_keys=True).encode("utf-8")).hexdigest()
    revision = (directory.revision or "").strip()

    row = db.execute(
        select(MibModule).where(
            MibModule.module == directory.module, MibModule.revision == revision
        )
    ).scalar_one_or_none()
    neu = row is None
    if row is None:
        row = MibModule(module=directory.module, revision=revision)
        db.add(row)
    row.enterprise = enterprise_of(directory)
    row.organization = directory.organization
    row.directory = stored
    row.object_count = len(directory.objects)
    row.descriptions_included = bool(directory.descriptions_included and data.license_allows_source)
    row.source_text = source_text
    row.sha256 = sha
    row.license = data.license.strip()[:200]
    row.license_allows_source = bool(data.license_allows_source)
    row.source_url = (data.source_url or "").strip()[:1000] or None
    row.checked_at = data.checked_at
    row.curator = curator[:128]
    row.notes = data.notes
    row.removed_at = None
    db.flush()
    return row, neu


def _latest_per_module(rows: list[MibModule]) -> list[MibModule]:
    best: dict[str, MibModule] = {}
    for r in rows:
        cur = best.get(r.module)
        if cur is None or revision_key(r.revision) > revision_key(cur.revision):
            best[r.module] = r
    return sorted(best.values(), key=lambda r: r.module.lower())


def _norm_oid(oid: str | None) -> str:
    return (oid or "").strip().strip(".")


def search(
    db: Session,
    *,
    q: str | None = None,
    enterprise: str | None = None,
    oid: str | None = None,
    include_removed: bool = False,
) -> list[MibModule]:
    """Neueste Revision je Modul. ``enterprise`` = exakter Hersteller-Zweig,
    ``oid`` = jede OID darunter (z. B. die sysObjectID eines Geräts)."""
    stmt = select(MibModule).options(defer(MibModule.directory), defer(MibModule.source_text))
    if not include_removed:
        stmt = stmt.where(MibModule.removed_at.is_(None))
    rows = list(db.execute(stmt).scalars().all())
    latest = _latest_per_module(rows)
    if enterprise:
        ent = _norm_oid(enterprise)
        latest = [r for r in latest if r.enterprise == ent]
    if oid:
        o = _norm_oid(oid)
        latest = [
            r
            for r in latest
            if r.enterprise and (o == r.enterprise or o.startswith(r.enterprise + "."))
        ]
    if q and q.strip():
        term = q.strip().lower()
        latest = [
            r
            for r in latest
            if term in r.module.lower()
            or term in (r.organization or "").lower()
            or term == (r.enterprise or "")
        ]
        latest.sort(key=lambda r: (0 if r.module.lower().startswith(term) else 1, r.module.lower()))
    return latest


def get_module(db: Session, module: str, revision: str | None = None) -> MibModule:
    if not NAME_RE.match(module or ""):
        raise HTTPException(status_code=404, detail="MIB module not found")
    stmt = select(MibModule).where(MibModule.module == module, MibModule.removed_at.is_(None))
    if revision is not None:
        stmt = stmt.where(MibModule.revision == revision.strip())
    rows = list(db.execute(stmt).scalars().all())
    if not rows:
        raise HTTPException(status_code=404, detail="MIB module not found")
    return max(rows, key=lambda r: revision_key(r.revision))


def remove(db: Session, module: str, revision: str | None) -> list[MibModule]:
    stmt = select(MibModule).where(MibModule.module == module, MibModule.removed_at.is_(None))
    if revision is not None:
        stmt = stmt.where(MibModule.revision == revision.strip())
    rows = list(db.execute(stmt).scalars().all())
    if not rows:
        raise HTTPException(status_code=404, detail="MIB module not found")
    now = datetime.now(UTC)
    for r in rows:
        r.removed_at = now
    db.flush()
    return rows


def summary(row: MibModule) -> dict[str, Any]:
    return {
        "module": row.module,
        "revision": row.revision or None,
        "enterprise": row.enterprise,
        "organization": row.organization,
        "object_count": row.object_count,
        "descriptions_included": row.descriptions_included,
        "has_source": bool(row.license_allows_source),
        "license": row.license,
        "source_url": row.source_url,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "sha256": row.sha256,
        "removed": row.removed_at is not None,
    }


def mib_refs_of_bundle(bundle: Any) -> list[dict[str, str | None]]:
    """Die `mibs`-Verweise eines (bereits geprüften) Bundles; Altbestand ohne
    das Feld ⇒ leer."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("mibs"), list):
        return []
    out: list[dict[str, str | None]] = []
    for ref in bundle["mibs"]:
        if isinstance(ref, dict) and isinstance(ref.get("module"), str):
            rev = ref.get("revision")
            out.append({"module": ref["module"], "revision": rev if isinstance(rev, str) else None})
    return out
