"""Community admin JSON API (C8).

Every endpoint here is gated by the ``AdminFlag`` seam (``X-Admin-Authorization``
header: ``Bearer <COMMUNITY_ADMIN_API_TOKEN>``, or ``Basic user:password`` only
while the admin has NO second factor — see ``is_admin_request``). These coexist
with the HTML admin pages (``app.routers.admin_pages``), which use the admin
SESSION + CSRF-protected form posts; this JSON API is for programmatic use.

Seit dem Umbau zur Vesana-Bibliothek (2026-11-07) entfallen die Endpunkte, die
nur Upload-Review und Moderation (Meldungen) bedient haben.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.admin_api import AdminFlag, DbDep
from app.auth.admin_api import require_admin_flag as _require_admin
from app.schemas.admin import (
    AdminStats,
    BlockIn,
    InstanceItem,
)
from app.services import admin as admin_service

router = APIRouter(tags=["admin"])


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
    instance = admin_service.set_blocked(db, instance_uuid, payload.blocked, payload.reason)
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
