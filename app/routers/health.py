"""Liveness endpoint used by Docker/compose healthchecks."""

from __future__ import annotations

from fastapi import APIRouter

from app.version import VERSION

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "vesana-community", "version": VERSION}
