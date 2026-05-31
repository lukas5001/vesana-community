"""Server-rendered pages for community.vesana.org (browse + detail)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_session_instance
from app.db import get_db
from app.models.community_profile import CommunityProfile
from app.models.instance import Instance
from app.models.question import Question
from app.schemas import VESANA_TEAM_UPLOADER, check_preview_from_bundle
from app.services import qa as qa_service
from app.services import uploads as uploads_service
from app.services.comments import author_display, create_comment, list_thread
from app.services.profiles import (
    SORT_OPTIONS,
    ProfileFilters,
    get_profile,
    latest_version_tag,
    list_profiles,
)
from app.services.voting import cast_vote
from app.templating import templates
from app.version import VERSION

router = APIRouter(tags=["pages"])

DbDep = Annotated[Session, Depends(get_db)]
SessionInstance = Annotated[Instance | None, Depends(get_session_instance)]


def _distinct_values(db: Session, column) -> list[str]:
    rows = db.execute(
        select(column)
        .where(
            column.is_not(None),
            CommunityProfile.is_removed.is_(False),
        )
        .distinct()
        .order_by(column)
    ).scalars()
    return [r for r in rows if r]


@router.get("/", response_class=HTMLResponse)
@router.get("/browse", response_class=HTMLResponse, include_in_schema=False)
def browse_page(
    request: Request,
    db: DbDep,
    instance: SessionInstance,
    q: Annotated[str | None, Query()] = None,
    tier: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    vendor: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "trending",
) -> HTMLResponse:
    filters = ProfileFilters(q=q, tier=tier, category=category, vendor=vendor, sort=sort, limit=100)
    profiles, total = list_profiles(db, filters)
    cards = [
        {
            "id": p.id,
            "name": p.name,
            "vendor": p.vendor,
            "category": p.category,
            "icon": p.icon,
            "tier": p.tier,
            "review_status": p.review_status,
            "has_scripts": p.has_scripts,
            "import_count": p.import_count,
            "download_count": p.download_count,
            "vote_score": p.vote_score,
            "tags": list(p.tags or []),
            "version_tag": latest_version_tag(p),
        }
        for p in profiles
    ]
    context = {
        "instance": instance,
        "version": VERSION,
        "profiles": cards,
        "total": total,
        "q": q or "",
        "active_tier": tier or "",
        "active_category": category or "",
        "active_vendor": vendor or "",
        "active_sort": sort if sort in SORT_OPTIONS else "trending",
        "sort_options": SORT_OPTIONS,
        "categories": _distinct_values(db, CommunityProfile.category),
        "vendors": _distinct_values(db, CommunityProfile.vendor),
    }
    return templates.TemplateResponse(request, "browse.html", context)


@router.get("/p/{profile_id}", response_class=HTMLResponse)
def detail_page(
    profile_id: str,
    request: Request,
    db: DbDep,
    instance: SessionInstance,
) -> HTMLResponse:
    profile = get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    current = profile.current_version
    bundle = current.bundle_json if current is not None else None
    if profile.tier in ("official", "beta") or not profile.uploader_instance_uuid:
        uploader = VESANA_TEAM_UPLOADER
    else:
        uploader_instance = db.get(Instance, profile.uploader_instance_uuid)
        uploader = (
            uploader_instance.display_name
            if uploader_instance is not None and uploader_instance.display_name
            else VESANA_TEAM_UPLOADER
        )
    # Server-render the comment thread read-only. The caller is the
    # cookie-session instance (or None); my_vote/can_edit reflect that.
    caller_uuid = instance.uuid if instance is not None else None
    threads = list_thread(db, profile.id, caller_uuid)
    related_questions = list(
        db.execute(
            select(Question)
            .where(Question.profile_id == profile.id)
            .order_by(Question.created_at.desc())
        ).scalars()
    )
    context = {
        "instance": instance,
        "version": VERSION,
        "profile": profile,
        "uploader": uploader,
        "check_preview": check_preview_from_bundle(bundle),
        "current_version": current,
        "latest_version_tag": latest_version_tag(profile),
        "now": datetime.now(UTC),
        "comment_threads": threads,
        "related_questions": related_questions,
    }
    return templates.TemplateResponse(request, "detail.html", context)


def _question_card(db: Session, question: Question) -> dict:
    return {
        "id": question.id,
        "title_text": question.title_text,
        "author_display": author_display(
            (db.get(Instance, question.instance_uuid).display_name)
            if db.get(Instance, question.instance_uuid) is not None
            else None,
            question.instance_uuid,
        ),
        "is_vesana_team": question.is_vesana_team,
        "tags": list(question.tags or []),
        "vote_score": question.vote_score,
        "answer_count": question.answer_count,
        "is_closed": question.is_closed,
        "has_accepted": qa_service.has_accepted(question),
    }


@router.get("/questions", response_class=HTMLResponse)
def questions_page(
    request: Request,
    db: DbDep,
    instance: SessionInstance,
    filter: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = qa_service.DEFAULT_SORT,
) -> HTMLResponse:
    questions, total = qa_service.list_questions(
        db, filter_=filter, tag=tag, search=search, sort=sort, limit=100
    )
    cards = [_question_card(db, q) for q in questions]
    context = {
        "instance": instance,
        "version": VERSION,
        "questions": cards,
        "total": total,
        "search": search or "",
        "active_filter": filter or "",
        "active_sort": sort if sort in qa_service.SORT_OPTIONS else qa_service.DEFAULT_SORT,
        "sort_options": qa_service.SORT_OPTIONS,
        "filter_options": qa_service.FILTER_OPTIONS,
        "active_tag": tag or "",
    }
    return templates.TemplateResponse(request, "questions.html", context)


@router.get("/questions/ask", response_class=HTMLResponse)
def ask_page(request: Request, instance: SessionInstance) -> HTMLResponse:
    """Render the 'ask a question' form (template gates on the session)."""
    return templates.TemplateResponse(
        request, "ask.html", {"instance": instance, "version": VERSION}
    )


@router.post("/questions/ask")
def ask_submit(
    request: Request,
    db: DbDep,
    instance: SessionInstance,
    title_text: Annotated[str, Form()] = "",
    body_md: Annotated[str, Form()] = "",
    tags: Annotated[str | None, Form()] = None,
):
    """Create a community question (session-cookie auth)."""
    if instance is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign in required")

    title = (title_text or "").strip()
    body = (body_md or "").strip()
    if len(title) < 8:
        return templates.TemplateResponse(
            request,
            "ask.html",
            {
                "instance": instance,
                "version": VERSION,
                "error": "Please give your question a clear title (at least 8 characters).",
                "title_text": title,
                "body_md": body,
                "tags": tags,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()][:6]
    question = qa_service.create_question(
        db,
        instance_uuid=instance.uuid,
        title_text=title,
        body_md=body,
        tags=tag_list,
        profile_id=None,
        is_vesana_team=False,
    )
    db.commit()
    return RedirectResponse(
        url=f"/questions/{question.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/questions/{question_id}", response_class=HTMLResponse)
def question_page(
    question_id: str,
    request: Request,
    db: DbDep,
    instance: SessionInstance,
) -> HTMLResponse:
    question = qa_service.get_question(db, question_id)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    answers = qa_service.sorted_answers(question)
    duplicate_of = (
        db.get(Question, question.duplicate_of_id) if question.duplicate_of_id is not None else None
    )
    display_names = qa_service.display_names_for(
        db, list({a.instance_uuid for a in answers} | {question.instance_uuid})
    )
    context = {
        "instance": instance,
        "version": VERSION,
        "question": {
            "id": question.id,
            "title_text": question.title_text,
            "body_md": question.body_md,
            "author_display": author_display(
                display_names.get(question.instance_uuid), question.instance_uuid
            ),
            "is_vesana_team": question.is_vesana_team,
            "tags": list(question.tags or []),
            "vote_score": question.vote_score,
            "answer_count": question.answer_count,
            "is_closed": question.is_closed,
            "closed_reason": question.closed_reason,
            "created_at": question.created_at,
            "profile_id": question.profile_id,
        },
        "answers": [
            {
                "id": a.id,
                "author_display": author_display(
                    display_names.get(a.instance_uuid), a.instance_uuid
                ),
                "is_vesana_team": a.is_vesana_team,
                "body_md": a.body_md,
                "vote_score": a.vote_score,
                "is_accepted": a.is_accepted,
            }
            for a in answers
        ],
        "duplicate_of": (
            {"id": duplicate_of.id, "title_text": duplicate_of.title_text}
            if duplicate_of is not None
            else None
        ),
    }
    return templates.TemplateResponse(request, "question.html", context)


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, instance: SessionInstance) -> HTMLResponse:
    """Render the profile upload form (the template gates on the session)."""
    return templates.TemplateResponse(
        request, "upload.html", {"instance": instance, "version": VERSION}
    )


@router.post("/upload")
def upload_submit(
    request: Request,
    db: DbDep,
    instance: SessionInstance,
    bundle: Annotated[UploadFile, File()],
    version_tag: Annotated[str | None, Form()] = None,
    changelog_md: Annotated[str | None, Form()] = None,
):
    """Handle a browser profile upload (session-cookie auth, multipart form).

    Parses the uploaded JSON bundle, runs the same validation + versioning
    service the machine API uses, then redirects to the new profile's page.
    """
    if instance is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign in required")

    def _form_error(msg: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "upload.html",
            {
                "instance": instance,
                "version": VERSION,
                "error": msg,
                "version_tag": version_tag,
                "changelog_md": changelog_md,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    raw = bundle.file.read()
    if len(raw) > 600_000:
        return _form_error("That file is too large.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _form_error(
            "That file is not valid JSON. Export the profile bundle from Vesana "
            "and upload the downloaded .json file."
        )

    try:
        validated = uploads_service.validate_bundle(parsed)
        profile, _version = uploads_service.create_or_version_profile(
            db,
            instance_uuid=instance.uuid,
            bundle=validated,
            version_tag=(version_tag or None),
            changelog_md=(changelog_md or None),
        )
        db.commit()
    except HTTPException as exc:
        db.rollback()
        return _form_error(str(exc.detail))

    return RedirectResponse(
        url=f"/p/{profile.id}?uploaded=1", status_code=status.HTTP_303_SEE_OTHER
    )


def _require_login(instance: Instance | None) -> Instance:
    if instance is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign in required")
    return instance


@router.post("/p/{profile_id}/vote")
def profile_vote(
    profile_id: str,
    db: DbDep,
    instance: SessionInstance,
    value: Annotated[int, Form()] = 0,
):
    """Up/down/clear the caller's vote on a profile (session-cookie auth)."""
    inst = _require_login(instance)
    clamped = 1 if value > 0 else (-1 if value < 0 else 0)
    cast_vote(db, instance_uuid=inst.uuid, target_type="profile", target_id=profile_id,
              value=clamped, reason=None)
    db.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/p/{profile_id}/comment")
def profile_comment(
    profile_id: str,
    db: DbDep,
    instance: SessionInstance,
    body_md: Annotated[str, Form()] = "",
    parent_id: Annotated[str | None, Form()] = None,
):
    """Post a comment (or reply) on a profile (session-cookie auth)."""
    inst = _require_login(instance)
    body = (body_md or "").strip()
    if body:
        create_comment(
            db,
            profile_id=profile_id,
            instance_uuid=inst.uuid,
            body_md=body,
            parent_id=(parent_id or None),
        )
        db.commit()
    return RedirectResponse(url=f"/p/{profile_id}#comments", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/questions/{question_id}/answer")
def question_answer(
    question_id: str,
    db: DbDep,
    instance: SessionInstance,
    body_md: Annotated[str, Form()] = "",
):
    """Post an answer to a question (session-cookie auth)."""
    inst = _require_login(instance)
    body = (body_md or "").strip()
    if body:
        qa_service.create_answer(
            db,
            question_id=question_id,
            instance_uuid=inst.uuid,
            body_md=body,
            is_vesana_team=False,
        )
        db.commit()
    return RedirectResponse(
        url=f"/questions/{question_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, instance: SessionInstance) -> HTMLResponse:
    """Account settings (display name). Template gates on the session."""
    return templates.TemplateResponse(
        request, "account.html", {"instance": instance, "version": VERSION}
    )


@router.post("/account")
def account_save(
    request: Request,
    db: DbDep,
    instance: SessionInstance,
    chosen_name: Annotated[str, Form()] = "",
):
    """Update the caller's community display name (session-cookie auth)."""
    inst = _require_login(instance)
    name = (chosen_name or "").strip()[:40]
    inst.chosen_name = name or None
    db.commit()
    # Keep the nav in sync without a re-login.
    request.session["display_name"] = inst.effective_name
    return RedirectResponse(url="/account?ok=1", status_code=status.HTTP_303_SEE_OTHER)
