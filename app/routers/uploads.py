"""Community profile upload + admin review queue (C3).

Uploading requires a Bearer API token (``get_current_instance``): the caller's
``instance.uuid`` becomes the profile's ``uploader_instance_uuid``. Uploads are
immediately visible carrying a "waiting for review" badge until an admin
approves them. The admin review queue + approve/reject actions are gated by the
``X-Admin-Authorization`` Basic header (the same ``AdminFlag`` seam C4/C5 use),
so they raise 401 when the header is missing or invalid.

The upload path is intentionally simple: re-uploading a profile with the same
(name, vendor) by the SAME uploader versions it (200); a DIFFERENT uploader with
the same (name, vendor) gets their own separate profile. The only 409 is a
re-used ``version_tag`` on a profile you already own (versions are immutable).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.routers.community_interactions import AdminFlag, CurrentInstance, DbDep
from app.schemas.upload import BundleUpload, RejectIn, ReviewItem, UploadResult
from app.services import uploads as uploads_service

router = APIRouter(tags=["uploads"])


@router.post("/api/v1/profiles/upload", response_model=UploadResult)
def upload_profile(
    payload: BundleUpload,
    instance: CurrentInstance,
    db: DbDep,
) -> UploadResult:
    bundle = uploads_service.validate_bundle(payload.bundle)
    profile, version = uploads_service.create_or_version_profile(
        db,
        instance_uuid=instance.uuid,
        bundle=bundle,
        version_tag=payload.version_tag,
        changelog_md=payload.changelog_md,
    )
    db.commit()
    return UploadResult(
        profile_id=profile.id,
        version_id=version.id,
        review_status=profile.review_status,
        has_scripts=profile.has_scripts,
        script_findings=list(profile.script_findings or []),
    )


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin auth required",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/api/v1/admin/review-queue", response_model=list[ReviewItem])
def review_queue(
    is_admin: AdminFlag,
    db: DbDep,
    status: str | None = None,
) -> list[ReviewItem]:
    """List pending uploads (default), or ``?status=all|approved|rejected``."""
    _require_admin(is_admin)
    return uploads_service.list_for_review(db, status)


@router.post("/api/v1/admin/review/{profile_id}/approve", response_model=ReviewItem)
def approve_profile(
    profile_id: str,
    is_admin: AdminFlag,
    db: DbDep,
) -> ReviewItem:
    _require_admin(is_admin)
    profile = uploads_service.approve(db, profile_id)
    db.commit()
    return uploads_service.to_review_item(db, profile)


@router.post("/api/v1/admin/review/{profile_id}/reject", response_model=ReviewItem)
def reject_profile(
    profile_id: str,
    payload: RejectIn,
    is_admin: AdminFlag,
    db: DbDep,
) -> ReviewItem:
    _require_admin(is_admin)
    profile = uploads_service.reject(db, profile_id, payload.reason)
    db.commit()
    return uploads_service.to_review_item(db, profile)
