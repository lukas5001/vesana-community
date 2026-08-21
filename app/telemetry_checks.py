"""Agent telemetry check types — the hub-side mirror of Vesana's canonical list.

In Vesana these six checks are created automatically on every agent host and are
hidden from EVERY user-facing list: they feed the host detail page's deep-analysis
and command-center panels and are never something a user picks, configures or
even sees. Canonical source: ``shared/telemetry_checks.py`` in the Vesana repo
(``AGENT_TELEMETRY_CHECKS``) — this file is a deliberate copy, because the hub is
a separate service with its own dependency tree.

The hub matters here because bundles carry them: a Vesana profile export used to
include telemetry checks, so 28 current hub profiles still list them (that is how
"Windows Server Base Checks" advertised 17 checks instead of 11). Two places use
this list:

  * ``services/uploads.validate_bundle`` — strips them on INGEST, so newly
    uploaded bundles never store them again,
  * ``schemas/profile.check_preview_from_bundle`` — strips them on DISPLAY, so
    the already-stored bundles stop showing them on the website and in the API
    preview that the Vesana app consumes.

Changing the list in Vesana means changing it here too.
"""

from __future__ import annotations

from typing import Any

AGENT_TELEMETRY_CHECK_TYPES: frozenset[str] = frozenset(
    {
        "agent_overview",
        "agent_network_state",
        "agent_security",
        "agent_inventory",
        "agent_lifecycle",
        "agent_hardware",
    }
)


def is_telemetry_check(check: Any) -> bool:
    """True when a bundle check is one of the invisible agent telemetry checks.

    🔒 The ``check_type`` decides ALONE — an ``is_telemetry`` flag is NOT enough.
    A bundle is foreign input: the flag is just a field someone set, while the
    six telemetry types are built into the Go agent and created server-side. A
    check of any other type is never telemetry, whatever the flag claims.

    Proven on our own data: "Proxmox VE (API)" v1 carries a real ``http_json``
    check "Uptime (Tage)" flagged ``is_telemetry: true`` (an authoring slip —
    "informational" was meant). Trusting the flag hid a real check. The rule is
    fail-safe: when in doubt, KEEP.

    The type key is ``check_type``; ``type`` is accepted as the legacy alias.
    """
    if not isinstance(check, dict):
        return False
    check_type = check.get("check_type") or check.get("type")
    return isinstance(check_type, str) and check_type in AGENT_TELEMETRY_CHECK_TYPES


def strip_telemetry_checks(checks: Any) -> list:
    """Return ``checks`` without telemetry entries (non-list ⇒ empty list)."""
    if not isinstance(checks, list):
        return []
    return [c for c in checks if not is_telemetry_check(c)]
