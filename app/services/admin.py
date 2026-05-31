"""Community admin panel data + actions (C8).

This service powers the admin panel: moderation (reports), instance blocking,
promoting community/beta profiles to official, and an overview stats dashboard.
It REUSES the C3 upload-review service (``app.services.uploads``) for the
review-queue listing/approve/reject — that logic is NOT duplicated here.

Design notes:
* Pure-ish: queries + in-place mutations only. The ROUTERS commit (mirroring the
  rest of the codebase). Service functions ``flush`` so generated values are set.
* Report ``status`` is reused as the resolution column (``open`` -> ``resolved``
  / ``dismissed``); no new migration is needed (see the C8 changelog entry).
* Target previews are built carefully so the admin sees WHAT was reported
  without leaking private signals: a removed comment shows ``[entfernt]``, and
  no downvote reasons or secrets are ever included.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.answer import Answer
from app.models.community_event import CommunityEvent
from app.models.community_profile import CommunityProfile
from app.models.instance import Instance
from app.models.moderation_report import ModerationReport
from app.models.profile_comment import ProfileComment
from app.models.question import Question
from app.models.vote import Vote
from app.schemas.admin import AdminStats, InstanceItem, ReportItem

# How many characters of a reported body to show as a preview snippet.
PREVIEW_LEN = 160

# Valid resolution actions and the report status each maps to.
RESOLVE_ACTIONS = ("dismiss", "remove")
_ACTION_STATUS = {"dismiss": "dismissed", "remove": "resolved"}


def _snippet(text: str | None) -> str:
    """Return a short, single-line preview of a body of text."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) > PREVIEW_LEN:
        return collapsed[:PREVIEW_LEN].rstrip() + "…"
    return collapsed


# ---- Moderation: reports ---------------------------------------------------


def _target_preview(db: Session, target_type: str, target_id: str) -> str:
    """Build a safe preview of a reported target.

    Never leaks private data: a removed comment shows a placeholder, and no
    downvote reasons / tokens / secrets are ever included.
    """
    if target_type == "comment":
        comment = db.get(ProfileComment, target_id)
        if comment is None:
            return "[gelöscht]"
        if comment.is_removed:
            return "[entfernt]"
        return _snippet(comment.body_md)
    if target_type == "question":
        question = db.get(Question, target_id)
        if question is None:
            return "[gelöscht]"
        return _snippet(question.title_text)
    if target_type == "answer":
        answer = db.get(Answer, target_id)
        if answer is None:
            return "[gelöscht]"
        return _snippet(answer.body_md)
    if target_type == "profile":
        profile = db.get(CommunityProfile, target_id)
        if profile is None:
            return "[gelöscht]"
        return _snippet(profile.name)
    return ""


def _to_report_item(db: Session, report: ModerationReport) -> ReportItem:
    return ReportItem(
        id=report.id,
        target_type=report.target_type,
        target_id=report.target_id,
        reporter_uuid=report.instance_uuid,
        reason=report.reason,
        status=report.status,
        created_at=report.created_at,
        target_preview=_target_preview(db, report.target_type, report.target_id),
    )


def list_reports(db: Session, status_filter: str = "open") -> list[ReportItem]:
    """List moderation reports (default: only ``open``).

    Pass ``"all"`` for every report, or a specific status to filter.
    """
    stmt = select(ModerationReport)
    if status_filter != "all":
        stmt = stmt.where(ModerationReport.status == status_filter)
    stmt = stmt.order_by(ModerationReport.created_at.desc())
    reports = db.execute(stmt).scalars().all()
    return [_to_report_item(db, r) for r in reports]


def resolve_report(db: Session, report_id: str, action: str) -> ModerationReport:
    """Resolve a report. ``action`` is ``"dismiss"`` or ``"remove"``.

    * ``dismiss`` -> report.status = 'dismissed' (no action on the target).
    * ``remove``  -> act on the target (soft-remove / close) AND set
      report.status = 'resolved'.

    404 if the report is missing. A missing target is tolerated (the report is
    still resolved) since reports survive hard target deletion by design.
    """
    if action not in RESOLVE_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"action must be one of {RESOLVE_ACTIONS}",
        )
    report = db.get(ModerationReport, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    if action == "remove":
        _act_on_target(db, report.target_type, report.target_id)

    report.status = _ACTION_STATUS[action]
    db.flush()
    db.refresh(report)
    return report


def _act_on_target(db: Session, target_type: str, target_id: str) -> None:
    """Soft-remove / close the reported target (best-effort; missing is ok)."""
    if target_type == "comment":
        comment = db.get(ProfileComment, target_id)
        if comment is not None:
            comment.is_removed = True
    elif target_type == "question":
        question = db.get(Question, target_id)
        if question is not None:
            question.is_closed = True
            question.closed_reason = "removed by moderator"
    elif target_type == "answer":
        answer = db.get(Answer, target_id)
        if answer is not None:
            answer.is_accepted = False
            db.delete(answer)
    elif target_type == "profile":
        profile = db.get(CommunityProfile, target_id)
        if profile is not None:
            profile.is_removed = True
    db.flush()


# ---- Instances -------------------------------------------------------------


def list_instances(db: Session, limit: int = 100, offset: int = 0) -> list[InstanceItem]:
    """List instances (most-recently-seen first) with their uploaded count."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    instances = (
        db.execute(
            select(Instance).order_by(Instance.last_seen_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    if not instances:
        return []

    # One grouped COUNT for the uploaded-profile stat instead of N queries.
    uuids = [i.uuid for i in instances]
    counts_rows = db.execute(
        select(
            CommunityProfile.uploader_instance_uuid,
            func.count(CommunityProfile.id),
        )
        .where(CommunityProfile.uploader_instance_uuid.in_(uuids))
        .group_by(CommunityProfile.uploader_instance_uuid)
    ).all()
    counts = {uuid_: int(n) for uuid_, n in counts_rows}

    return [
        InstanceItem(
            uuid=i.uuid,
            display_name=i.display_name,
            is_blocked=i.is_blocked,
            joined_at=i.joined_at,
            last_seen_at=i.last_seen_at,
            uploaded_count=counts.get(i.uuid, 0),
        )
        for i in instances
    ]


def set_blocked(db: Session, instance_uuid: str, blocked: bool) -> Instance:
    """Block or unblock an instance. 404 if the instance is unknown."""
    instance = db.get(Instance, instance_uuid)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance not found")
    instance.is_blocked = blocked
    db.flush()
    db.refresh(instance)
    return instance


# ---- Profiles: promote -----------------------------------------------------


def promote_to_official(db: Session, profile_id: str) -> CommunityProfile:
    """Promote a profile to the ``official`` tier (also marks it approved).

    404 if the profile is missing or removed.
    """
    profile = db.get(CommunityProfile, profile_id)
    if profile is None or profile.is_removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    profile.tier = "official"
    profile.approved = True
    profile.review_status = "approved"
    profile.approved_at = datetime.now(UTC)
    profile.approved_by = "admin"
    profile.rejection_reason = None
    db.flush()
    db.refresh(profile)
    return profile


_VALID_TIERS = ("official", "beta", "community")


def set_tier(db: Session, profile_id: str, tier: str) -> CommunityProfile:
    """Set a profile's tier to official/beta/community (admin power).

    Curated tiers (official/beta) are also marked approved; community keeps its
    existing review status. 404 if missing/removed, 400 for an invalid tier.
    """
    if tier not in _VALID_TIERS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid tier")
    profile = db.get(CommunityProfile, profile_id)
    if profile is None or profile.is_removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    profile.tier = tier
    if tier in ("official", "beta"):
        profile.approved = True
        profile.review_status = "approved"
        profile.approved_at = datetime.now(UTC)
        profile.approved_by = "admin"
        profile.rejection_reason = None
    db.flush()
    db.refresh(profile)
    return profile


def delete_profile(db: Session, profile_id: str) -> CommunityProfile:
    """Soft-delete a profile (sets ``is_removed``). 404 if missing/already gone."""
    profile = db.get(CommunityProfile, profile_id)
    if profile is None or profile.is_removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    profile.is_removed = True
    db.flush()
    return profile


def list_all_profiles(db: Session, limit: int = 500) -> list[CommunityProfile]:
    """List ALL non-removed profiles for admin management (newest first)."""
    limit = max(1, min(limit, 1000))
    profiles = (
        db.execute(
            select(CommunityProfile)
            .where(CommunityProfile.is_removed.is_(False))
            .order_by(CommunityProfile.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(profiles)


def list_promotable(db: Session, limit: int = 200) -> list[CommunityProfile]:
    """List non-official, non-removed profiles (beta + community) for promotion.

    Beta first, then community; newest first within each tier.
    """
    limit = max(1, min(limit, 500))
    profiles = (
        db.execute(
            select(CommunityProfile)
            .where(
                CommunityProfile.is_removed.is_(False),
                CommunityProfile.tier.in_(("beta", "community")),
            )
            .order_by(CommunityProfile.tier, CommunityProfile.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(profiles)


# ---- Stats dashboard -------------------------------------------------------


def _count(db: Session, stmt) -> int:
    return int(db.execute(stmt).scalar_one() or 0)


def stats(db: Session) -> AdminStats:
    """Build the admin overview dashboard counts with efficient aggregates."""
    instances_total = _count(db, select(func.count()).select_from(Instance))
    instances_blocked = _count(
        db, select(func.count()).select_from(Instance).where(Instance.is_blocked.is_(True))
    )

    profiles_total = _count(
        db,
        select(func.count())
        .select_from(CommunityProfile)
        .where(CommunityProfile.is_removed.is_(False)),
    )
    tier_rows = db.execute(
        select(CommunityProfile.tier, func.count(CommunityProfile.id))
        .where(CommunityProfile.is_removed.is_(False))
        .group_by(CommunityProfile.tier)
    ).all()
    by_tier = {tier: int(n) for tier, n in tier_rows}
    profiles_by_tier = {
        "official": by_tier.get("official", 0),
        "beta": by_tier.get("beta", 0),
        "community": by_tier.get("community", 0),
    }
    profiles_pending = _count(
        db,
        select(func.count())
        .select_from(CommunityProfile)
        .where(
            CommunityProfile.is_removed.is_(False),
            CommunityProfile.review_status == "pending",
        ),
    )

    downloads_total = _count(
        db,
        select(func.coalesce(func.sum(CommunityProfile.download_count), 0)).where(
            CommunityProfile.is_removed.is_(False)
        ),
    )
    imports_total = _count(
        db,
        select(func.coalesce(func.sum(CommunityProfile.import_count), 0)).where(
            CommunityProfile.is_removed.is_(False)
        ),
    )

    votes_total = _count(db, select(func.count()).select_from(Vote))

    questions_total = _count(db, select(func.count()).select_from(Question))
    # Open = not closed AND has no accepted answer.
    accepted_qids = select(Answer.question_id).where(Answer.is_accepted.is_(True))
    questions_open = _count(
        db,
        select(func.count())
        .select_from(Question)
        .where(
            Question.is_closed.is_(False),
            Question.id.not_in(accepted_qids),
        ),
    )

    reports_open = _count(
        db,
        select(func.count()).select_from(ModerationReport).where(ModerationReport.status == "open"),
    )
    events_total = _count(db, select(func.count()).select_from(CommunityEvent))

    return AdminStats(
        instances_total=instances_total,
        instances_blocked=instances_blocked,
        profiles_total=profiles_total,
        profiles_by_tier=profiles_by_tier,
        profiles_pending=profiles_pending,
        downloads_total=downloads_total,
        imports_total=imports_total,
        votes_total=votes_total,
        questions_total=questions_total,
        questions_open=questions_open,
        reports_open=reports_open,
        events_total=events_total,
    )
