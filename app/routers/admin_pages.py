"""Server-rendered HTML admin panel (C8).

A licence-portal-style simple admin login: every route depends on
``require_admin`` (HTTP Basic from the app's OWN ``.env`` credentials —
``COMMUNITY_ADMIN_USER`` / ``COMMUNITY_ADMIN_PASSWORD``), so the browser shows a
Basic-auth prompt. This is intentionally NOT instance SSO.

Sections: a stats dashboard, the upload review queue (reusing the C3 review
service), moderation (open reports), instances (block/unblock) and profiles
(promote beta/community -> official). Each WRITE is a plain HTML form that posts
to a ``require_admin`` form-handler which calls the service, commits and 303s
back to the section. Programmatic callers use the JSON API in ``admin_api``.

Everything is rendered with Jinja2 autoescaping ON; attacker-influenced content
(report reasons, script findings, profile/instance names) is NEVER marked safe.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.deps import require_admin
from app.routers.community_interactions import DbDep
from app.services import admin as admin_service
from app.services import uploads as uploads_service
from app.templating import templates
from app.version import VERSION

router = APIRouter(tags=["admin-pages"])

# All admin pages + form handlers require HTTP Basic admin credentials.
AdminUser = Annotated[str, Depends(require_admin)]

_SECTIONS = (
    {"key": "dashboard", "href": "/admin", "label": "Übersicht"},
    {"key": "review", "href": "/admin/review", "label": "Review-Queue"},
    {"key": "moderation", "href": "/admin/moderation", "label": "Moderation"},
    {"key": "instances", "href": "/admin/instances", "label": "Instanzen"},
    {"key": "profiles", "href": "/admin/profiles", "label": "Profile"},
)


def _base_context(active: str) -> dict:
    return {"version": VERSION, "sections": _SECTIONS, "active": active}


# ---- Pages -----------------------------------------------------------------


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    admin: AdminUser,
    db: DbDep,
) -> HTMLResponse:
    stats = admin_service.stats(db)
    context = _base_context("dashboard")
    context["stats"] = stats
    return templates.TemplateResponse(request, "admin/dashboard.html", context)


@router.get("/admin/review", response_class=HTMLResponse)
def admin_review(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    status: str | None = None,
) -> HTMLResponse:
    items = uploads_service.list_for_review(db, status)
    context = _base_context("review")
    context["items"] = items
    context["status_filter"] = status or "pending"
    return templates.TemplateResponse(request, "admin/review.html", context)


@router.get("/admin/moderation", response_class=HTMLResponse)
def admin_moderation(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    status: str = "open",
) -> HTMLResponse:
    reports = admin_service.list_reports(db, status)
    context = _base_context("moderation")
    context["reports"] = reports
    context["status_filter"] = status
    return templates.TemplateResponse(request, "admin/moderation.html", context)


@router.get("/admin/instances", response_class=HTMLResponse)
def admin_instances(
    request: Request,
    admin: AdminUser,
    db: DbDep,
) -> HTMLResponse:
    instances = admin_service.list_instances(db, limit=200, offset=0)
    context = _base_context("instances")
    context["instances"] = instances
    return templates.TemplateResponse(request, "admin/instances.html", context)


@router.get("/admin/profiles", response_class=HTMLResponse)
def admin_profiles(
    request: Request,
    admin: AdminUser,
    db: DbDep,
) -> HTMLResponse:
    profiles = admin_service.list_promotable(db)
    context = _base_context("profiles")
    context["profiles"] = profiles
    return templates.TemplateResponse(request, "admin/profiles.html", context)


# ---- Form handlers (HTML forms post here; then 303 back) -------------------


def _redirect(path: str) -> RedirectResponse:
    # 303 so the browser re-issues a GET after the POST.
    return RedirectResponse(path, status_code=303)


@router.post("/admin/review/{profile_id}/approve")
def admin_review_approve(
    profile_id: str,
    admin: AdminUser,
    db: DbDep,
) -> RedirectResponse:
    uploads_service.approve(db, profile_id)
    db.commit()
    return _redirect("/admin/review")


@router.post("/admin/review/{profile_id}/reject")
def admin_review_reject(
    profile_id: str,
    admin: AdminUser,
    db: DbDep,
    reason: Annotated[str, Form()] = "rejected by admin",
) -> RedirectResponse:
    uploads_service.reject(db, profile_id, reason)
    db.commit()
    return _redirect("/admin/review")


@router.post("/admin/moderation/{report_id}/resolve")
def admin_moderation_resolve(
    report_id: str,
    admin: AdminUser,
    db: DbDep,
    action: Annotated[str, Form()],
) -> RedirectResponse:
    admin_service.resolve_report(db, report_id, action)
    db.commit()
    return _redirect("/admin/moderation")


@router.post("/admin/instances/{instance_uuid}/block")
def admin_instance_block(
    instance_uuid: str,
    admin: AdminUser,
    db: DbDep,
    blocked: Annotated[str, Form()],
) -> RedirectResponse:
    admin_service.set_blocked(db, instance_uuid, blocked == "true")
    db.commit()
    return _redirect("/admin/instances")


@router.post("/admin/profiles/{profile_id}/promote")
def admin_profile_promote(
    profile_id: str,
    admin: AdminUser,
    db: DbDep,
) -> RedirectResponse:
    admin_service.promote_to_official(db, profile_id)
    db.commit()
    return _redirect("/admin/profiles")
