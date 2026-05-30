"""Idempotent seed data for the profile library.

Inserts a handful of official + beta example profiles, each with one current
version whose ``bundle_json`` is a realistic (but stub) profile bundle. Run
with ``python -m app.seed``; safe to re-run (UPSERT by deterministic UUID).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.community_profile import CommunityProfile
from app.models.community_profile_version import CommunityProfileVersion

# Stable namespace so the same logical profile always maps to the same UUID.
_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _profile_id(slug: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"profile:{slug}"))


def _version_id(slug: str, version_tag: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"version:{slug}:{version_tag}"))


def _bundle(
    *, name: str, category: str, vendor: str, checks: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": {
            "name": name,
            "category": category,
            "vendor": vendor,
        },
        "checks": checks,
        "scripts": [],
    }


SEED_PROFILES: list[dict[str, Any]] = [
    {
        "slug": "unifi-switch",
        "name": "UniFi Switch",
        "description_md": (
            "Monitor Ubiquiti UniFi switches over SNMP.\n\n"
            "Tracks port status, PoE budget, uptime and CPU/memory."
        ),
        "category": "Network",
        "vendor": "Ubiquiti",
        "icon": "🔌",
        "tier": "official",
        "approved": True,
        "vesana_min_version": "0.30.0",
        "requires_snmp": True,
        "tags": ["network", "snmp", "switch", "unifi"],
        "version_tag": "1.0.0",
        "changelog_md": "Initial official release.",
        "checks": [
            {"name": "Switch reachable", "check_type": "ping", "config": {"host": "REDACTED"}},
            {"name": "Port status", "check_type": "snmp", "oid": "1.3.6.1.2.1.2.2.1.8"},
            {"name": "PoE budget", "check_type": "snmp", "oid": "1.3.6.1.4.1.41112"},
            {"name": "Uptime", "check_type": "snmp", "oid": "1.3.6.1.2.1.1.3.0"},
        ],
    },
    {
        "slug": "synology-nas-snmp",
        "name": "Synology NAS (SNMP)",
        "description_md": (
            "Monitor a Synology DiskStation via SNMP: volume usage, disk health, "
            "temperature and system load."
        ),
        "category": "Storage",
        "vendor": "Synology",
        "icon": "🗄️",
        "tier": "official",
        "approved": True,
        "vesana_min_version": "0.30.0",
        "requires_snmp": True,
        "tags": ["storage", "nas", "snmp", "synology"],
        "version_tag": "1.1.0",
        "changelog_md": "Added disk SMART status check.",
        "checks": [
            {"name": "Volume usage", "check_type": "snmp", "oid": "1.3.6.1.4.1.6574.3"},
            {"name": "Disk health", "check_type": "snmp", "oid": "1.3.6.1.4.1.6574.2"},
            {"name": "System temperature", "check_type": "snmp", "oid": "1.3.6.1.4.1.6574.1.2"},
        ],
    },
    {
        "slug": "generic-linux-host-agent",
        "name": "Generic Linux Host (Agent)",
        "description_md": (
            "Baseline host monitoring via the Vesana agent: CPU, memory, disk, "
            "load average and the systemd service state."
        ),
        "category": "Server",
        "vendor": "Generic",
        "icon": "🐧",
        "tier": "official",
        "approved": True,
        "vesana_min_version": "0.31.0",
        "requires_agent": True,
        "tags": ["linux", "agent", "server", "host"],
        "version_tag": "2.0.0",
        "changelog_md": "Reworked for the new agent check contract.",
        "checks": [
            {"name": "CPU usage", "check_type": "agent_metric", "metric": "cpu.usage"},
            {"name": "Memory usage", "check_type": "agent_metric", "metric": "mem.usage"},
            {"name": "Disk usage /", "check_type": "agent_metric", "metric": "disk.usage"},
            {"name": "Load average", "check_type": "agent_metric", "metric": "load.avg1"},
        ],
    },
    {
        "slug": "proxmox-ve",
        "name": "Proxmox VE",
        "description_md": (
            "Monitor a Proxmox Virtual Environment node: running VMs, cluster "
            "quorum, storage pools and node load. **Beta** — feedback welcome."
        ),
        "category": "Virtualization",
        "vendor": "Proxmox",
        "icon": "🖥️",
        "tier": "beta",
        "approved": False,
        "vesana_min_version": "0.32.0",
        "requires_agent": True,
        "tags": ["virtualization", "proxmox", "cluster"],
        "version_tag": "0.3.0",
        "changelog_md": "Beta preview: cluster quorum check added.",
        "checks": [
            {"name": "Node online", "check_type": "http", "url": "REDACTED"},
            {"name": "Cluster quorum", "check_type": "agent_metric", "metric": "pve.quorum"},
            {"name": "Running VMs", "check_type": "agent_metric", "metric": "pve.vms.running"},
        ],
    },
    {
        "slug": "fritzbox",
        "name": "Fritzbox",
        "description_md": (
            "Monitor an AVM FRITZ!Box router via TR-064: WAN status, sync rate "
            "and connected devices. **Beta**."
        ),
        "category": "Network",
        "vendor": "AVM",
        "icon": "📡",
        "tier": "beta",
        "approved": False,
        "vesana_min_version": "0.32.0",
        "requires_collector": True,
        "tags": ["network", "router", "fritzbox", "tr064"],
        "version_tag": "0.2.1",
        "changelog_md": "Beta preview: WAN reconnect detection.",
        "checks": [
            {"name": "WAN connected", "check_type": "tr064", "action": "GetStatusInfo"},
            {"name": "DSL sync rate", "check_type": "tr064", "action": "GetCommonLinkProperties"},
        ],
    },
]


def _upsert_profile(db: Session, spec: dict[str, Any]) -> None:
    slug = spec["slug"]
    pid = _profile_id(slug)
    vid = _version_id(slug, spec["version_tag"])

    profile = db.get(CommunityProfile, pid)
    if profile is None:
        profile = CommunityProfile(id=pid)
        db.add(profile)

    profile.name = spec["name"]
    profile.description_md = spec["description_md"]
    profile.category = spec["category"]
    profile.vendor = spec["vendor"]
    profile.icon = spec["icon"]
    profile.tier = spec["tier"]
    profile.approved = spec.get("approved", False)
    profile.vesana_min_version = spec.get("vesana_min_version")
    profile.requires_agent = spec.get("requires_agent", False)
    profile.requires_collector = spec.get("requires_collector", False)
    profile.requires_snmp = spec.get("requires_snmp", False)
    profile.tags = spec.get("tags", [])
    profile.uploader_instance_uuid = None  # official/beta = Vesana Team
    # Flush so the profile row exists before the version FK references it.
    db.flush()

    bundle = _bundle(
        name=spec["name"],
        category=spec["category"],
        vendor=spec["vendor"],
        checks=spec["checks"],
    )

    version = db.get(CommunityProfileVersion, vid)
    if version is None:
        version = CommunityProfileVersion(id=vid, profile_id=pid)
        db.add(version)
    version.version_tag = spec["version_tag"]
    version.bundle_json = bundle
    version.changelog_md = spec.get("changelog_md")

    # Exactly one current version per profile.
    for existing in profile.versions:
        existing.is_current = existing.id == vid
    version.is_current = True
    db.flush()

    profile.latest_version_id = vid


def seed(db: Session | None = None) -> int:
    """Seed all example profiles. Returns the number of profiles seeded."""
    own_session = db is None
    session = db or SessionLocal()
    try:
        for spec in SEED_PROFILES:
            _upsert_profile(session, spec)
        session.commit()
        return len(SEED_PROFILES)
    finally:
        if own_session:
            session.close()


def main() -> None:
    count = seed()
    print(f"Seeded {count} community profiles.")


if __name__ == "__main__":
    main()
