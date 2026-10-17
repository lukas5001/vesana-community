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
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text
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


def restore(db: Session, module: str) -> list[MibModule]:
    """Entfernte Revisionen eines Moduls wieder ausliefern (Kurator-Rückweg)."""
    if not NAME_RE.match(module or ""):
        raise HTTPException(status_code=404, detail="MIB module not found")
    rows = list(
        db.execute(
            select(MibModule).where(MibModule.module == module, MibModule.removed_at.is_not(None))
        ).scalars()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="MIB module not found")
    for r in rows:
        r.removed_at = None
    db.flush()
    return rows


def admin_overview(db: Session, q: str | None = None) -> dict[str, Any]:
    """Kurator-Sicht: alle Module (auch entfernte), wer sie nennt, und was fehlt —
    MIBs, die Profile nennen, ohne dass die Bibliothek sie hat, und
    SNMP-Profile ohne einen einzigen MIB-Verweis."""
    from app.services.profiles import profiles_by_mib, snmp_profiles_without_mibs

    users = profiles_by_mib(db)
    rows = search(db, q=q, include_removed=True)
    live = {r.module for r in search(db)}
    items = []
    for r in rows:
        items.append(
            {
                **summary(r),
                "revision_date": revision_date(r.revision),
                "curator": r.curator,
                "used_by": users.get(r.module, []),
            }
        )
    return {
        "items": items,
        "total": len(live),
        "objects": sum(i["object_count"] for i in items if not i["removed"]),
        "with_descriptions": sum(
            1 for i in items if i["descriptions_included"] and not i["removed"]
        ),
        "removed": sum(1 for i in items if i["removed"]),
        "missing": sorted(
            ({"module": m, "used_by": u} for m, u in users.items() if m not in live),
            key=lambda x: x["module"].lower(),
        ),
        "snmp_without_mibs": snmp_profiles_without_mibs(db),
    }


def summary(row: MibModule) -> dict[str, Any]:
    return {
        "module": row.module,
        "revision": row.revision or None,
        "enterprise": row.enterprise,
        "organization": row.organization,
        "object_count": row.object_count,
        "descriptions_included": row.descriptions_included,
        # Herunterladbar nur, wenn die Lizenz es erlaubt UND der Text vorliegt —
        # sonst verspräche die Liste einen Download, den ``/source`` mit 404 beantwortet.
        "has_source": bool(row.license_allows_source and row.has_source_text),
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


# ---- Hub-Seiten (Lesen für Menschen) ---------------------------------------

#: Objekte je Seite auf der Modulseite (PowerNet-MIB hat über 8000).
OBJECTS_PER_PAGE = 200
#: Treffer der Objektsuche über alle Module.
OBJECT_SEARCH_LIMIT = 60
#: Wertelisten werden in der Tabelle gekürzt; die volle Liste steht aufgeklappt.
ENUM_PREVIEW = 6

#: Filter „Art" der Modulseite → Objektarten des Verzeichnisses.
KIND_FILTERS: dict[str, tuple[str, ...]] = {
    "values": ("scalar", "column"),
    "scalar": ("scalar",),
    "column": ("column",),
    "table": ("table", "entry"),
    "notification": ("notification",),
}


def revision_date(revision: str | None) -> str | None:
    """``202607070000Z`` → ``2026-07-07`` (zweistellige Jahre wie ``revision_key``)."""
    r = revision_key(revision or "")
    if len(r) >= 8 and r[:8].isdigit():
        return f"{r[:4]}-{r[4:6]}-{r[6:8]}"
    return None


def _type_label(obj: dict[str, Any]) -> str | None:
    return obj.get("tc_name") or obj.get("syntax")


def _view_object(obj: dict[str, Any]) -> dict[str, Any]:
    enum = obj.get("enum") or []
    return {
        "name": obj.get("name"),
        "oid": obj.get("oid"),
        "kind": obj.get("kind"),
        "type": _type_label(obj),
        "units": obj.get("units"),
        "access": obj.get("access"),
        "status": obj.get("status"),
        "description": obj.get("description"),
        "enum": [{"value": v, "label": lbl} for v, lbl in enum],
        "enum_preview": [{"value": v, "label": lbl} for v, lbl in enum[:ENUM_PREVIEW]],
        "enum_more": max(0, len(enum) - ENUM_PREVIEW),
    }


@dataclass
class ObjectPage:
    items: list[dict[str, Any]]
    total: int
    total_all: int
    page: int
    pages: int
    kind_counts: dict[str, int]


def object_page(
    directory: dict[str, Any] | None,
    *,
    q: str | None = None,
    kind: str | None = None,
    page: int = 1,
    per_page: int = OBJECTS_PER_PAGE,
) -> ObjectPage:
    """Objekte eines Verzeichnisses für die Modulseite: gefiltert (Name/OID,
    Art) und seitenweise. Reine Funktion — das Verzeichnis ist geprüftes JSON."""
    objects = [o for o in (directory or {}).get("objects") or [] if isinstance(o, dict)]
    kind_counts: dict[str, int] = {}
    for key, kinds in KIND_FILTERS.items():
        kind_counts[key] = sum(1 for o in objects if o.get("kind") in kinds)
    selected = objects
    kinds = KIND_FILTERS.get(kind or "")
    if kinds:
        selected = [o for o in selected if o.get("kind") in kinds]
    term = (q or "").strip()
    if term:
        low = term.lower()
        oid = _norm_oid(term) if _OID_PREFIX_RE.match(_norm_oid(term)) else ""
        selected = [
            o
            for o in selected
            if low in str(o.get("name", "")).lower()
            or (oid and (o.get("oid") == oid or str(o.get("oid", "")).startswith(oid + ".")))
        ]
    total = len(selected)
    pages = max(1, -(-total // per_page))
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return ObjectPage(
        items=[_view_object(o) for o in selected[start : start + per_page]],
        total=total,
        total_all=len(objects),
        page=page,
        pages=pages,
        kind_counts=kind_counts,
    )


def search_objects(db: Session, term: str, *, limit: int = OBJECT_SEARCH_LIMIT) -> list[dict]:
    """Objekte über alle Module finden (Name enthält ``term`` oder OID beginnt
    damit) — beantwortet „in welcher MIB steht upsAdvBatteryCapacity?".

    Nur die jeweils NEUESTE nicht entfernte Revision eines Moduls."""
    term = (term or "").strip()
    if len(term) < 3:
        return []
    ids = [str(r.id) for r in search(db)]
    if not ids:
        return []
    oid = _norm_oid(term)
    rows = db.execute(
        text(
            """
            SELECT m.module, o->>'name' AS name, o->>'oid' AS oid, o->>'kind' AS kind,
                   COALESCE(o->>'tc_name', o->>'syntax') AS type
              FROM community.mib_modules m
              CROSS JOIN LATERAL jsonb_array_elements(m.directory->'objects') o
             WHERE m.id = ANY(CAST(:ids AS uuid[]))
               AND (o->>'name' ILIKE :like
                    OR (:oid <> '' AND (o->>'oid' = :oid OR o->>'oid' LIKE :oid_prefix)))
             ORDER BY (lower(o->>'name') = lower(:term)) DESC,
                      (o->>'name' ILIKE :starts) DESC,
                      length(o->>'name'), o->>'name', m.module
             LIMIT :limit
            """
        ),
        {
            "ids": ids,
            "like": "%" + _like_escape(term) + "%",
            "starts": _like_escape(term) + "%",
            "term": term,
            "oid": oid if _OID_PREFIX_RE.match(oid) else "",
            "oid_prefix": (oid + ".%") if _OID_PREFIX_RE.match(oid) else "",
            "limit": limit,
        },
    ).all()
    return [dict(r._mapping) for r in rows]


_OID_PREFIX_RE = re.compile(r"^[0-9]+(\.[0-9]+){1,127}$")


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
