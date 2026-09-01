"""Daten + Aktionen des Admin-Bereichs.

Reine Abfragen und In-Place-Mutationen; die ROUTER committen (wie überall im
Code) und schreiben das Admin-Protokoll (``app.services.admin_audit``). Die
Review-Logik (Freigabe/Ablehnung + Benachrichtigung) bleibt in
``app.services.uploads`` und wird hier nur benutzt, nie kopiert.

Alle Listen sind seitenweise (``Page``) und durchsuchbar — der Hub hat heute
gut hundert Profile und ein Dutzend Instanzen, die Oberfläche muss aber auch
bei tausend noch bedienbar sein.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, defer

from app.identity import public_name
from app.models.answer import Answer
from app.models.community_event import CommunityEvent
from app.models.community_profile import CommunityProfile
from app.models.community_profile_version import CommunityProfileVersion
from app.models.instance import Instance
from app.models.library_icon import LibraryIcon
from app.models.moderation_report import ModerationReport
from app.models.profile_comment import ProfileComment
from app.models.question import Question
from app.models.vote import Vote
from app.schemas.admin import AdminStats, InstanceItem, ReportItem
from app.schemas.profile import VESANA_TEAM_UPLOADER

# How many characters of a reported body to show as a preview snippet.
PREVIEW_LEN = 160

# Valid resolution actions and the report status each maps to.
RESOLVE_ACTIONS = ("dismiss", "remove")
_ACTION_STATUS = {"dismiss": "dismissed", "remove": "resolved"}

PER_PAGE = 40

TIERS = ("official", "beta", "community")
REVIEW_STATES = ("pending", "approved", "rejected")
REPORT_STATES = ("open", "resolved", "dismissed")

PROFILE_SORTS = ("updated", "name", "downloads", "score", "created")
INSTANCE_SORTS = ("seen", "joined", "name", "uploads")


@dataclass
class Page:
    items: list[Any]
    total: int
    page: int
    per_page: int
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.per_page - 1) // self.per_page)

    @property
    def start(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.per_page + 1

    @property
    def end(self) -> int:
        return min(self.total, self.page * self.per_page)


def _paginate(db: Session, stmt, page: int, per_page: int = PER_PAGE) -> Page:
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    page = max(1, page)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = db.execute(stmt.limit(per_page).offset((page - 1) * per_page)).scalars().all()
    return Page(items=list(rows), total=total, page=page, per_page=per_page)


def _snippet(text_value: str | None) -> str:
    """Return a short, single-line preview of a body of text."""
    if not text_value:
        return ""
    collapsed = " ".join(text_value.split())
    if len(collapsed) > PREVIEW_LEN:
        return collapsed[:PREVIEW_LEN].rstrip() + "…"
    return collapsed


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


# ---- Namen ----------------------------------------------------------------------


def names_for(db: Session, uuids: set[str] | list[str]) -> dict[str, str]:
    """uuid → öffentlicher Name (chosen → SSO → @handle) in EINER Abfrage."""
    wanted = {u for u in uuids if u}
    if not wanted:
        return {}
    rows = db.execute(
        select(Instance.uuid, Instance.display_name, Instance.chosen_name).where(
            Instance.uuid.in_(sorted(wanted))
        )
    ).all()
    out = {u: public_name(d, u, c) for u, d, c in rows}
    for u in wanted:
        out.setdefault(u, public_name(None, u))
    return out


def uploader_name(db: Session, profile: CommunityProfile) -> str:
    if not profile.uploader_instance_uuid:
        return VESANA_TEAM_UPLOADER
    return names_for(db, {profile.uploader_instance_uuid}).get(
        profile.uploader_instance_uuid, VESANA_TEAM_UPLOADER
    )


# ---- Moderation: reports --------------------------------------------------------


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


def _target_href(db: Session, target_type: str, target_id: str) -> str | None:
    """Öffentlicher Link zum gemeldeten Ziel (damit der Admin es im Kontext sieht)."""
    if target_type == "profile":
        return f"/p/{target_id}"
    if target_type == "comment":
        comment = db.get(ProfileComment, target_id)
        return f"/p/{comment.profile_id}?tab=comments" if comment else None
    if target_type == "question":
        return f"/questions/{target_id}"
    if target_type == "answer":
        answer = db.get(Answer, target_id)
        return f"/questions/{answer.question_id}" if answer else None
    return None


def _to_report_item(db: Session, report: ModerationReport, names: dict[str, str]) -> ReportItem:
    return ReportItem(
        id=report.id,
        target_type=report.target_type,
        target_id=report.target_id,
        reporter_uuid=report.instance_uuid,
        reporter_display=names.get(report.instance_uuid, public_name(None, report.instance_uuid)),
        reason=report.reason,
        status=report.status,
        created_at=report.created_at,
        target_preview=_target_preview(db, report.target_type, report.target_id),
        target_href=_target_href(db, report.target_type, report.target_id),
    )


def list_reports(db: Session, status_filter: str = "open") -> list[ReportItem]:
    """List moderation reports (default: only ``open``).

    Pass ``"all"`` for every report, or a specific status to filter.
    """
    return list_reports_page(db, status_filter, page=1, per_page=500).items


def list_reports_page(
    db: Session, status_filter: str = "open", *, page: int = 1, per_page: int = PER_PAGE
) -> Page:
    stmt = select(ModerationReport)
    if status_filter != "all":
        stmt = stmt.where(ModerationReport.status == status_filter)
    stmt = stmt.order_by(ModerationReport.created_at.desc())
    result = _paginate(db, stmt, page, per_page)
    names = names_for(db, {r.instance_uuid for r in result.items})
    result.items = [_to_report_item(db, r, names) for r in result.items]
    return result


def report_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(ModerationReport.status, func.count()).group_by(ModerationReport.status)
    ).all()
    counts = {s: 0 for s in REPORT_STATES}
    for s, n in rows:
        counts[s] = int(n)
    counts["all"] = sum(counts.values())
    return counts


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
        raise _not_found("Report")

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
            question = db.get(Question, answer.question_id)
            db.delete(answer)
            db.flush()
            if question is not None:
                _recount_answers(db, question)
    elif target_type == "profile":
        profile = db.get(CommunityProfile, target_id)
        if profile is not None:
            profile.is_removed = True
    db.flush()


# ---- Kommentare -----------------------------------------------------------------


def set_comment_removed(db: Session, comment_id: str, removed: bool) -> ProfileComment:
    comment = db.get(ProfileComment, comment_id)
    if comment is None:
        raise _not_found("Comment")
    comment.is_removed = removed
    db.flush()
    return comment


def profile_comments(db: Session, profile_id: str) -> list[dict]:
    rows = (
        db.execute(
            select(ProfileComment)
            .where(ProfileComment.profile_id == profile_id)
            .order_by(ProfileComment.created_at.desc())
        )
        .scalars()
        .all()
    )
    names = names_for(db, {c.instance_uuid for c in rows})
    return [
        {
            "id": c.id,
            "author": names.get(c.instance_uuid, ""),
            "author_uuid": c.instance_uuid,
            "body": _snippet(c.body_md),
            "is_removed": c.is_removed,
            "is_reply": c.parent_id is not None,
            "score": c.vote_score,
            "created_at": c.created_at,
        }
        for c in rows
    ]


# ---- Instanzen ------------------------------------------------------------------


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
    return _instance_items(db, list(instances))


def _upload_counts(db: Session, uuids: list[str]) -> dict[str, int]:
    if not uuids:
        return {}
    rows = db.execute(
        select(CommunityProfile.uploader_instance_uuid, func.count(CommunityProfile.id))
        .where(
            CommunityProfile.uploader_instance_uuid.in_(uuids),
            CommunityProfile.is_removed.is_(False),
        )
        .group_by(CommunityProfile.uploader_instance_uuid)
    ).all()
    return {u: int(n) for u, n in rows}


def _instance_items(db: Session, instances: list[Instance]) -> list[InstanceItem]:
    if not instances:
        return []
    counts = _upload_counts(db, [i.uuid for i in instances])
    return [
        InstanceItem(
            uuid=i.uuid,
            display_name=i.display_name,
            public_name=public_name(i.display_name, i.uuid, i.chosen_name),
            chosen_name=i.chosen_name,
            is_blocked=i.is_blocked,
            blocked_reason=i.blocked_reason,
            blocked_at=i.blocked_at,
            joined_at=i.joined_at,
            last_seen_at=i.last_seen_at,
            uploaded_count=counts.get(i.uuid, 0),
        )
        for i in instances
    ]


def search_instances(
    db: Session,
    *,
    q: str | None = None,
    state: str | None = None,
    sort: str = "seen",
    page: int = 1,
) -> Page:
    stmt = select(Instance)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Instance.display_name.ilike(like),
                Instance.chosen_name.ilike(like),
                Instance.uuid.ilike(like),
            )
        )
    if state == "blocked":
        stmt = stmt.where(Instance.is_blocked.is_(True))
    elif state == "active":
        stmt = stmt.where(Instance.is_blocked.is_(False))
    if sort == "joined":
        stmt = stmt.order_by(Instance.joined_at.desc())
    elif sort == "name":
        stmt = stmt.order_by(func.lower(func.coalesce(Instance.chosen_name, Instance.display_name)))
    else:
        stmt = stmt.order_by(Instance.last_seen_at.desc())
    result = _paginate(db, stmt, page)
    items = _instance_items(db, result.items)
    if sort == "uploads":
        items.sort(key=lambda i: i.uploaded_count, reverse=True)
    result.items = items
    return result


def instance_counts(db: Session) -> dict[str, int]:
    total = int(db.execute(select(func.count()).select_from(Instance)).scalar_one())
    blocked = int(
        db.execute(
            select(func.count()).select_from(Instance).where(Instance.is_blocked.is_(True))
        ).scalar_one()
    )
    return {"all": total, "blocked": blocked, "active": total - blocked}


def instance_detail(db: Session, instance_uuid: str) -> dict:
    instance = db.get(Instance, instance_uuid)
    if instance is None:
        raise _not_found("Instance")
    profiles = (
        db.execute(
            select(CommunityProfile)
            .where(CommunityProfile.uploader_instance_uuid == instance_uuid)
            .order_by(CommunityProfile.updated_at.desc())
        )
        .scalars()
        .all()
    )
    comments = (
        db.execute(
            select(ProfileComment)
            .where(ProfileComment.instance_uuid == instance_uuid)
            .order_by(ProfileComment.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    questions = (
        db.execute(
            select(Question)
            .where(Question.instance_uuid == instance_uuid)
            .order_by(Question.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    answers = (
        db.execute(
            select(Answer)
            .where(Answer.instance_uuid == instance_uuid)
            .order_by(Answer.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    reports_by = int(
        db.execute(
            select(func.count())
            .select_from(ModerationReport)
            .where(ModerationReport.instance_uuid == instance_uuid)
        ).scalar_one()
    )
    votes = int(
        db.execute(
            select(func.count()).select_from(Vote).where(Vote.instance_uuid == instance_uuid)
        ).scalar_one()
    )
    return {
        "instance": instance,
        "public_name": public_name(instance.display_name, instance.uuid, instance.chosen_name),
        "profiles": list(profiles),
        "comments": list(comments),
        "questions": list(questions),
        "answers": list(answers),
        "reports_by": reports_by,
        "votes": votes,
    }


def set_blocked(
    db: Session, instance_uuid: str, blocked: bool, reason: str | None = None
) -> Instance:
    """Block or unblock an instance. 404 if the instance is unknown."""
    instance = db.get(Instance, instance_uuid)
    if instance is None:
        raise _not_found("Instance")
    instance.is_blocked = blocked
    if blocked:
        instance.blocked_reason = (reason or "").strip() or None
        instance.blocked_at = datetime.now(UTC)
    else:
        instance.blocked_reason = None
        instance.blocked_at = None
    db.flush()
    db.refresh(instance)
    return instance


def reset_chosen_name(db: Session, instance_uuid: str) -> Instance:
    instance = db.get(Instance, instance_uuid)
    if instance is None:
        raise _not_found("Instance")
    instance.chosen_name = None
    db.flush()
    return instance


# ---- Profile --------------------------------------------------------------------


def promote_to_official(db: Session, profile_id: str) -> CommunityProfile:
    """Promote a profile to the ``official`` tier (also marks it approved)."""
    return set_tier(db, profile_id, "official")


def set_tier(db: Session, profile_id: str, tier: str) -> CommunityProfile:
    """Set a profile's tier to official/beta/community (admin power).

    Curated tiers (official/beta) are also marked approved; community keeps its
    existing review status. 404 if missing/removed, 400 for an invalid tier.
    """
    if tier not in TIERS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid tier")
    profile = db.get(CommunityProfile, profile_id)
    if profile is None or profile.is_removed:
        raise _not_found("Profile")
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
        raise _not_found("Profile")
    profile.is_removed = True
    db.flush()
    return profile


def restore_profile(db: Session, profile_id: str) -> CommunityProfile:
    profile = db.get(CommunityProfile, profile_id)
    if profile is None or not profile.is_removed:
        raise _not_found("Profile")
    profile.is_removed = False
    db.flush()
    return profile


def get_profile_any(db: Session, profile_id: str) -> CommunityProfile:
    """Auch gelöschte Profile — der Admin muss sie sehen können (Wiederherstellen)."""
    profile = db.get(CommunityProfile, profile_id)
    if profile is None:
        raise _not_found("Profile")
    return profile


# Felder, die der Admin an einem Profil bearbeiten darf (alles andere kommt aus
# dem Bundle oder ist abgeleitet).
EDITABLE_FIELDS = (
    "name",
    "vendor",
    "category",
    "icon",
    "description_md",
    "vesana_min_version",
    "tags",
    "requires_agent",
    "requires_collector",
    "requires_snmp",
    "requires_ssh",
    "requires_api_token",
)


def update_profile(db: Session, profile_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """Metadaten setzen; liefert die tatsächlich geänderten Felder (vorher → nachher)."""
    profile = get_profile_any(db, profile_id)
    changed: dict[str, Any] = {}
    for key in EDITABLE_FIELDS:
        if key not in values:
            continue
        new = values[key]
        if isinstance(new, str):
            new = new.strip() or None
        if key == "name" and not new:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name required")
        old = getattr(profile, key)
        if old != new:
            changed[key] = {"from": old, "to": new}
            setattr(profile, key, new)
    if changed:
        profile.updated_at = datetime.now(UTC)
        db.flush()
    return changed


def set_current_version(db: Session, profile_id: str, version_id: str) -> CommunityProfileVersion:
    """Eine andere Bundle-Version zur aktuellen machen.

    ACHTUNG (Merkregel aus 08/2026): eine neue aktuelle Version ist ein Eingriff
    bei JEDER importierenden Instanz („Update verfügbar"). Die Oberfläche sagt
    das vor dem Klick; hier wird es nur ausgeführt.
    """
    profile = get_profile_any(db, profile_id)
    target = None
    for version in profile.versions:
        if version.id == version_id:
            target = version
    if target is None:
        raise _not_found("Version")
    for version in profile.versions:
        version.is_current = version.id == version_id
    profile.latest_version_id = target.id
    profile.updated_at = datetime.now(UTC)
    db.flush()
    return target


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
    """List non-official, non-removed profiles (beta + community) for promotion."""
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


def search_profiles(
    db: Session,
    *,
    q: str | None = None,
    tier: str | None = None,
    category: str | None = None,
    vendor: str | None = None,
    review: str | None = None,
    source: str | None = None,  # "team" | "community"
    removed: bool = False,
    scripts: bool | None = None,
    sort: str = "updated",
    page: int = 1,
) -> Page:
    stmt = select(CommunityProfile).where(CommunityProfile.is_removed.is_(removed))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CommunityProfile.name.ilike(like),
                CommunityProfile.vendor.ilike(like),
                CommunityProfile.category.ilike(like),
                CommunityProfile.id == q.strip(),
                func.array_to_string(CommunityProfile.tags, " ").ilike(like),
            )
        )
    if tier in TIERS:
        stmt = stmt.where(CommunityProfile.tier == tier)
    if category:
        stmt = stmt.where(CommunityProfile.category == category)
    if vendor:
        stmt = stmt.where(CommunityProfile.vendor == vendor)
    if review in REVIEW_STATES:
        stmt = stmt.where(CommunityProfile.review_status == review)
    if source == "team":
        stmt = stmt.where(CommunityProfile.uploader_instance_uuid.is_(None))
    elif source == "community":
        stmt = stmt.where(CommunityProfile.uploader_instance_uuid.is_not(None))
    if scripts is not None:
        stmt = stmt.where(CommunityProfile.has_scripts.is_(scripts))

    if sort == "name":
        stmt = stmt.order_by(func.lower(CommunityProfile.name))
    elif sort == "downloads":
        stmt = stmt.order_by(CommunityProfile.download_count.desc(), CommunityProfile.name)
    elif sort == "score":
        stmt = stmt.order_by(CommunityProfile.vote_score.desc(), CommunityProfile.name)
    elif sort == "created":
        stmt = stmt.order_by(CommunityProfile.created_at.desc())
    else:
        stmt = stmt.order_by(CommunityProfile.updated_at.desc())

    result = _paginate(db, stmt, page)
    result.extra["names"] = names_for(
        db, {p.uploader_instance_uuid for p in result.items if p.uploader_instance_uuid}
    )
    return result


def profile_facets(db: Session) -> dict[str, list[str]]:
    def distinct(column) -> list[str]:
        rows = db.execute(
            select(column).where(column.is_not(None)).distinct().order_by(column)
        ).scalars()
        return [r for r in rows if r]

    return {
        "categories": distinct(CommunityProfile.category),
        "vendors": distinct(CommunityProfile.vendor),
    }


def profile_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(CommunityProfile.tier, func.count())
        .where(CommunityProfile.is_removed.is_(False))
        .group_by(CommunityProfile.tier)
    ).all()
    counts = {t: 0 for t in TIERS}
    for t, n in rows:
        counts[t] = int(n)
    counts["all"] = sum(counts.values())
    counts["removed"] = int(
        db.execute(
            select(func.count())
            .select_from(CommunityProfile)
            .where(CommunityProfile.is_removed.is_(True))
        ).scalar_one()
    )
    counts["pending"] = int(
        db.execute(
            select(func.count())
            .select_from(CommunityProfile)
            .where(
                CommunityProfile.is_removed.is_(False),
                CommunityProfile.review_status == "pending",
            )
        ).scalar_one()
    )
    return counts


def review_counts(db: Session) -> dict[str, int]:
    """Zähler der Review-Chips — dieselbe Grundmenge wie die Liste: alles mit Uploader."""
    rows = db.execute(
        select(CommunityProfile.review_status, func.count())
        .where(
            CommunityProfile.is_removed.is_(False),
            CommunityProfile.uploader_instance_uuid.is_not(None),
        )
        .group_by(CommunityProfile.review_status)
    ).all()
    counts = {s: 0 for s in REVIEW_STATES}
    for s, n in rows:
        counts[s] = int(n)
    counts["all"] = sum(counts.values())
    return counts


def profile_report_count(db: Session, profile_id: str) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(ModerationReport)
            .where(
                ModerationReport.target_type == "profile",
                ModerationReport.target_id == profile_id,
                ModerationReport.status == "open",
            )
        ).scalar_one()
    )


def bundle_scripts(bundle: dict | None) -> list[dict]:
    """Die im Bundle mitgelieferten Scripts (Name, Interpreter, Rumpf) — für die Prüfung."""
    if not isinstance(bundle, dict):
        return []
    out: list[dict] = []
    for raw in bundle.get("scripts") or []:
        if not isinstance(raw, dict):
            continue
        body = raw.get("script_body") or raw.get("body") or ""
        out.append(
            {
                "name": str(raw.get("name") or "—"),
                "interpreter": str(raw.get("interpreter") or "—"),
                "expected_output": raw.get("expected_output"),
                "body": body if isinstance(body, str) else "",
                "lines": (body.count("\n") + 1) if isinstance(body, str) and body else 0,
            }
        )
    return out


def findings_by_line(scripts: list[dict], findings: list[dict]) -> list[dict]:
    """Fundstellen des Script-Gates auf Zeilen des Rumpfs abbilden (für die Anzeige)."""
    from app.services.uploads import SCRIPT_MARKERS

    for script in scripts:
        hits: list[dict] = []
        for idx, line in enumerate(script["body"].splitlines(), start=1):
            lowered = line.lower()
            markers = [m for m in SCRIPT_MARKERS if m in lowered]
            if markers:
                hits.append({"line": idx, "text": line, "markers": markers})
        script["hits"] = hits
    return scripts


# ---- Fragen & Antworten ---------------------------------------------------------


def _recount_answers(db: Session, question: Question) -> None:
    total = db.execute(
        select(func.count()).select_from(Answer).where(Answer.question_id == question.id)
    ).scalar_one()
    question.answer_count = int(total)
    db.flush()


def search_questions(
    db: Session,
    *,
    q: str | None = None,
    state: str | None = None,  # open | closed | unanswered
    page: int = 1,
) -> Page:
    stmt = select(Question)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Question.title_text.ilike(like), Question.body_md.ilike(like)))
    if state == "open":
        stmt = stmt.where(Question.is_closed.is_(False))
    elif state == "closed":
        stmt = stmt.where(Question.is_closed.is_(True))
    elif state == "unanswered":
        stmt = stmt.where(Question.is_closed.is_(False), Question.answer_count == 0)
    stmt = stmt.order_by(Question.created_at.desc())
    result = _paginate(db, stmt, page)
    result.extra["names"] = names_for(db, {qn.instance_uuid for qn in result.items})
    return result


def question_counts(db: Session) -> dict[str, int]:
    total = int(db.execute(select(func.count()).select_from(Question)).scalar_one())
    closed = int(
        db.execute(
            select(func.count()).select_from(Question).where(Question.is_closed.is_(True))
        ).scalar_one()
    )
    unanswered = int(
        db.execute(
            select(func.count())
            .select_from(Question)
            .where(Question.is_closed.is_(False), Question.answer_count == 0)
        ).scalar_one()
    )
    return {"all": total, "open": total - closed, "closed": closed, "unanswered": unanswered}


def get_question(db: Session, question_id: str) -> Question:
    question = db.get(Question, question_id)
    if question is None:
        raise _not_found("Question")
    return question


def close_question(db: Session, question_id: str, reason: str | None) -> Question:
    question = get_question(db, question_id)
    question.is_closed = True
    question.closed_reason = (reason or "").strip() or "closed by moderator"
    db.flush()
    return question


def reopen_question(db: Session, question_id: str) -> Question:
    question = get_question(db, question_id)
    question.is_closed = False
    question.closed_reason = None
    question.duplicate_of_id = None
    db.flush()
    return question


def set_question_team(db: Session, question_id: str, flag: bool) -> Question:
    question = get_question(db, question_id)
    question.is_vesana_team = flag
    db.flush()
    return question


def delete_answer(db: Session, answer_id: str) -> Question:
    answer = db.get(Answer, answer_id)
    if answer is None:
        raise _not_found("Answer")
    question = get_question(db, answer.question_id)
    db.delete(answer)
    db.flush()
    _recount_answers(db, question)
    return question


def set_answer_team(db: Session, answer_id: str, flag: bool) -> Answer:
    answer = db.get(Answer, answer_id)
    if answer is None:
        raise _not_found("Answer")
    answer.is_vesana_team = flag
    db.flush()
    return answer


# ---- Icons ----------------------------------------------------------------------


def icon_overview(db: Session) -> dict:
    rows = db.execute(select(LibraryIcon.source, func.count()).group_by(LibraryIcon.source)).all()
    sources = {src: int(n) for src, n in rows}
    last = db.execute(select(func.max(LibraryIcon.updated_at))).scalar()
    size = db.execute(select(func.coalesce(func.sum(LibraryIcon.file_size_bytes), 0))).scalar()
    dark = int(
        db.execute(
            select(func.count())
            .select_from(LibraryIcon)
            .where(LibraryIcon.dark_sha256.is_not(None))
        ).scalar_one()
    )
    # Hersteller der Profile, für die die Bibliothek KEIN Logo hat (Kuratier-Liste).
    vendors = [
        v
        for v in db.execute(
            select(CommunityProfile.vendor)
            .where(CommunityProfile.vendor.is_not(None), CommunityProfile.is_removed.is_(False))
            .distinct()
        ).scalars()
        if v and v.strip().lower() != "generic"
    ]
    slugs = {v.strip().lower() for v in vendors}
    found = set(
        db.execute(select(LibraryIcon.slug).where(LibraryIcon.slug.in_(sorted(slugs)))).scalars()
    )
    missing = sorted({v for v in vendors if v.strip().lower() not in found}, key=str.lower)
    return {
        "total": sum(sources.values()),
        "sources": sources,
        "last_synced_at": last,
        "size_bytes": int(size or 0),
        "with_dark": dark,
        "vendors_total": len(slugs),
        "vendors_missing": missing,
    }


def search_icons(
    db: Session, *, q: str | None = None, source: str | None = None, page: int = 1
) -> Page:
    stmt = select(LibraryIcon).options(defer(LibraryIcon.body), defer(LibraryIcon.dark_body))
    if source:
        stmt = stmt.where(LibraryIcon.source == source)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(LibraryIcon.slug).like(like),
                func.lower(LibraryIcon.name).like(like),
                func.lower(func.array_to_string(LibraryIcon.aliases, " ")).like(like),
            )
        )
    stmt = stmt.order_by(func.lower(LibraryIcon.name))
    return _paginate(db, stmt, page, per_page=60)


# ---- Übersicht ------------------------------------------------------------------


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


def tasks(db: Session) -> dict[str, int]:
    """„Was braucht mich" — die Zahlen, die eine Handlung verlangen."""
    return {
        "pending": _count(
            db,
            select(func.count())
            .select_from(CommunityProfile)
            .where(
                CommunityProfile.is_removed.is_(False),
                CommunityProfile.review_status == "pending",
            ),
        ),
        "reports": _count(
            db,
            select(func.count())
            .select_from(ModerationReport)
            .where(ModerationReport.status == "open"),
        ),
        "unanswered": _count(
            db,
            select(func.count())
            .select_from(Question)
            .where(Question.is_closed.is_(False), Question.answer_count == 0),
        ),
        "comments_total": _count(
            db,
            select(func.count())
            .select_from(ProfileComment)
            .where(ProfileComment.is_removed.is_(False)),
        ),
    }


def activity(db: Session, limit: int = 20) -> list[dict]:
    """Jüngste Bewegung in der Community — ein Strom über alle Tabellen."""
    events: list[dict] = []
    for p in (
        db.execute(
            select(CommunityProfile)
            .where(CommunityProfile.uploader_instance_uuid.is_not(None))
            .order_by(CommunityProfile.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    ):
        events.append(
            {
                "kind": "upload",
                "at": p.created_at,
                "actor": p.uploader_instance_uuid,
                "title": p.name,
                "href": f"/admin/profiles/{p.id}",
                "extra": p.review_status,
            }
        )
    for v in (
        db.execute(
            select(CommunityProfileVersion)
            .order_by(CommunityProfileVersion.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    ):
        events.append(
            {
                "kind": "version",
                "at": v.created_at,
                "actor": v.profile.uploader_instance_uuid if v.profile else None,
                "title": f"{v.profile.name if v.profile else '?'} {v.version_tag}",
                "href": f"/admin/profiles/{v.profile_id}",
                "extra": None,
            }
        )
    for c in (
        db.execute(select(ProfileComment).order_by(ProfileComment.created_at.desc()).limit(limit))
        .scalars()
        .all()
    ):
        events.append(
            {
                "kind": "comment",
                "at": c.created_at,
                "actor": c.instance_uuid,
                "title": _snippet(c.body_md),
                "href": f"/p/{c.profile_id}?tab=comments",
                "extra": "removed" if c.is_removed else None,
            }
        )
    for qn in (
        db.execute(select(Question).order_by(Question.created_at.desc()).limit(limit))
        .scalars()
        .all()
    ):
        events.append(
            {
                "kind": "question",
                "at": qn.created_at,
                "actor": qn.instance_uuid,
                "title": qn.title_text,
                "href": f"/questions/{qn.id}",
                "extra": None,
            }
        )
    for a in db.execute(select(Answer).order_by(Answer.created_at.desc()).limit(limit)).scalars():
        events.append(
            {
                "kind": "answer",
                "at": a.created_at,
                "actor": a.instance_uuid,
                "title": _snippet(a.body_md),
                "href": f"/questions/{a.question_id}",
                "extra": None,
            }
        )
    for r in (
        db.execute(
            select(ModerationReport).order_by(ModerationReport.created_at.desc()).limit(limit)
        )
        .scalars()
        .all()
    ):
        events.append(
            {
                "kind": "report",
                "at": r.created_at,
                "actor": r.instance_uuid,
                "title": _snippet(r.reason),
                "href": "/admin/moderation",
                "extra": r.status,
            }
        )
    for i in db.execute(
        select(Instance).order_by(Instance.joined_at.desc()).limit(limit)
    ).scalars():
        events.append(
            {
                "kind": "joined",
                "at": i.joined_at,
                "actor": i.uuid,
                "title": public_name(i.display_name, i.uuid, i.chosen_name),
                "href": f"/admin/instances/{i.uuid}",
                "extra": None,
            }
        )
    events.sort(key=lambda e: e["at"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    events = events[:limit]
    names = names_for(db, {e["actor"] for e in events if e["actor"]})
    for e in events:
        e["actor_name"] = names.get(e["actor"], "") if e["actor"] else VESANA_TEAM_UPLOADER
    return events


def system_info(db: Session) -> dict:
    head = None
    try:
        head = db.execute(text("SELECT version_num FROM community.alembic_version")).scalar()
    except Exception:  # noqa: BLE001 — Anzeige, nie ein Fehler auf der Übersicht
        db.rollback()
    icons = db.execute(select(func.count()).select_from(LibraryIcon)).scalar_one()
    icons_last = db.execute(select(func.max(LibraryIcon.updated_at))).scalar()
    events_unread = _count(
        db,
        select(func.count()).select_from(CommunityEvent).where(CommunityEvent.is_read.is_(False)),
    )
    return {
        "alembic_head": head,
        "icons": int(icons),
        "icons_synced_at": icons_last,
        "events_unread": events_unread,
    }
