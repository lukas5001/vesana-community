"""Seed community 'beta' profiles from a Vesana built-in export dump.

Community-first distribution: every profile Vesana ships built-in is published
to the hub as a ``tier='beta'`` profile (uploader NULL, already approved) so a
self-hoster pulls it from the Community tab instead of carrying a local builtin.

Input is a JSON array produced on the Vesana DB (one item per built-in profile):

    [
      {
        "schema_version": 1,
        "match_rules": {...} | null,           # discovery classifier rules
        "profile": { "name", "vendor", "category", "icon", "description",
                     "agent_capable", "snmp_enabled", "ip_required",
                     "os_family", "tags", "sysoid_patterns", ... },
        "checks": [ { "name", "check_type", "check_config", ... }, ... ]
      },
      ...
    ]

This script is IDEMPOTENT: a beta profile is keyed by (name, vendor) among the
team-owned rows (uploader_instance_uuid IS NULL). If it already exists we leave
its bundle alone and only backfill ``match_rules`` when missing. New ones are
created as ``tier='beta'`` + ``review_status='approved'`` with a single ``v1``
version carrying the full bundle.

Usage (inside the community container):
    python scripts/seed_beta_from_vesana.py /tmp/builtins.json [--commit]

Without --commit it runs as a dry-run and prints what it WOULD do.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models.community_profile import CommunityProfile
from app.models.community_profile_version import CommunityProfileVersion
from app.services.uploads import scan_scripts, validate_bundle

# Check-type prefixes that imply a deployment requirement, for the hub badges.
_AGENT_TYPES = ("agent", "wmi")
_SNMP_TYPES = ("snmp",)

CHANGELOG = "Aus den mitgelieferten Vesana-Profilen in den Community-Hub übernommen."
SEED_VERSION_TAG = "v1"


def _requirements(profile: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, bool]:
    """Derive the hub requirement badges from a profile's capabilities + checks."""
    types = [str(c.get("check_type") or "") for c in checks]
    requires_agent = any(t.startswith(_AGENT_TYPES) for t in types)
    requires_snmp = bool(profile.get("snmp_enabled")) or any(
        t.startswith(_SNMP_TYPES) for t in types
    )
    # A "collector" runs every non-agent (remote) check: ping, port, http, ssl,
    # ssh, snmp, ... So a collector is required whenever any non-agent check
    # exists. Agent-only profiles need no collector.
    requires_collector = any(not t.startswith(_AGENT_TYPES) for t in types if t)
    return {
        "requires_agent": requires_agent,
        "requires_snmp": requires_snmp,
        "requires_collector": requires_collector,
    }


def _build_bundle(item: dict[str, Any]) -> dict[str, Any]:
    """Turn a dump item into a community-shaped, validatable bundle."""
    profile = dict(item.get("profile") or {})
    checks = list(item.get("checks") or [])
    profile.update(_requirements(profile, checks))
    # description_md mirrors the Vesana description so the hub shows real text.
    if profile.get("description") and not profile.get("description_md"):
        profile["description_md"] = profile["description"]
    return {"schema_version": 1, "profile": profile, "checks": checks}


def _find_team_profile(db, name: str, vendor: str | None) -> CommunityProfile | None:
    stmt = select(CommunityProfile).where(
        CommunityProfile.uploader_instance_uuid.is_(None),
        CommunityProfile.name == name,
        CommunityProfile.is_removed.is_(False),
    )
    stmt = stmt.where(
        CommunityProfile.vendor.is_(None) if vendor is None else CommunityProfile.vendor == vendor
    )
    return db.execute(stmt).scalars().first()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: seed_beta_from_vesana.py <dump.json> [--commit]")
        return 2
    path = sys.argv[1]
    commit = "--commit" in sys.argv[2:]

    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
    if not isinstance(items, list):
        print("dump must be a JSON array")
        return 2

    created = skipped = backfilled = failed = 0
    db = SessionLocal()
    try:
        for item in items:
            bundle = _build_bundle(item)
            match_rules = item.get("match_rules")
            try:
                validate_bundle(bundle)
            except Exception as exc:  # noqa: BLE001 — report + continue
                failed += 1
                print(f"  SKIP (invalid) {bundle['profile'].get('name')!r}: {exc}")
                continue

            name = bundle["profile"]["name"]
            vendor = bundle["profile"].get("vendor")
            existing = _find_team_profile(db, name, vendor)
            if existing is not None:
                # Idempotent: leave the bundle, only backfill missing match_rules.
                if existing.match_rules is None and match_rules is not None:
                    existing.match_rules = match_rules
                    backfilled += 1
                    print(f"  backfill match_rules: {name}")
                else:
                    skipped += 1
                continue

            has_scripts, findings = scan_scripts(bundle)
            profile = CommunityProfile(
                name=name,
                description_md=bundle["profile"].get("description_md"),
                category=bundle["profile"].get("category"),
                vendor=vendor,
                icon=bundle["profile"].get("icon"),
                tier="beta",
                approved=True,
                review_status="approved",
                approved_by="seed:vesana-builtins",
                uploader_instance_uuid=None,
                requires_agent=bundle["profile"].get("requires_agent", False),
                requires_collector=bundle["profile"].get("requires_collector", False),
                requires_snmp=bundle["profile"].get("requires_snmp", False),
                tags=bundle["profile"].get("tags") or None,
                match_rules=match_rules,
                has_scripts=has_scripts,
                script_findings=findings or None,
            )
            db.add(profile)
            db.flush()
            version = CommunityProfileVersion(
                profile_id=profile.id,
                version_tag=SEED_VERSION_TAG,
                bundle_json=bundle,
                changelog_md=CHANGELOG,
                is_current=True,
            )
            db.add(version)
            db.flush()
            profile.latest_version_id = version.id
            db.flush()
            created += 1
            print(f"  CREATE beta: {name} ({len(bundle['checks'])} checks)")

        print(
            f"\nsummary: created={created} backfilled={backfilled} "
            f"skipped(existing)={skipped} failed={failed} total={len(items)}"
        )
        if commit:
            db.commit()
            print("COMMITTED.")
        else:
            db.rollback()
            print("DRY-RUN (no --commit) — rolled back.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
