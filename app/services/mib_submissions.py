"""MIB-Vorschläge: Instanzen reichen ein, ein Admin prüft und gibt frei.

EINE Stelle für den ganzen Weg eines Vorschlags — die Instanz-API, die
Admin-API und die Admin-Seiten rufen nur hier.

Regeln:
* Angenommen wird nur, was ``MibDirectory`` (Format 1) annimmt — dieselbe
  Prüfung wie beim Kurator. Der Originaltext ist nur zum Prüfen da.
* 🔒 Ein Vorschlag ist bis zur Freigabe UNSICHTBAR: keine öffentliche Liste,
  keine Bibliothek. Freigeben heißt ``mib_library.publish`` — dort greift die
  Lizenz-Linie (ohne Weitergabe-Erlaubnis gehen Text und Beschreibungen nie
  hinaus), und zwar mit der Lizenz, die der ADMIN festlegt, nicht mit der
  Angabe des Einsenders.
* 🔒 Nach der Entscheidung (oder dem Zurückziehen) werden Verzeichnis und
  Originaltext gelöscht; der Vorschlag behält nur Kopfdaten und Ergebnis.
* Jede Instanz hat höchstens ``MAX_PENDING_PER_INSTANCE`` offene Vorschläge;
  dasselbe Verzeichnis zweimal ergibt keinen zweiten Vorschlag.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.mib_directory import MibDirectory, enterprise_of
from app.models.mib_module import MibModule
from app.models.mib_submission import MibSubmission
from app.services import admin_audit as audit
from app.services import mib_library as lib
from app.services import notifications

MAX_PENDING_PER_INSTANCE = 20
#: So viele eigene Vorschläge liefert die Instanz-Liste (neueste zuerst).
OWN_LIST_LIMIT = 100
#: Für die Prüfseite: so viele Objekte zeigt die Vorschau.
PREVIEW_OBJECTS = 40
#: Kopf des Originaltexts auf der Prüfseite (Lizenz/Copyright steht oben).
SOURCE_HEAD_LINES = 80

_DECIDED = ("approved", "rejected", "withdrawn")


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="submission not found")


@dataclass
class SubmitInput:
    directory: dict[str, Any]
    license: str
    allows_source: bool = False
    source_url: str | None = None
    source_text: str | None = None
    note: str | None = None


def _validated(raw: dict[str, Any]) -> MibDirectory:
    try:
        size = len(json.dumps(raw).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise _bad("directory is not JSON-serializable") from exc
    if size > lib.MAX_DIRECTORY_BYTES:
        raise HTTPException(status_code=413, detail="directory too large")
    try:
        return MibDirectory.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise _bad(f"directory invalid at {where or '?'}: {first.get('msg', 'invalid')}") from exc


def _clean_url(value: str | None) -> str | None:
    url = (value or "").strip()[:1000]
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        raise _bad("source_url must be an http(s) URL")
    return url


def submit(db: Session, instance_uuid: str, data: SubmitInput) -> tuple[MibSubmission, bool]:
    """Legt einen Vorschlag an. → ``(vorschlag, schon_vorhanden)``.

    409 ``already_in_library``: dieselbe (Modul, Revision) liegt schon in der
    Bibliothek. 429: zu viele offene Vorschläge dieser Instanz."""
    license_claim = (data.license or "").strip()
    if not license_claim:
        raise _bad("license is required")
    directory = _validated(data.directory)
    if not directory.objects:
        raise _bad("directory has no objects")
    source_text = data.source_text if data.source_text and data.source_text.strip() else None
    if source_text is not None and len(source_text.encode("utf-8")) > lib.MAX_SOURCE_BYTES:
        raise HTTPException(status_code=413, detail="source text too large")
    note = (data.note or "").strip()[:4000] or None
    source_url = _clean_url(data.source_url)

    stored = directory.model_dump(mode="json")
    sha = hashlib.sha256(json.dumps(stored, sort_keys=True).encode("utf-8")).hexdigest()
    revision = (directory.revision or "").strip()

    in_library = db.execute(
        select(MibModule.id).where(
            MibModule.module == directory.module,
            MibModule.revision == revision,
            MibModule.removed_at.is_(None),
        )
    ).first()
    if in_library is not None:
        raise HTTPException(status_code=409, detail="already_in_library")

    same = db.execute(
        select(MibSubmission)
        .options(defer(MibSubmission.directory), defer(MibSubmission.source_text))
        .where(
            MibSubmission.instance_uuid == instance_uuid,
            MibSubmission.sha256 == sha,
            MibSubmission.status == "pending",
        )
    ).scalar_one_or_none()
    if same is not None:
        return same, True

    pending = db.execute(
        select(func.count())
        .select_from(MibSubmission)
        .where(
            MibSubmission.instance_uuid == instance_uuid,
            MibSubmission.status == "pending",
        )
    ).scalar_one()
    if pending >= MAX_PENDING_PER_INSTANCE:
        raise HTTPException(status_code=429, detail="too many pending submissions")

    row = MibSubmission(
        id=uuid.uuid4(),
        instance_uuid=instance_uuid,
        module=directory.module,
        revision=revision,
        enterprise=enterprise_of(directory),
        organization=directory.organization,
        directory=stored,
        object_count=len(directory.objects),
        descriptions_included=bool(directory.descriptions_included),
        source_text=source_text,
        sha256=sha,
        license_claim=license_claim[:200],
        allows_source_claim=bool(data.allows_source),
        source_url=source_url,
        note=note,
        status="pending",
    )
    db.add(row)
    db.flush()
    return row, False


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def to_instance_view(row: MibSubmission) -> dict[str, Any]:
    """Was die einsendende Instanz über ihren Vorschlag erfährt."""
    return {
        "id": str(row.id),
        "module": row.module,
        "revision": row.revision or None,
        "object_count": row.object_count,
        "status": row.status,
        "reason": row.reason if row.status == "rejected" else None,
        "created_at": _iso(row.created_at),
        "decided_at": _iso(row.reviewed_at),
    }


def list_for_instance(db: Session, instance_uuid: str) -> list[MibSubmission]:
    return list(
        db.execute(
            select(MibSubmission)
            .options(defer(MibSubmission.directory), defer(MibSubmission.source_text))
            .where(MibSubmission.instance_uuid == instance_uuid)
            .order_by(MibSubmission.created_at.desc())
            .limit(OWN_LIST_LIMIT)
        )
        .scalars()
        .all()
    )


def _parse_id(submission_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(submission_id))
    except ValueError as exc:
        raise _not_found() from exc


def get(db: Session, submission_id: str) -> MibSubmission:
    row = db.get(MibSubmission, _parse_id(submission_id))
    if row is None:
        raise _not_found()
    return row


def _close(row: MibSubmission, status: str, *, by: str | None, reason: str | None) -> None:
    row.status = status
    row.reason = reason
    row.reviewed_by = by
    row.reviewed_at = datetime.now(UTC)
    # Aufbewahrt bleibt nur, was die Bibliothek selbst trägt.
    row.directory = None
    row.source_text = None


def withdraw(db: Session, instance_uuid: str, submission_id: str) -> MibSubmission:
    """Die Instanz zieht ihren offenen Vorschlag zurück. Fremd ⇒ 404."""
    row = get(db, submission_id)
    if row.instance_uuid != instance_uuid:
        raise _not_found()
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="submission already decided")
    _close(row, "withdrawn", by=None, reason=None)
    db.flush()
    return row


def _require_pending(row: MibSubmission) -> None:
    if row.status != "pending" or row.directory is None:
        raise HTTPException(status_code=409, detail="submission already decided")


@dataclass
class ApproveInput:
    license: str
    license_allows_source: bool
    source_url: str | None = None
    notes: str | None = None


def approve(
    db: Session, submission_id: str, data: ApproveInput, *, admin: str
) -> tuple[MibSubmission, MibModule, bool]:
    """Freigeben = über DENSELBEN Dienst wie der Kurator veröffentlichen.
    → ``(vorschlag, modul, neu)``."""
    row = get(db, submission_id)
    _require_pending(row)
    module, neu = lib.publish(
        db,
        lib.PublishInput(
            directory=dict(row.directory or {}),
            license=data.license,
            license_allows_source=data.license_allows_source,
            checked_at=date.today(),
            source_url=_clean_url(data.source_url),
            source_text=row.source_text,
            notes=(data.notes or "").strip()[:4000] or None,
        ),
        curator=admin,
    )
    _close(row, "approved", by=admin, reason=None)
    notifications.enqueue(
        db,
        recipient_uuid=row.instance_uuid,
        actor_uuid=None,
        type="mib_approved",
        payload={"module": row.module, "revision": row.revision or None},
    )
    db.flush()
    return row, module, neu


def reject(db: Session, submission_id: str, reason: str, *, admin: str) -> MibSubmission:
    row = get(db, submission_id)
    _require_pending(row)
    text = (reason or "").strip()[:2000] or "rejected by admin"
    _close(row, "rejected", by=admin, reason=text)
    notifications.enqueue(
        db,
        recipient_uuid=row.instance_uuid,
        actor_uuid=None,
        type="mib_rejected",
        payload={"module": row.module, "revision": row.revision or None, "reason": text},
    )
    db.flush()
    return row


def pending_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(MibSubmission).where(MibSubmission.status == "pending")
        ).scalar_one()
    )


def list_for_review(
    db: Session, status: str | None = "pending", limit: int = 200
) -> list[MibSubmission]:
    stmt = select(MibSubmission).options(
        defer(MibSubmission.directory), defer(MibSubmission.source_text)
    )
    if status and status != "all":
        stmt = stmt.where(MibSubmission.status == status)
    return list(
        db.execute(stmt.order_by(MibSubmission.created_at.desc()).limit(limit)).scalars().all()
    )


def to_review_item(row: MibSubmission, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        **to_instance_view(row),
        "enterprise": row.enterprise,
        "organization": row.organization,
        "descriptions_included": row.descriptions_included,
        "has_source": bool(row.has_source_text),
        "license_claim": row.license_claim,
        "allows_source_claim": row.allows_source_claim,
        "source_url": row.source_url,
        "note": row.note,
        "instance_uuid": row.instance_uuid,
        "instance_name": (names or {}).get(row.instance_uuid, row.instance_uuid[:8]),
        "reviewed_by": row.reviewed_by,
        "reason": row.reason,
    }


def inspection(db: Session, row: MibSubmission) -> dict[str, Any]:
    """Alles, was der Admin zum Prüfen braucht: Kopf des Originaltexts (dort
    steht die Lizenz), eine Objekt-Vorschau und was die Bibliothek schon hat."""
    directory = row.directory or {}
    objects = list(directory.get("objects") or [])
    head = None
    if row.source_text:
        lines = row.source_text.splitlines()
        head = "\n".join(lines[:SOURCE_HEAD_LINES])
    existing = [
        lib.summary(m)
        for m in db.execute(
            select(MibModule)
            .options(defer(MibModule.directory), defer(MibModule.source_text))
            .where(MibModule.module == row.module)
        )
        .scalars()
        .all()
    ]
    existing.sort(key=lambda m: lib.revision_key(m.get("revision") or ""), reverse=True)
    kinds: dict[str, int] = {}
    for o in objects:
        k = o.get("kind") or "?"
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "preview": objects[:PREVIEW_OBJECTS],
        "kinds": kinds,
        "imports": list(directory.get("imports") or []),
        "source_head": head,
        "source_lines": len(row.source_text.splitlines()) if row.source_text else 0,
        "existing": existing,
        "revision_date": lib.revision_date(row.revision),
    }


def record_approve(
    db: Session, admin: str, row: MibSubmission, module: MibModule, neu: bool, *, ip: str | None
) -> None:
    """Admin-Protokoll einer Freigabe — API und Admin-Seite schreiben gleich."""
    audit.record(
        db,
        admin_user=admin,
        action="mib.submission.approve",
        target_type="mib",
        target_id=f"{module.module}@{module.revision or '-'}",
        summary=(
            f"{module.module} {module.revision or ''} "
            f"({module.object_count} objects, {module.license})"
        ),
        details={
            "submission": str(row.id),
            "instance": row.instance_uuid,
            "new": neu,
            "license_allows_source": module.license_allows_source,
            "license_claim": row.license_claim,
            "sha256": module.sha256,
        },
        ip=ip,
    )


def record_reject(db: Session, admin: str, row: MibSubmission, *, ip: str | None) -> None:
    audit.record(
        db,
        admin_user=admin,
        action="mib.submission.reject",
        target_type="mib",
        target_id=f"{row.module}@{row.revision or '-'}",
        summary=f"{row.module} {row.revision or ''}: {row.reason}",
        details={"submission": str(row.id), "instance": row.instance_uuid},
        ip=ip,
    )
