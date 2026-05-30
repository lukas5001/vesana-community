"""Pydantic schemas for community notifications / events (C6a)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    payload: dict | None = None
    is_read: bool
    created_at: datetime


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class MarkReadIn(BaseModel):
    ids: list[str] | None = None
    all: bool = False
