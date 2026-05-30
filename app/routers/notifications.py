"""Notifications router (C6a): an instance polls and marks read its OWN events.

The authenticated instance (Bearer API token) is the recipient identity: every
query is scoped to ``CommunityEvent.instance_uuid == instance.uuid`` so a caller
can never read or mark another instance's events.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.community_event import CommunityEvent
from app.routers.community_interactions import CurrentInstance, DbDep
from app.schemas.notifications import MarkReadIn, NotificationList, NotificationOut

router = APIRouter(tags=["notifications"])


def _unread_count(db: Session, instance_uuid: str) -> int:
    stmt = (
        select(func.count())
        .select_from(CommunityEvent)
        .where(
            CommunityEvent.instance_uuid == instance_uuid,
            CommunityEvent.is_read.is_(False),
        )
    )
    return int(db.execute(stmt).scalar_one())


@router.get("/api/v1/notifications", response_model=NotificationList)
def list_notifications(
    instance: CurrentInstance,
    db: DbDep,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
) -> NotificationList:
    stmt = select(CommunityEvent).where(CommunityEvent.instance_uuid == instance.uuid)
    if unread_only:
        stmt = stmt.where(CommunityEvent.is_read.is_(False))
    stmt = stmt.order_by(CommunityEvent.created_at.desc()).limit(limit)
    rows = db.execute(stmt).scalars().all()

    items = [
        NotificationOut(
            id=row.id,
            type=row.type,
            payload=row.payload_json,
            is_read=row.is_read,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return NotificationList(items=items, unread_count=_unread_count(db, instance.uuid))


@router.post("/api/v1/notifications/mark-read")
def mark_read(
    payload: MarkReadIn,
    instance: CurrentInstance,
    db: DbDep,
) -> dict[str, int]:
    if payload.all:
        stmt = (
            update(CommunityEvent)
            .where(
                CommunityEvent.instance_uuid == instance.uuid,
                CommunityEvent.is_read.is_(False),
            )
            .values(is_read=True)
        )
    elif payload.ids:
        stmt = (
            update(CommunityEvent)
            .where(
                CommunityEvent.instance_uuid == instance.uuid,
                CommunityEvent.id.in_(payload.ids),
                CommunityEvent.is_read.is_(False),
            )
            .values(is_read=True)
        )
    else:
        return {"marked": 0}

    result = db.execute(stmt)
    db.commit()
    return {"marked": int(result.rowcount or 0)}
