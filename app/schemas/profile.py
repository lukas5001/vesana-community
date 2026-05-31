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
    """One human-readable setting of a check: a humanised label + a value."""

    key: str  # humanised label, e.g. "SNMP Version"
    value: str  # display value; secret values are masked to ``•••``


class CheckPreview(BaseModel):
    """A single check on a profile — like viewing a service in the Vesana app.

    Surfaces the check name, type and ALL of its settings (top-level fields such
    as ``oid`` plus the ``config`` dict, merged and humanised). Secret-VALUED
    settings are masked to ``•••`` (the setting is still listed so the user knows
    it exists); nested structures are skipped. An uploaded bundle can therefore
    never leak credentials through the public preview.
    """

    name: str
    check_type: str
    description: str | None = None
    settings: list[CheckParam] = Field(default_factory=list)


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
    # Id of the current version — a consumer (e.g. the Vesana import proxy) needs
    # it to fetch /versions/{id}/bundle; the tag alone is not addressable.
    latest_version_id: str | None = None
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
# Setting KEYS whose VALUE is masked to ``•••``. The setting is STILL listed (so
# the user knows the check uses e.g. an SNMP community) — only the value hides.
_SECRET_KEY_HINTS = (
    "password",
    "secret",
    "token",
    "community",
    "credential",
    "auth",
    "passphrase",
    "apikey",
    "api_key",
    "private",
    "key",
)
# Structural keys that are not user-facing settings.
_SKIP_KEYS = {
    "name",
    "check_type",
    "type",
    "config",
    "description",
    "id",
    "check_id",
    "profile_check_id",
    "profile_id",
}
_MAX_SETTINGS = 20
_MAX_VALUE_LEN = 160
_ACRONYMS = {
    "oid": "OID",
    "snmp": "SNMP",
    "url": "URL",
    "uri": "URI",
    "ip": "IP",
    "http": "HTTP",
    "https": "HTTPS",
    "ssh": "SSH",
    "tls": "TLS",
    "ssl": "SSL",
    "dns": "DNS",
    "poe": "PoE",
    "id": "ID",
    "cpu": "CPU",
    "ram": "RAM",
    "api": "API",
    "tcp": "TCP",
    "udp": "UDP",
    "mac": "MAC",
    "wmi": "WMI",
}


def _looks_secret(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _SECRET_KEY_HINTS)


def _humanize(key: str) -> str:
    words = key.replace("-", " ").replace("_", " ").split()
    parts = [_ACRONYMS.get(w.lower(), w[:1].upper() + w[1:]) for w in words]
    return " ".join(parts) or key


def _setting_value(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _MAX_VALUE_LEN:
        text = text[:_MAX_VALUE_LEN] + "…"
    return text


def _collect_settings(check: dict, config: dict) -> list[CheckParam]:
    """Merge top-level check fields + config into humanised, masked settings.

    Mirrors how a service is shown in the Vesana app: every configured setting is
    listed (humanised label). Secret-valued settings are masked; nested values
    and structural keys are skipped.
    """
    out: list[CheckParam] = []
    seen: set[str] = set()
    for source in (check, config):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if not isinstance(key, str):
                continue
            kl = key.lower()
            if kl in _SKIP_KEYS or kl in seen:
                continue
            if _looks_secret(key):
                seen.add(kl)
                out.append(CheckParam(key=_humanize(key), value="•••"))
            else:
                sval = _setting_value(value)
                if sval is None:
                    continue
                seen.add(kl)
                out.append(CheckParam(key=_humanize(key), value=sval))
            if len(out) >= _MAX_SETTINGS:
                return out
    return out


def check_preview_from_bundle(bundle: dict[str, Any] | None) -> list[CheckPreview]:
    """Derive a safe, informative per-check settings view from a profile bundle.

    Shows every configured setting of each check (top-level fields like ``oid``
    plus the ``config`` dict, humanised), with secret VALUES masked. An untrusted
    uploaded bundle can never leak credentials. Malformed/missing data degrades
    gracefully to an empty list.
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
        desc = check.get("description")
        preview.append(
            CheckPreview(
                name=name,
                check_type=check_type,
                description=(desc.strip() if isinstance(desc, str) and desc.strip() else None),
                settings=_collect_settings(check, config),
            )
        )
    return preview
