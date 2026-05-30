"""Pydantic schemas for community profiles (browse + detail + JSON API)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# Tier of a profile. ``official`` / ``beta`` are curated by the Vesana team;
# ``community`` profiles are uploaded by self-hosters.
ProfileTier = str

# Display name used for official/beta profiles whose uploader is the Vesana team.
VESANA_TEAM_UPLOADER = "Vesana Team"


class CheckPreview(BaseModel):
    """A single check exposed in a profile preview.

    Deliberately exposes ONLY the check name and type. Any sensitive
    configuration (credentials, hosts, thresholds, scripts) is stripped.
    """

    name: str
    check_type: str


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version_tag: str
    is_current: bool
    created_at: datetime
    changelog_md: str | None = None


class ProfileSummary(BaseModel):
    """List-item view of a profile (no heavy/derived fields)."""

    id: str
    name: str
    vendor: str | None = None
    category: str | None = None
    icon: str | None = None
    tier: str
    approved: bool
    # Review workflow (C3): 'pending' | 'approved' | 'rejected'. Drives the
    # "🔄 Warte auf Review" badge in browse/detail.
    review_status: str = "approved"
    # Heuristic script-gate flag (C3); true if any check references a script.
    has_scripts: bool = False
    vote_score: int = 0
    download_count: int = 0
    import_count: int = 0
    tags: list[str] = []
    requires_agent: bool = False
    requires_collector: bool = False
    requires_snmp: bool = False
    vesana_min_version: str | None = None
    latest_version_tag: str | None = None
    updated_at: datetime


class ProfileListResponse(BaseModel):
    items: list[ProfileSummary]
    total: int


class ProfileDetail(ProfileSummary):
    """Full detail view of a profile."""

    description_md: str | None = None
    created_at: datetime
    uploader: str
    current_changelog_md: str | None = None
    check_preview: list[CheckPreview] = []


def check_preview_from_bundle(bundle: dict[str, Any] | None) -> list[CheckPreview]:
    """Derive a safe check preview from a profile bundle.

    Only ``name`` and ``check_type`` are exposed; every other field of each
    check (config, secrets, command, thresholds, ...) is dropped. Malformed
    or missing data degrades gracefully to an empty list.
    """
    if not isinstance(bundle, dict):
        return []
    checks = bundle.get("checks")
    if not isinstance(checks, list):
        return []
    preview: list[CheckPreview] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = check.get("name")
        check_type = check.get("check_type") or check.get("type")
        if not isinstance(name, str) or not isinstance(check_type, str):
            continue
        preview.append(CheckPreview(name=name, check_type=check_type))
    return preview
