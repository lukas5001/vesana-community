"""Pydantic schemas for the community admin panel.

These back the AdminFlag-gated JSON API (``/api/v1/admin/*``). The HTML admin
pages render their own context, but reuse the same service layer.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class InstanceItem(BaseModel):
    """An instance row in the admin instances table."""

    uuid: str
    display_name: str
    public_name: str = ""
    chosen_name: str | None = None
    is_blocked: bool
    blocked_reason: str | None = None
    blocked_at: datetime | None = None
    joined_at: datetime
    last_seen_at: datetime
    uploaded_count: int


class BlockIn(BaseModel):
    """Body for blocking/unblocking an instance."""

    blocked: bool
    reason: str | None = None


class AdminStats(BaseModel):
    """Overview dashboard counts."""

    instances_total: int
    instances_blocked: int
    profiles_total: int
    profiles_by_tier: dict[str, int]
    profiles_pending: int
    downloads_total: int
    imports_total: int
    votes_total: int
    questions_total: int
    questions_open: int
    reports_open: int
    events_total: int
