"""Pydantic schemas for community profiles (browse + detail + JSON API)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Tier of a profile. ``official`` / ``beta`` are curated by the Vesana team;
# ``community`` profiles are uploaded by self-hosters.
ProfileTier = str

# Display name used for official/beta profiles whose uploader is the Vesana team.
VESANA_TEAM_UPLOADER = "Vesana Team"


class CheckParam(BaseModel):
    """One safe, human-readable config parameter of a check (key + value)."""

    key: str
    value: str


class CheckPreview(BaseModel):
    """A single check exposed in a profile preview.

    Exposes WHAT a check monitors and WHEN it alerts — name, type, interval,
    thresholds, description — plus a safe subset of its config params. Secret-ish
    values are redacted to ``•••`` and command/script bodies + nested structures
    are dropped, so an uploaded bundle can never leak credentials through the
    public preview.
    """

    name: str
    check_type: str
    interval_seconds: int | None = None
    threshold_warn: str | None = None
    threshold_crit: str | None = None
    description: str | None = None
    params: list[CheckParam] = Field(default_factory=list)


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


# Config keys whose VALUE must never be shown (credentials etc.) — redacted.
_SECRET_KEY_HINTS = (
    "password",
    "secret",
    "token",
    "key",
    "community",
    "credential",
    "auth",
    "passphrase",
    "apikey",
    "api_key",
    "private",
)
# Config keys dropped entirely: free-text command/script bodies + raw args, and
# instance-specific network targets (host/ip/…) that leak internal topology and
# are meaningless to other users (the importer supplies their own).
_DROP_KEY_HINTS = (
    "command",
    "cmd",
    "script",
    "body",
    "powershell",
    "args",
    "argument",
    "host",
    "hostname",
    "ip",
    "address",
    "target",
    "endpoint",
)
_MAX_PARAMS = 12
_MAX_VALUE_LEN = 120


def _looks_secret(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _SECRET_KEY_HINTS)


def _opt_str(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def _safe_params(config: Any) -> list[CheckParam]:
    """A redacted, display-safe view of a check's config dict."""
    if not isinstance(config, dict):
        return []
    out: list[CheckParam] = []
    for key, value in config.items():
        if not isinstance(key, str):
            continue
        kl = key.lower()
        # Drop entirely: secret-ish keys, command/script bodies, network targets.
        if _looks_secret(key) or any(d in kl for d in _DROP_KEY_HINTS):
            continue
        if isinstance(value, (dict, list)) or value is None:
            continue  # don't render nested structures or empties
        sval = ("yes" if value else "no") if isinstance(value, bool) else str(value)
        if len(sval) > _MAX_VALUE_LEN:
            sval = sval[:_MAX_VALUE_LEN] + "…"
        out.append(CheckParam(key=key, value=sval))
        if len(out) >= _MAX_PARAMS:
            break
    return out


def check_preview_from_bundle(bundle: dict[str, Any] | None) -> list[CheckPreview]:
    """Derive a safe, informative check preview from a profile bundle.

    Exposes name/type/interval/thresholds/description + a redacted config view
    (see ``CheckPreview``). Secrets are masked and command/script bodies dropped,
    so an untrusted uploaded bundle cannot leak credentials. Malformed or missing
    data degrades gracefully to an empty list.
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
        config = check.get("config") if isinstance(check.get("config"), dict) else {}

        def _pick(*keys: str, _check=check, _config=config) -> Any:
            for source in (_check, _config):
                for k in keys:
                    if isinstance(source, dict) and source.get(k) is not None:
                        return source.get(k)
            return None

        interval = _pick("interval_seconds", "interval")
        interval_seconds = (
            interval if isinstance(interval, int) and not isinstance(interval, bool) else None
        )
        preview.append(
            CheckPreview(
                name=name,
                check_type=check_type,
                interval_seconds=interval_seconds,
                threshold_warn=_opt_str(_pick("threshold_warn", "warn", "warning")),
                threshold_crit=_opt_str(_pick("threshold_crit", "crit", "critical")),
                description=_opt_str(check.get("description")),
                params=_safe_params(config),
            )
        )
    return preview
