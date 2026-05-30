"""Pydantic schemas for community profile upload + the admin review queue (C3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class BundleUpload(BaseModel):
    """Request body for ``POST /api/v1/profiles/upload``.

    ``bundle`` is the same export shape Vesana produces: a dict with
    ``schema_version`` (must be 1), a ``profile`` object and a ``checks`` list.
    ``version_tag`` / ``changelog_md`` are optional metadata for this upload.
    """

    bundle: dict[str, Any]
    version_tag: str | None = None
    changelog_md: str | None = None


class UploadResult(BaseModel):
    """Response after a successful upload (new profile or new version)."""

    profile_id: str
    version_id: str
    review_status: str
    has_scripts: bool
    script_findings: list[dict[str, Any]] = []


class ReviewItem(BaseModel):
    """A single profile in the admin review queue."""

    profile_id: str
    name: str
    vendor: str | None = None
    uploader_instance_uuid: str | None = None
    uploader_display: str
    review_status: str
    has_scripts: bool
    script_findings: list[dict[str, Any]] = []
    created_at: datetime
    current_version_tag: str | None = None


class RejectIn(BaseModel):
    """Body for rejecting a pending upload."""

    reason: str
