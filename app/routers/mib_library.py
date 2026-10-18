"""JSON-API der MIB-Bibliothek.

Lesen ist öffentlich wie die Icon-Bibliothek: ausgeliefert werden
Schnittstellen-Fakten (OIDs, Namen, Typen, Wertelisten) — der Originaltext nur
bei erlaubender Lizenz. Instanzen rufen serverseitig, der Browser eines Kunden
spricht nie direkt mit dem Hub.

Veröffentlichen/Entfernen nur über die Admin-API (``AdminFlag``, wie die
übrigen ``/api/v1/admin/*``-Endpunkte) und mit Eintrag im Admin-Protokoll.

Vorschläge (``/api/v1/mib-submissions``): eine Instanz (Bearer-Token) reicht
das Verzeichnis einer selbst hochgeladenen MIB zur Prüfung ein; sichtbar wird
es erst, wenn ein Admin freigibt (``app/services/mib_submissions.py``).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.routers.admin_auth import client_ip
from app.routers.community_interactions import AdminFlag, CurrentInstance, DbDep
from app.routers.uploads import _require_admin
from app.services import admin_audit as audit
from app.services import mib_library as lib
from app.services import mib_submissions as subs

router = APIRouter(tags=["mib-library"])


class MibListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int


@router.get("/api/v1/mibs", response_model=MibListResponse)
def list_mibs(
    db: DbDep,
    q: str | None = Query(None, max_length=128),
    enterprise: str | None = Query(
        None, max_length=64, description="exakter Hersteller-Zweig 1.3.6.1.4.1.<n>"
    ),
    oid: str | None = Query(
        None, max_length=600, description="liefert Module, deren Hersteller-Zweig diese OID enthält"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> MibListResponse:
    rows = lib.search(db, q=q, enterprise=enterprise, oid=oid)
    return MibListResponse(
        items=[lib.summary(r) for r in rows[offset : offset + limit]], total=len(rows)
    )


@router.get("/api/v1/mibs/{module}")
def get_mib(
    module: str, request: Request, db: DbDep, revision: str | None = Query(None, max_length=32)
) -> Response:
    row = lib.get_module(db, module, revision)
    etag = f'"{row.sha256}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    body = {**lib.summary(row), "directory": row.directory}
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
    )


@router.get("/api/v1/mibs/{module}/source", response_class=PlainTextResponse)
def get_mib_source(
    module: str, db: DbDep, revision: str | None = Query(None, max_length=32)
) -> PlainTextResponse:
    row = lib.get_module(db, module, revision)
    if not row.license_allows_source or not row.source_text:
        raise HTTPException(status_code=404, detail="source text not available for this module")
    return PlainTextResponse(
        row.source_text,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{row.module}.mib"',
        },
    )


# ---- Kurator (Admin-API) ---------------------------------------------------


class PublishIn(BaseModel):
    directory: dict[str, Any]
    license: str = Field(min_length=1, max_length=200)
    license_allows_source: bool = False
    checked_at: date
    source_url: str | None = Field(default=None, max_length=1000)
    source_text: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


@router.get("/api/v1/admin/mibs", response_model=MibListResponse)
def admin_list_mibs(
    is_admin: AdminFlag, db: DbDep, q: str | None = Query(None, max_length=128)
) -> MibListResponse:
    _require_admin(is_admin)
    rows = lib.search(db, q=q, include_removed=True)
    return MibListResponse(items=[lib.summary(r) for r in rows], total=len(rows))


@router.post("/api/v1/admin/mibs", status_code=201)
def admin_publish_mib(
    payload: PublishIn, request: Request, is_admin: AdminFlag, db: DbDep
) -> dict[str, Any]:
    _require_admin(is_admin)
    admin = get_settings().COMMUNITY_ADMIN_USER
    row, neu = lib.publish(
        db,
        lib.PublishInput(
            directory=payload.directory,
            license=payload.license,
            license_allows_source=payload.license_allows_source,
            checked_at=payload.checked_at,
            source_url=payload.source_url,
            source_text=payload.source_text,
            notes=payload.notes,
        ),
        curator=admin,
    )
    audit.record(
        db,
        admin_user=admin,
        action="mib.publish",
        target_type="mib",
        target_id=f"{row.module}@{row.revision or '-'}",
        summary=f"{row.module} {row.revision or ''} ({row.object_count} objects, {row.license})",
        details={
            "new": neu,
            "license_allows_source": row.license_allows_source,
            "sha256": row.sha256,
        },
        ip=client_ip(request),
    )
    db.commit()
    return {**lib.summary(row), "created": neu}


@router.delete("/api/v1/admin/mibs/{module}")
def admin_remove_mib(
    module: str,
    request: Request,
    is_admin: AdminFlag,
    db: DbDep,
    revision: str | None = Query(None, max_length=32),
) -> dict[str, Any]:
    _require_admin(is_admin)
    rows = lib.remove(db, module, revision)
    admin = get_settings().COMMUNITY_ADMIN_USER
    audit.record(
        db,
        admin_user=admin,
        action="mib.remove",
        target_type="mib",
        target_id=f"{module}@{revision or '*'}",
        summary=f"{module}: {len(rows)} revision(s) removed",
        ip=client_ip(request),
    )
    db.commit()
    return {"removed": len(rows)}


# ---- Vorschläge (Instanz) --------------------------------------------------


class SubmissionIn(BaseModel):
    directory: dict[str, Any]
    license: str = Field(min_length=1, max_length=200)
    allows_source: bool = False
    source_url: str | None = Field(default=None, max_length=1000)
    source_text: str | None = None
    note: str | None = Field(default=None, max_length=4000)


@router.post("/api/v1/mib-submissions", status_code=201)
def submit_mib(payload: SubmissionIn, instance: CurrentInstance, db: DbDep) -> dict[str, Any]:
    row, duplicate = subs.submit(
        db,
        instance.uuid,
        subs.SubmitInput(
            directory=payload.directory,
            license=payload.license,
            allows_source=payload.allows_source,
            source_url=payload.source_url,
            source_text=payload.source_text,
            note=payload.note,
        ),
    )
    db.commit()
    return {**subs.to_instance_view(row), "duplicate": duplicate}


@router.get("/api/v1/mib-submissions")
def list_own_submissions(instance: CurrentInstance, db: DbDep) -> dict[str, Any]:
    rows = subs.list_for_instance(db, instance.uuid)
    return {"items": [subs.to_instance_view(r) for r in rows]}


@router.delete("/api/v1/mib-submissions/{submission_id}")
def withdraw_submission(submission_id: str, instance: CurrentInstance, db: DbDep) -> dict[str, Any]:
    row = subs.withdraw(db, instance.uuid, submission_id)
    db.commit()
    return subs.to_instance_view(row)


# ---- Vorschläge (Admin-API) ------------------------------------------------


class ApproveIn(BaseModel):
    license: str = Field(min_length=1, max_length=200)
    license_allows_source: bool = False
    source_url: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=4000)


class RejectIn(BaseModel):
    reason: str = Field(default="", max_length=2000)


@router.get("/api/v1/admin/mib-submissions")
def admin_list_submissions(
    is_admin: AdminFlag,
    db: DbDep,
    status: str | None = Query("pending", pattern="^(pending|approved|rejected|withdrawn|all)$"),
) -> dict[str, Any]:
    _require_admin(is_admin)
    from app.services import admin as admin_service

    rows = subs.list_for_review(db, status)
    names = admin_service.names_for(db, {r.instance_uuid for r in rows})
    return {"items": [subs.to_review_item(r, names) for r in rows]}


@router.post("/api/v1/admin/mib-submissions/{submission_id}/approve")
def admin_approve_submission(
    submission_id: str, payload: ApproveIn, request: Request, is_admin: AdminFlag, db: DbDep
) -> dict[str, Any]:
    _require_admin(is_admin)
    admin = get_settings().COMMUNITY_ADMIN_USER
    row, module, neu = subs.approve(
        db,
        submission_id,
        subs.ApproveInput(
            license=payload.license,
            license_allows_source=payload.license_allows_source,
            source_url=payload.source_url,
            notes=payload.notes,
        ),
        admin=admin,
    )
    subs.record_approve(db, admin, row, module, neu, ip=client_ip(request))
    db.commit()
    return {**subs.to_instance_view(row), "library": lib.summary(module)}


@router.post("/api/v1/admin/mib-submissions/{submission_id}/reject")
def admin_reject_submission(
    submission_id: str, payload: RejectIn, request: Request, is_admin: AdminFlag, db: DbDep
) -> dict[str, Any]:
    _require_admin(is_admin)
    admin = get_settings().COMMUNITY_ADMIN_USER
    row = subs.reject(db, submission_id, payload.reason, admin=admin)
    subs.record_reject(db, admin, row, ip=client_ip(request))
    db.commit()
    return subs.to_instance_view(row)
