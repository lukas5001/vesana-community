"""Admin-Protokoll: schreiben + lesen (``admin_audit_log``).

``record`` wird von JEDEM schreibenden Admin-Handler aufgerufen — in derselben
Session wie die Aktion, damit Protokoll und Änderung zusammen committen (oder
zusammen scheitern). Ein Protokoll, das erst nach dem Commit geschrieben wird,
fehlt genau dann, wenn man es braucht.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.admin_audit import AdminAuditLog

# Bekannte Aktionen (Anzeige-Labels in i18n unter ``audit.<action>``). Neue
# Aktionen hier eintragen — der Quell-Anker prüft, dass jede eine Übersetzung hat.
ACTIONS = (
    "auth.login",
    "auth.login_failed",
    "auth.logout",
    "auth.2fa_enabled",
    "auth.2fa_disabled",
    "auth.backup_codes",
    "review.approve",
    "review.reject",
    "profile.tier",
    "profile.update",
    "profile.version_current",
    "profile.delete",
    "profile.restore",
    "comment.remove",
    "comment.restore",
    "report.dismiss",
    "report.remove",
    "instance.block",
    "instance.unblock",
    "instance.reset_name",
    "question.close",
    "question.reopen",
    "question.team",
    "answer.delete",
    "answer.team",
)

PER_PAGE = 50


def record(
    db: Session,
    *,
    admin_user: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AdminAuditLog:
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action {action!r}")
    row = AdminAuditLog(
        admin_user=admin_user,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=(summary or "")[:500] or None,
        details=details or None,
        ip=ip,
    )
    db.add(row)
    db.flush()
    return row


@dataclass
class AuditPage:
    items: list[AdminAuditLog]
    total: int
    page: int
    pages: int


def list_entries(
    db: Session,
    *,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    q: str | None = None,
    page: int = 1,
    per_page: int = PER_PAGE,
) -> AuditPage:
    stmt = select(AdminAuditLog)
    if action:
        if action.endswith(".*"):
            stmt = stmt.where(AdminAuditLog.action.like(action[:-1] + "%"))
        else:
            stmt = stmt.where(AdminAuditLog.action == action)
    if target_type:
        stmt = stmt.where(AdminAuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AdminAuditLog.target_id == target_id)
    if q:
        stmt = stmt.where(AdminAuditLog.summary.ilike(f"%{q.strip()}%"))
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    page = max(1, page)
    pages = max(1, (total + per_page - 1) // per_page)
    rows = (
        db.execute(
            stmt.order_by(AdminAuditLog.created_at.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        .scalars()
        .all()
    )
    return AuditPage(items=list(rows), total=total, page=page, pages=pages)


def recent(db: Session, limit: int = 8) -> list[AdminAuditLog]:
    return list(
        db.execute(select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )


def action_groups() -> list[tuple[str, list[str]]]:
    """Aktionen nach Präfix gruppiert (für den Filter im Protokoll)."""
    groups: dict[str, list[str]] = {}
    for action in ACTIONS:
        prefix = action.split(".", 1)[0]
        groups.setdefault(prefix, []).append(action)
    return list(groups.items())
