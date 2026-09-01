"""Server-rendered admin area (Neubau 09/2026).

Sektionen: Übersicht · Review · Profile · Moderation · Fragen · Instanzen ·
Icons · Protokoll · Sicherheit. Jede Seite hängt an ``require_admin``
(Admin-SESSION mit Idle-Ablauf), jeder schreibende Handler zusätzlich an
``require_csrf`` und schreibt einen Eintrag ins Admin-Protokoll — in derselben
Transaktion wie die Änderung.

Regeln, die hier gelten:
* Alles server-gerendert, Formulare sind normale POSTs mit 303 zurück.
* Jede Liste ist durchsuchbar und seitenweise (``Page``).
* Attacker-influenced content (Gründe, Namen, Script-Rümpfe) wird NIE als
  ``safe`` markiert — Jinja-Autoescaping bleibt an.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import admin_security as sec
from app.auth.csrf import require_csrf
from app.auth.deps import require_admin, verify_admin_credentials
from app.config import get_settings
from app.db import get_db
from app.routers.admin_auth import client_ip
from app.schemas import check_preview_from_bundle
from app.services import admin as admin_service
from app.services import admin_account
from app.services import admin_audit as audit
from app.services import uploads as uploads_service
from app.templating import templates
from app.version import VERSION

router = APIRouter(tags=["admin-pages"])

DbDep = Annotated[Session, Depends(get_db)]
AdminUser = Annotated[str, Depends(require_admin)]
Csrf = Annotated[None, Depends(require_csrf)]

SECTIONS = (
    ("dashboard", "/admin", "home"),
    ("review", "/admin/review", "inbox"),
    ("profiles", "/admin/profiles", "layers"),
    ("moderation", "/admin/moderation", "flag"),
    ("questions", "/admin/questions", "message"),
    ("instances", "/admin/instances", "users"),
    ("icons", "/admin/icons", "image"),
    ("audit", "/admin/audit", "list"),
    ("security", "/admin/security", "shield"),
)


# ---- Helfer ----------------------------------------------------------------------


def _ctx(request: Request, db: Session, active: str, **extra: Any) -> dict:
    tasks = admin_service.tasks(db)
    return {
        "version": VERSION,
        "active": active,
        "sections": [{"key": key, "href": href, "icon": icon} for key, href, icon in SECTIONS],
        "badges": {"review": tasks["pending"], "moderation": tasks["reports"]},
        "admin_user": request.session.get("admin_user") or "admin",
        "path": request.url.path,
        **extra,
    }


def _redirect(path: str) -> RedirectResponse:
    # 303 so the browser re-issues a GET after the POST.
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _back(next_: str | None, default: str) -> RedirectResponse:
    """Zurück dorthin, wo das Formular stand — nur innerhalb des Admin-Bereichs."""
    if next_ and next_.startswith("/admin") and not next_.startswith("//"):
        return _redirect(next_)
    return _redirect(default)


def _flash(request: Request, kind: str, key: str, **kw: Any) -> None:
    items = list(request.session.get("flash") or [])
    items.append({"kind": kind, "key": key, "kw": kw})
    request.session["flash"] = items


def _log(request: Request, db: Session, admin: str, action: str, **kw: Any) -> None:
    audit.record(db, admin_user=admin, action=action, ip=client_ip(request), **kw)


def _page_href(request: Request, page: int) -> str:
    params = dict(request.query_params)
    params["page"] = str(page)
    return request.url.path + "?" + urlencode(params)


def _pager(request: Request, page_obj) -> dict:
    return {
        "page": page_obj.page,
        "pages": page_obj.pages,
        "total": page_obj.total,
        "start": page_obj.start,
        "end": page_obj.end,
        "prev": _page_href(request, page_obj.page - 1) if page_obj.page > 1 else None,
        "next": _page_href(request, page_obj.page + 1) if page_obj.page < page_obj.pages else None,
    }


# ---- Übersicht -------------------------------------------------------------------


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: AdminUser, db: DbDep) -> HTMLResponse:
    context = _ctx(
        request,
        db,
        "dashboard",
        stats=admin_service.stats(db),
        tasks=admin_service.tasks(db),
        activity=admin_service.activity(db, limit=18),
        recent_audit=audit.recent(db, limit=8),
        system=admin_service.system_info(db),
        two_fa=admin_account.status(db, admin),
    )
    return templates.TemplateResponse(request, "admin/dashboard.html", context)


# ---- Review ----------------------------------------------------------------------


@router.get("/admin/review", response_class=HTMLResponse)
def admin_review(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    review = status_filter or "pending"
    result = admin_service.search_profiles(
        db,
        q=q,
        review=None if review == "all" else review,
        source="community",
        sort="created",
        page=page,
    )
    items = [uploads_service.to_review_item(db, p) for p in result.items]
    context = _ctx(
        request,
        db,
        "review",
        items=items,
        profiles=result.items,
        status_filter=review,
        counts=admin_service.review_counts(db),
        q=q or "",
        pager=_pager(request, result),
    )
    return templates.TemplateResponse(request, "admin/review.html", context)


def _profile_inspection(db: Session, profile) -> dict:
    """Bundle-Inspektion: Checks, Scripts + Fundstellen, Versionen, Uploader."""
    current = profile.current_version
    bundle = current.bundle_json if current is not None else None
    previews = check_preview_from_bundle(bundle)
    scripts = admin_service.findings_by_line(
        admin_service.bundle_scripts(bundle), list(profile.script_findings or [])
    )
    versions = sorted(profile.versions or [], key=lambda v: v.created_at, reverse=True)
    return {
        "profile": profile,
        "uploader": admin_service.uploader_name(db, profile),
        "checks": previews,
        "scripts": scripts,
        "findings": list(profile.script_findings or []),
        "versions": versions,
        "current": current,
        "bundle_meta": (bundle or {}).get("profile") if isinstance(bundle, dict) else None,
        "open_reports": admin_service.profile_report_count(db, profile.id),
    }


@router.get("/admin/review/{profile_id}", response_class=HTMLResponse)
def admin_review_detail(
    profile_id: str, request: Request, admin: AdminUser, db: DbDep
) -> HTMLResponse:
    profile = admin_service.get_profile_any(db, profile_id)
    context = _ctx(request, db, "review", **_profile_inspection(db, profile))
    return templates.TemplateResponse(request, "admin/review_detail.html", context)


@router.post("/admin/review/{profile_id}/approve")
def admin_review_approve(
    profile_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    profile = uploads_service.approve(db, profile_id)
    _log(
        request,
        db,
        admin,
        "review.approve",
        target_type="profile",
        target_id=profile.id,
        summary=profile.name,
    )
    db.commit()
    _flash(request, "ok", "admin.flash.approved", name=profile.name)
    return _back(next, "/admin/review")


@router.post("/admin/review/{profile_id}/reject")
def admin_review_reject(
    profile_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    reason: Annotated[str, Form()] = "",
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    reason = reason.strip() or "rejected by admin"
    profile = uploads_service.reject(db, profile_id, reason)
    _log(
        request,
        db,
        admin,
        "review.reject",
        target_type="profile",
        target_id=profile.id,
        summary=profile.name,
        details={"reason": reason},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.rejected", name=profile.name)
    return _back(next, "/admin/review")


# ---- Profile ---------------------------------------------------------------------


@router.get("/admin/profiles", response_class=HTMLResponse)
def admin_profiles(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    q: str | None = None,
    tier: str | None = None,
    category: str | None = None,
    vendor: str | None = None,
    review: str | None = None,
    source: str | None = None,
    scripts: str | None = None,
    removed: str | None = None,
    sort: str = "updated",
    page: int = 1,
) -> HTMLResponse:
    result = admin_service.search_profiles(
        db,
        q=q,
        tier=tier,
        category=category,
        vendor=vendor,
        review=review,
        source=source,
        scripts=True if scripts == "yes" else (False if scripts == "no" else None),
        removed=removed == "yes",
        sort=sort if sort in admin_service.PROFILE_SORTS else "updated",
        page=page,
    )
    facets = admin_service.profile_facets(db)
    context = _ctx(
        request,
        db,
        "profiles",
        profiles=result.items,
        names=result.extra.get("names", {}),
        pager=_pager(request, result),
        counts=admin_service.profile_counts(db),
        categories=facets["categories"],
        vendors=facets["vendors"],
        sorts=admin_service.PROFILE_SORTS,
        f={
            "q": q or "",
            "tier": tier or "",
            "category": category or "",
            "vendor": vendor or "",
            "review": review or "",
            "source": source or "",
            "scripts": scripts or "",
            "removed": removed or "",
            "sort": sort,
        },
        filtered=any([q, tier, category, vendor, review, source, scripts, removed]),
    )
    return templates.TemplateResponse(request, "admin/profiles.html", context)


@router.get("/admin/profiles/{profile_id}", response_class=HTMLResponse)
def admin_profile_detail(
    profile_id: str, request: Request, admin: AdminUser, db: DbDep
) -> HTMLResponse:
    profile = admin_service.get_profile_any(db, profile_id)
    context = _ctx(
        request,
        db,
        "profiles",
        **_profile_inspection(db, profile),
        comments=admin_service.profile_comments(db, profile.id),
        history=audit.list_entries(db, target_type="profile", target_id=profile.id, per_page=20),
        tiers=admin_service.TIERS,
        facets=admin_service.profile_facets(db),
    )
    return templates.TemplateResponse(request, "admin/profile_detail.html", context)


@router.post("/admin/profiles/{profile_id}/update")
def admin_profile_update(
    profile_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    name: Annotated[str, Form()] = "",
    vendor: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
    icon: Annotated[str, Form()] = "",
    description_md: Annotated[str, Form()] = "",
    vesana_min_version: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    requires_agent: Annotated[str | None, Form()] = None,
    requires_collector: Annotated[str | None, Form()] = None,
    requires_snmp: Annotated[str | None, Form()] = None,
    requires_ssh: Annotated[str | None, Form()] = None,
    requires_api_token: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    tag_list = [t.strip() for t in tags.replace(";", ",").split(",") if t.strip()] or None
    values = {
        "name": name,
        "vendor": vendor,
        "category": category,
        "icon": icon,
        "description_md": description_md,
        "vesana_min_version": vesana_min_version,
        "tags": tag_list,
        "requires_agent": requires_agent == "on",
        "requires_collector": requires_collector == "on",
        "requires_snmp": requires_snmp == "on",
        "requires_ssh": requires_ssh == "on",
        "requires_api_token": requires_api_token == "on",
    }
    changed = admin_service.update_profile(db, profile_id, values)
    profile = admin_service.get_profile_any(db, profile_id)
    if changed:
        _log(
            request,
            db,
            admin,
            "profile.update",
            target_type="profile",
            target_id=profile.id,
            summary=profile.name,
            details={"changed": changed},
        )
        db.commit()
        _flash(request, "ok", "admin.flash.saved")
    else:
        _flash(request, "info", "admin.flash.nothing_changed")
    return _redirect(f"/admin/profiles/{profile_id}")


@router.post("/admin/profiles/{profile_id}/tier")
def admin_profile_set_tier(
    profile_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    tier: Annotated[str, Form()],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    before = admin_service.get_profile_any(db, profile_id).tier
    profile = admin_service.set_tier(db, profile_id, tier)
    if before != profile.tier:
        _log(
            request,
            db,
            admin,
            "profile.tier",
            target_type="profile",
            target_id=profile.id,
            summary=profile.name,
            details={"from": before, "to": profile.tier},
        )
        db.commit()
        _flash(request, "ok", "admin.flash.tier_set", name=profile.name, tier=profile.tier)
    return _back(next, f"/admin/profiles/{profile_id}")


@router.post("/admin/profiles/{profile_id}/promote")
def admin_profile_promote(
    profile_id: str, request: Request, admin: AdminUser, db: DbDep, _csrf: Csrf
) -> RedirectResponse:
    profile = admin_service.promote_to_official(db, profile_id)
    _log(
        request,
        db,
        admin,
        "profile.tier",
        target_type="profile",
        target_id=profile.id,
        summary=profile.name,
        details={"to": "official"},
    )
    db.commit()
    return _redirect(f"/admin/profiles/{profile_id}")


@router.post("/admin/profiles/{profile_id}/delete")
def admin_profile_delete(
    profile_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    profile = admin_service.delete_profile(db, profile_id)
    _log(
        request,
        db,
        admin,
        "profile.delete",
        target_type="profile",
        target_id=profile.id,
        summary=profile.name,
    )
    db.commit()
    _flash(request, "ok", "admin.flash.deleted", name=profile.name)
    return _back(next, "/admin/profiles")


@router.post("/admin/profiles/{profile_id}/restore")
def admin_profile_restore(
    profile_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    profile = admin_service.restore_profile(db, profile_id)
    _log(
        request,
        db,
        admin,
        "profile.restore",
        target_type="profile",
        target_id=profile.id,
        summary=profile.name,
    )
    db.commit()
    _flash(request, "ok", "admin.flash.restored", name=profile.name)
    return _back(next, f"/admin/profiles/{profile_id}")


@router.post("/admin/profiles/{profile_id}/versions/{version_id}/current")
def admin_profile_version_current(
    profile_id: str,
    version_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
) -> RedirectResponse:
    version = admin_service.set_current_version(db, profile_id, version_id)
    profile = admin_service.get_profile_any(db, profile_id)
    _log(
        request,
        db,
        admin,
        "profile.version_current",
        target_type="profile",
        target_id=profile.id,
        summary=f"{profile.name} → {version.version_tag}",
        details={"version_id": version.id, "version_tag": version.version_tag},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.version_current", tag=version.version_tag)
    return _redirect(f"/admin/profiles/{profile_id}?tab=versions")


@router.post("/admin/comments/{comment_id}/remove")
def admin_comment_remove(
    comment_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    comment = admin_service.set_comment_removed(db, comment_id, True)
    _log(
        request,
        db,
        admin,
        "comment.remove",
        target_type="comment",
        target_id=comment.id,
        summary=admin_service._snippet(comment.body_md),
        details={"profile_id": comment.profile_id},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.comment_removed")
    return _back(next, f"/admin/profiles/{comment.profile_id}?tab=comments")


@router.post("/admin/comments/{comment_id}/restore")
def admin_comment_restore(
    comment_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    comment = admin_service.set_comment_removed(db, comment_id, False)
    _log(
        request,
        db,
        admin,
        "comment.restore",
        target_type="comment",
        target_id=comment.id,
        summary=admin_service._snippet(comment.body_md),
        details={"profile_id": comment.profile_id},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.comment_restored")
    return _back(next, f"/admin/profiles/{comment.profile_id}?tab=comments")


# ---- Moderation ------------------------------------------------------------------


@router.get("/admin/moderation", response_class=HTMLResponse)
def admin_moderation(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    status_filter: Annotated[str, Query(alias="status")] = "open",
    page: int = 1,
) -> HTMLResponse:
    result = admin_service.list_reports_page(db, status_filter, page=page)
    context = _ctx(
        request,
        db,
        "moderation",
        reports=result.items,
        status_filter=status_filter,
        counts=admin_service.report_counts(db),
        pager=_pager(request, result),
    )
    return templates.TemplateResponse(request, "admin/moderation.html", context)


@router.post("/admin/moderation/{report_id}/resolve")
def admin_moderation_resolve(
    report_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    action: Annotated[str, Form()],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    report = admin_service.resolve_report(db, report_id, action)
    _log(
        request,
        db,
        admin,
        f"report.{action}",
        target_type=report.target_type,
        target_id=report.target_id,
        summary=admin_service._snippet(report.reason),
        details={"report_id": report.id},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.report_" + action)
    return _back(next, "/admin/moderation")


# ---- Fragen & Antworten ----------------------------------------------------------


@router.get("/admin/questions", response_class=HTMLResponse)
def admin_questions(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    q: str | None = None,
    state: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    result = admin_service.search_questions(db, q=q, state=state, page=page)
    context = _ctx(
        request,
        db,
        "questions",
        questions=result.items,
        names=result.extra.get("names", {}),
        counts=admin_service.question_counts(db),
        state=state or "",
        q=q or "",
        pager=_pager(request, result),
    )
    return templates.TemplateResponse(request, "admin/questions.html", context)


@router.get("/admin/questions/{question_id}", response_class=HTMLResponse)
def admin_question_detail(
    question_id: str, request: Request, admin: AdminUser, db: DbDep
) -> HTMLResponse:
    question = admin_service.get_question(db, question_id)
    answers = sorted(question.answers, key=lambda a: (not a.is_accepted, a.created_at))
    names = admin_service.names_for(
        db, {question.instance_uuid, *[a.instance_uuid for a in answers]}
    )
    context = _ctx(
        request,
        db,
        "questions",
        question=question,
        answers=answers,
        names=names,
        history=audit.list_entries(db, target_type="question", target_id=question.id, per_page=10),
    )
    return templates.TemplateResponse(request, "admin/question_detail.html", context)


@router.post("/admin/questions/{question_id}/close")
def admin_question_close(
    question_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    reason: Annotated[str, Form()] = "",
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    question = admin_service.close_question(db, question_id, reason)
    _log(
        request,
        db,
        admin,
        "question.close",
        target_type="question",
        target_id=question.id,
        summary=question.title_text,
        details={"reason": question.closed_reason},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.question_closed")
    return _back(next, f"/admin/questions/{question_id}")


@router.post("/admin/questions/{question_id}/reopen")
def admin_question_reopen(
    question_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    question = admin_service.reopen_question(db, question_id)
    _log(
        request,
        db,
        admin,
        "question.reopen",
        target_type="question",
        target_id=question.id,
        summary=question.title_text,
    )
    db.commit()
    _flash(request, "ok", "admin.flash.question_reopened")
    return _back(next, f"/admin/questions/{question_id}")


@router.post("/admin/questions/{question_id}/team")
def admin_question_team(
    question_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    flag: Annotated[str, Form()] = "true",
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    question = admin_service.set_question_team(db, question_id, flag == "true")
    _log(
        request,
        db,
        admin,
        "question.team",
        target_type="question",
        target_id=question.id,
        summary=question.title_text,
        details={"is_vesana_team": question.is_vesana_team},
    )
    db.commit()
    return _back(next, f"/admin/questions/{question_id}")


@router.post("/admin/answers/{answer_id}/delete")
def admin_answer_delete(
    answer_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    question = admin_service.delete_answer(db, answer_id)
    _log(
        request,
        db,
        admin,
        "answer.delete",
        target_type="question",
        target_id=question.id,
        summary=question.title_text,
        details={"answer_id": answer_id},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.answer_deleted")
    return _back(next, f"/admin/questions/{question.id}")


@router.post("/admin/answers/{answer_id}/team")
def admin_answer_team(
    answer_id: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    flag: Annotated[str, Form()] = "true",
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    answer = admin_service.set_answer_team(db, answer_id, flag == "true")
    _log(
        request,
        db,
        admin,
        "answer.team",
        target_type="question",
        target_id=answer.question_id,
        summary=admin_service._snippet(answer.body_md),
        details={"answer_id": answer.id, "is_vesana_team": answer.is_vesana_team},
    )
    db.commit()
    return _back(next, f"/admin/questions/{answer.question_id}")


# ---- Instanzen -------------------------------------------------------------------


@router.get("/admin/instances", response_class=HTMLResponse)
def admin_instances(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    q: str | None = None,
    state: str | None = None,
    sort: str = "seen",
    page: int = 1,
) -> HTMLResponse:
    result = admin_service.search_instances(
        db,
        q=q,
        state=state,
        sort=sort if sort in admin_service.INSTANCE_SORTS else "seen",
        page=page,
    )
    context = _ctx(
        request,
        db,
        "instances",
        instances=result.items,
        counts=admin_service.instance_counts(db),
        state=state or "",
        q=q or "",
        sort=sort,
        sorts=admin_service.INSTANCE_SORTS,
        pager=_pager(request, result),
    )
    return templates.TemplateResponse(request, "admin/instances.html", context)


@router.get("/admin/instances/{instance_uuid}", response_class=HTMLResponse)
def admin_instance_detail(
    instance_uuid: str, request: Request, admin: AdminUser, db: DbDep
) -> HTMLResponse:
    detail = admin_service.instance_detail(db, instance_uuid)
    context = _ctx(
        request,
        db,
        "instances",
        **detail,
        history=audit.list_entries(
            db, target_type="instance", target_id=instance_uuid, per_page=10
        ),
    )
    return templates.TemplateResponse(request, "admin/instance_detail.html", context)


@router.post("/admin/instances/{instance_uuid}/block")
def admin_instance_block(
    instance_uuid: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    blocked: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    flag = blocked == "true"
    instance = admin_service.set_blocked(db, instance_uuid, flag, reason)
    name = admin_service.names_for(db, {instance.uuid})[instance.uuid]
    _log(
        request,
        db,
        admin,
        "instance.block" if flag else "instance.unblock",
        target_type="instance",
        target_id=instance.uuid,
        summary=name,
        details={"reason": instance.blocked_reason} if flag else None,
    )
    db.commit()
    _flash(request, "ok", "admin.flash.blocked" if flag else "admin.flash.unblocked", name=name)
    return _back(next, f"/admin/instances/{instance_uuid}")


@router.post("/admin/instances/{instance_uuid}/reset-name")
def admin_instance_reset_name(
    instance_uuid: str,
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    instance = db.get(admin_service.Instance, instance_uuid)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    old = instance.chosen_name
    admin_service.reset_chosen_name(db, instance_uuid)
    _log(
        request,
        db,
        admin,
        "instance.reset_name",
        target_type="instance",
        target_id=instance_uuid,
        summary=old or "—",
        details={"was": old},
    )
    db.commit()
    _flash(request, "ok", "admin.flash.name_reset")
    return _back(next, f"/admin/instances/{instance_uuid}")


# ---- Icons -----------------------------------------------------------------------


@router.get("/admin/icons", response_class=HTMLResponse)
def admin_icons(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    q: str | None = None,
    source: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    result = admin_service.search_icons(db, q=q, source=source, page=page)
    context = _ctx(
        request,
        db,
        "icons",
        overview=admin_service.icon_overview(db),
        icons=result.items,
        q=q or "",
        source=source or "",
        pager=_pager(request, result),
    )
    return templates.TemplateResponse(request, "admin/icons.html", context)


# ---- Protokoll -------------------------------------------------------------------


@router.get("/admin/audit", response_class=HTMLResponse)
def admin_audit(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    action: str | None = None,
    q: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    result = audit.list_entries(db, action=action or None, q=q, page=page)
    context = _ctx(
        request,
        db,
        "audit",
        entries=result.items,
        action=action or "",
        q=q or "",
        groups=audit.action_groups(),
        pager={
            "page": result.page,
            "pages": result.pages,
            "total": result.total,
            "start": 0 if result.total == 0 else (result.page - 1) * audit.PER_PAGE + 1,
            "end": min(result.total, result.page * audit.PER_PAGE),
            "prev": _page_href(request, result.page - 1) if result.page > 1 else None,
            "next": _page_href(request, result.page + 1) if result.page < result.pages else None,
        },
    )
    return templates.TemplateResponse(request, "admin/audit.html", context)


# ---- Sicherheit (2FA) ------------------------------------------------------------


@router.get("/admin/security", response_class=HTMLResponse)
def admin_security(request: Request, admin: AdminUser, db: DbDep) -> HTMLResponse:
    settings = get_settings()
    context = _ctx(
        request,
        db,
        "security",
        two_fa=admin_account.status(db, admin),
        logins=audit.list_entries(db, action="auth.*", per_page=12).items,
        idle_minutes=settings.COMMUNITY_ADMIN_IDLE_MINUTES,
        api_token_set=bool(settings.COMMUNITY_ADMIN_API_TOKEN),
    )
    return templates.TemplateResponse(request, "admin/security.html", context)


@router.post("/admin/security/2fa/setup")
def admin_2fa_begin(request: Request, admin: AdminUser, db: DbDep, _csrf: Csrf) -> RedirectResponse:
    request.session["totp_setup"] = admin_account.begin_setup(admin, get_settings())
    return _redirect("/admin/security/2fa/setup")


@router.get("/admin/security/2fa/setup", response_class=HTMLResponse)
def admin_2fa_setup_page(request: Request, admin: AdminUser, db: DbDep) -> HTMLResponse:
    setup = request.session.get("totp_setup")
    if not setup:
        return _redirect("/admin/security")
    context = _ctx(
        request,
        db,
        "security",
        qr=sec.qr_svg(setup["uri"]),
        secret_pretty=sec.pretty_secret(setup["secret"]),
        error=request.query_params.get("error"),
    )
    return templates.TemplateResponse(request, "admin/security_setup.html", context)


@router.post("/admin/security/2fa/confirm")
def admin_2fa_confirm(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    code: Annotated[str, Form()] = "",
):
    setup = request.session.get("totp_setup")
    if not setup:
        return _redirect("/admin/security")
    codes = admin_account.confirm_setup(db, admin, setup["secret"], code, get_settings())
    if codes is None:
        return _redirect("/admin/security/2fa/setup?error=code")
    request.session.pop("totp_setup", None)
    _log(
        request, db, admin, "auth.2fa_enabled", target_type="admin", target_id=admin, summary=admin
    )
    db.commit()
    context = _ctx(request, db, "security", codes=codes, heading_key="admin.sec.enabled_title")
    return templates.TemplateResponse(request, "admin/security_codes.html", context)


@router.post("/admin/security/2fa/disable")
def admin_2fa_disable(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    password: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
) -> RedirectResponse:
    settings = get_settings()
    if not verify_admin_credentials(admin, password, settings):
        _flash(request, "err", "admin.sec.pw_wrong")
        return _redirect("/admin/security")
    if not admin_account.verify_second_factor(db, admin, code, settings).ok:
        db.rollback()
        _flash(request, "err", "admin.sec.code_wrong")
        return _redirect("/admin/security")
    admin_account.disable(db, admin)
    _log(
        request, db, admin, "auth.2fa_disabled", target_type="admin", target_id=admin, summary=admin
    )
    db.commit()
    _flash(request, "ok", "admin.sec.disabled_ok")
    return _redirect("/admin/security")


@router.post("/admin/security/backup-codes")
def admin_backup_codes(
    request: Request,
    admin: AdminUser,
    db: DbDep,
    _csrf: Csrf,
    code: Annotated[str, Form()] = "",
):
    settings = get_settings()
    if not admin_account.two_fa_enabled(db, admin):
        return _redirect("/admin/security")
    if not admin_account.verify_second_factor(db, admin, code, settings).ok:
        db.rollback()
        _flash(request, "err", "admin.sec.code_wrong")
        return _redirect("/admin/security")
    codes = admin_account.regenerate_backup_codes(db, admin, settings)
    _log(
        request, db, admin, "auth.backup_codes", target_type="admin", target_id=admin, summary=admin
    )
    db.commit()
    context = _ctx(request, db, "security", codes=codes, heading_key="admin.sec.codes_new_title")
    return templates.TemplateResponse(request, "admin/security_codes.html", context)
