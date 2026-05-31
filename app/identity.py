"""Single source of truth for how an instance is shown to humans.

The SSO login JWT defaults to an auto name like ``instanz-94b0ad1a``. That ugly
default must never reach the UI. ``public_name`` resolves, in order:

1. the user-chosen community name (``Instance.chosen_name``), else
2. a real SSO ``display_name`` (anything that is NOT the auto default), else
3. a clean, language-neutral handle ``@<short-id>`` derived from the uuid.

Used EVERYWHERE a name is shown (nav, uploader, comment/question/answer authors,
admin) so ``instanz-…`` appears nowhere.
"""

from __future__ import annotations

import re

# The auto default looks like ``instanz-<hex>`` / ``instance-<hex>`` or a raw
# uuid. Treat those as "no real name".
_AUTO_RE = re.compile(r"^(instan[zc]e?[-_])", re.IGNORECASE)
_UUIDISH_RE = re.compile(r"^[0-9a-f]{8}(-?[0-9a-f]{4}){0,4}$", re.IGNORECASE)


def _short_id(uuid: str) -> str:
    return (uuid or "").replace("-", "")[:8] or "anon"


def _is_auto(name: str | None) -> bool:
    if not name or not name.strip():
        return True
    n = name.strip()
    return bool(_AUTO_RE.match(n) or _UUIDISH_RE.match(n))


def public_name(raw_name: str | None, uuid: str, chosen: str | None = None) -> str:
    """Resolve the human-facing name for an instance (see module docstring)."""
    if chosen and chosen.strip():
        return chosen.strip()
    if not _is_auto(raw_name):
        return (raw_name or "").strip()
    return "@" + _short_id(uuid)


def is_real_name(raw_name: str | None, chosen: str | None = None) -> bool:
    """True if a user-meaningful name exists (chosen, or a non-auto SSO name)."""
    if chosen and chosen.strip():
        return True
    return not _is_auto(raw_name)
