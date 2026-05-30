"""Community admin JSON API (C8).

Every endpoint here is gated by the C3 ``AdminFlag`` seam (the
``X-Admin-Authorization`` Basic header): each handler calls ``_require_admin``
and raises 401 when the header is missing or invalid. These coexist with the
HTML admin pages (``app.routers.admin_pages``), which use ``require_admin``
(browser Basic-auth prompt) + form posts; this JSON API is for programmatic use.

The review-queue endpoints (``/api/v1/admin/review-queue`` + approve/reject)
already live in ``app.routers.uploads`` and are NOT redefined here.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.community_interactions import AdminFlag, DbDep
from app.routers.uploads import _require_admin
from app.schemas.admin import (
    AdminStats,
    BlockIn,
    InstanceItem,
    ReportItem,
    ResolveReportIn,
)
from app.services import admin as admin_service

router = APIRouter(tags=["admin"])


# ---- Moderation: reports ---------------------------------------------------


@router.get("/api/v1/admin/reports", response_model=list[ReportItem])
def list_reports(
    is_admin: AdminFlag,
    db: DbDep,
    status: str = "open",
) -> list[ReportItem]:
    """List moderation reports (``?status=open`` default, ``all``, or a status)."""
    _require_admin(is_admin)
    return admin_service.list_reports(db, status)


@router.post("/api/v1/admin/reports/{report_id}/resolve")
def resolve_report(
    report_id: str,
    payload: ResolveReportIn,
    is_admin: AdminFlag,
    db: DbDep,
) -> dict[str, str]:
    _require_admin(is_admin)
    report = admin_service.resolve_report(db, report_id, payload.action)
    db.commit()
    return {"status": report.status}


# ---- Instances -------------------------------------------------------------


@router.get("/api/v1/admin/instances", response_model=list[InstanceItem])
def list_instances(
    is_admin: AdminFlag,
    db: DbDep,
    limit: int = 100,
    offset: int = 0,
) -> list[InstanceItem]:
    _require_admin(is_admin)
    return admin_service.list_instances(db, limit=limit, offset=offset)


@router.post("/api/v1/admin/instances/{instance_uuid}/block")
def block_instance(
    instance_uuid: str,
    payload: BlockIn,
    is_admin: AdminFlag,
    db: DbDep,
) -> dict[str, object]:
    _require_admin(is_admin)
    instance = admin_service.set_blocked(db, instance_uuid, payload.blocked)
    db.commit()
    return {"uuid": instance.uuid, "is_blocked": instance.is_blocked}


# ---- Profiles: promote -----------------------------------------------------


@router.post("/api/v1/admin/profiles/{profile_id}/promote")
def promote_profile(
    profile_id: str,
    is_admin: AdminFlag,
    db: DbDep,
) -> dict[str, str]:
    _require_admin(is_admin)
    profile = admin_service.promote_to_official(db, profile_id)
    db.commit()
    return {"profile_id": profile.id, "tier": profile.tier}


# ---- Stats -----------------------------------------------------------------


@router.get("/api/v1/admin/stats", response_model=AdminStats)
def admin_stats(
    is_admin: AdminFlag,
    db: DbDep,
) -> AdminStats:
    _require_admin(is_admin)
    return admin_service.stats(db)
