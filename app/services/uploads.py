"""Community profile upload, versioning, script-gate + review queue (C3).

A self-hoster (authenticated Instance) uploads a profile BUNDLE — the same
export shape Vesana produces. We validate it, run a heuristic script-gate over
it, then either create a brand-new community profile (tier 'community',
``review_status='pending'``, immediately visible with a "waiting for review"
badge) or, when the same uploader re-uploads a profile with the same
(name, vendor), add a NEW immutable version and flip ``is_current``.

``review_status`` is the source of truth for visibility; the legacy ``approved``
bool is mirrored from it so existing C1 visibility queries keep working.

The script-gate is a HEURISTIC, not a sandbox. Vesana bundles reference scripts
by ``script_id`` inside ``check_config`` (they do NOT embed script bodies), so
the gate (a) flags any check that references a script and (b) defensively scans
every string value for dangerous shell/powershell/python markers should an
inline script ever appear. Findings surface in admin review and as a UI warning.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.community_profile import CommunityProfile
from app.models.community_profile_version import CommunityProfileVersion
from app.models.instance import Instance
from app.schemas.profile import VESANA_TEAM_UPLOADER
from app.schemas.upload import ReviewItem
from app.services import notifications

# Maximum serialized bundle size. Bundles reference scripts by id, so they stay
# small; a generous cap stops abuse without rejecting legitimate large profiles.
MAX_BUNDLE_BYTES = 500 * 1024

BUNDLE_SCHEMA_VERSION = 1

# Heuristic danger markers scanned across all string values in check_config and
# any top-level 'scripts' list. Case-insensitive substring match. NOT a sandbox.
SCRIPT_MARKERS = (
    "rm -rf",
    "mkfs",
    ":(){",
    "invoke-expression",
    "iex ",
    "downloadstring",
    "curl",
    "wget",
    "| bash",
    "| sh",
    "base64 -d",
    "eval(",
    "exec(",
    "os.system",
    "subprocess",
    "powershell -enc",
)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def validate_bundle(bundle: Any) -> dict[str, Any]:
    """Validate the uploaded bundle shape + size; return it on success.

    Raises 400 for a malformed bundle and 413 when the serialized bundle is
    larger than ``MAX_BUNDLE_BYTES``.
    """
    if not isinstance(bundle, dict):
        raise _bad_request("bundle must be an object")
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise _bad_request("unsupported bundle schema_version (expected 1)")
    profile = bundle.get("profile")
    if not isinstance(profile, dict):
        raise _bad_request("bundle.profile must be an object")
    name = profile.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _bad_request("bundle.profile.name is required")
    checks = bundle.get("checks")
    if not isinstance(checks, list):
        raise _bad_request("bundle.checks must be a list")

    try:
        serialized = json.dumps(bundle)
    except (TypeError, ValueError) as exc:
        raise _bad_request("bundle is not JSON-serializable") from exc
    if len(serialized.encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="bundle exceeds the 500KB limit",
        )
    return bundle


def _iter_strings(value: Any, where: str):
    """Yield (string_value, dotted_where) for every string nested in ``value``."""
    if isinstance(value, str):
        yield value, where
    elif isinstance(value, dict):
        for key, sub in value.items():
            yield from _iter_strings(sub, f"{where}.{key}" if where else str(key))
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            yield from _iter_strings(sub, f"{where}[{idx}]")


def scan_scripts(bundle: dict[str, Any]) -> tuple[bool, list[dict[str, str]]]:
    """Heuristic script-gate over a bundle.

    Returns ``(has_scripts, findings)`` where:
    * ``has_scripts`` is True if any check references a script via
      ``check_config.script_id`` (the normal Vesana shape).
    * ``findings`` is a list of ``{marker, where}`` for every dangerous marker
      found in any check_config string value or any top-level 'scripts' entry.

    This is purely advisory — it never blocks an upload and is NOT a sandbox.
    """
    has_scripts = False
    findings: list[dict[str, str]] = []

    checks = bundle.get("checks")
    if isinstance(checks, list):
        for idx, check in enumerate(checks):
            if not isinstance(check, dict):
                continue
            config = check.get("check_config")
            if isinstance(config, dict) and config.get("script_id"):
                has_scripts = True
            if config is not None:
                for text_value, where in _iter_strings(config, f"checks[{idx}].check_config"):
                    lowered = text_value.lower()
                    for marker in SCRIPT_MARKERS:
                        if marker in lowered:
                            findings.append({"marker": marker, "where": where})

    scripts = bundle.get("scripts")
    if isinstance(scripts, list):
        for text_value, where in _iter_strings(scripts, "scripts"):
            lowered = text_value.lower()
            for marker in SCRIPT_MARKERS:
                if marker in lowered:
                    findings.append({"marker": marker, "where": where})

    return has_scripts, findings


def _profile_fields_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Extract the persisted CommunityProfile metadata from a bundle.profile."""
    profile = bundle.get("profile", {})
    tags = profile.get("tags")
    return {
        "name": profile["name"],
        "description_md": profile.get("description_md") or profile.get("description"),
        "category": profile.get("category"),
        "vendor": profile.get("vendor"),
        "icon": profile.get("icon"),
        "vesana_min_version": profile.get("vesana_min_version"),
        "requires_agent": bool(profile.get("requires_agent", False)),
        "requires_collector": bool(profile.get("requires_collector", False)),
        "requires_snmp": bool(profile.get("requires_snmp", False)),
        "tags": list(tags) if isinstance(tags, list) else None,
    }


def _find_owned_profile(
    db: Session, uploader_instance_uuid: str, name: str, vendor: str | None
) -> CommunityProfile | None:
    """Return the uploader's existing profile with the same (name, vendor)."""
    stmt = select(CommunityProfile).where(
        CommunityProfile.uploader_instance_uuid == uploader_instance_uuid,
        CommunityProfile.name == name,
        CommunityProfile.is_removed.is_(False),
    )
    if vendor is None:
        stmt = stmt.where(CommunityProfile.vendor.is_(None))
    else:
        stmt = stmt.where(CommunityProfile.vendor == vendor)
    return db.execute(stmt).scalars().first()


def create_or_version_profile(
    db: Session,
    *,
    instance_uuid: str,
    bundle: dict[str, Any],
    version_tag: str | None,
    changelog_md: str | None,
) -> tuple[CommunityProfile, CommunityProfileVersion]:
    """Create a new community profile OR add a new version of the uploader's own.

    All work happens in one transaction (the caller commits). When the uploader
    already owns a profile with the same (name, vendor), this re-upload becomes a
    NEW immutable version: ``is_current`` flips to the new row, ``review_status``
    resets to 'pending' and the script-gate re-runs. Re-using an existing
    ``version_tag`` for the same profile is a 409.
    """
    fields = _profile_fields_from_bundle(bundle)
    has_scripts, findings = scan_scripts(bundle)
    tag = version_tag or "v1"

    existing = _find_owned_profile(db, instance_uuid, fields["name"], fields["vendor"])

    if existing is not None:
        # Reject a duplicate version_tag on the same profile (immutable rows).
        dup = db.execute(
            select(CommunityProfileVersion).where(
                CommunityProfileVersion.profile_id == existing.id,
                CommunityProfileVersion.version_tag == tag,
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"version_tag '{tag}' already exists for this profile",
            )
        # Flip every existing version off; new one becomes current.
        for version in existing.versions:
            version.is_current = False
        new_version = CommunityProfileVersion(
            profile_id=existing.id,
            version_tag=tag,
            bundle_json=bundle,
            changelog_md=changelog_md,
            is_current=True,
        )
        db.add(new_version)
        db.flush()

        # Refresh metadata from the new bundle + reset to pending re-review.
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.tier = "community"
        existing.review_status = "pending"
        existing.approved = False
        existing.approved_at = None
        existing.approved_by = None
        existing.rejection_reason = None
        existing.has_scripts = has_scripts
        existing.script_findings = findings or None
        existing.latest_version_id = new_version.id
        existing.updated_at = datetime.now(UTC)
        db.flush()
        return existing, new_version

    # Brand-new community profile.
    profile = CommunityProfile(
        tier="community",
        approved=False,
        review_status="pending",
        uploader_instance_uuid=instance_uuid,
        has_scripts=has_scripts,
        script_findings=findings or None,
        **fields,
    )
    db.add(profile)
    db.flush()

    first_version = CommunityProfileVersion(
        profile_id=profile.id,
        version_tag=tag,
        bundle_json=bundle,
        changelog_md=changelog_md,
        is_current=True,
    )
    db.add(first_version)
    db.flush()

    profile.latest_version_id = first_version.id
    db.flush()
    return profile, first_version


# ---- Review queue ----------------------------------------------------------


def _uploader_display(db: Session, profile: CommunityProfile) -> str:
    if not profile.uploader_instance_uuid:
        return VESANA_TEAM_UPLOADER
    instance = db.get(Instance, profile.uploader_instance_uuid)
    if instance is not None and instance.display_name:
        return instance.display_name
    return VESANA_TEAM_UPLOADER


def _current_version_tag(profile: CommunityProfile) -> str | None:
    current = profile.current_version
    if current is not None:
        return current.version_tag
    if profile.versions:
        return profile.versions[0].version_tag
    return None


def to_review_item(db: Session, profile: CommunityProfile) -> ReviewItem:
    """Build the public ReviewItem view of a profile (used by the router)."""
    return ReviewItem(
        profile_id=profile.id,
        name=profile.name,
        vendor=profile.vendor,
        uploader_instance_uuid=profile.uploader_instance_uuid,
        uploader_display=_uploader_display(db, profile),
        review_status=profile.review_status,
        has_scripts=profile.has_scripts,
        script_findings=list(profile.script_findings or []),
        created_at=profile.created_at,
        current_version_tag=_current_version_tag(profile),
    )


def list_for_review(db: Session, status_filter: str | None = None) -> list[ReviewItem]:
    """List community profiles for the admin review queue.

    Default (``status_filter`` None) returns only pending uploads. Pass
    'all' for every community profile, or a specific status to filter.
    """
    stmt = select(CommunityProfile).where(
        CommunityProfile.tier == "community",
        CommunityProfile.is_removed.is_(False),
    )
    if status_filter is None:
        stmt = stmt.where(CommunityProfile.review_status == "pending")
    elif status_filter != "all":
        stmt = stmt.where(CommunityProfile.review_status == status_filter)
    stmt = stmt.order_by(CommunityProfile.created_at.desc())
    profiles = db.execute(stmt).scalars().all()
    return [to_review_item(db, p) for p in profiles]


def approve(db: Session, profile_id: str) -> CommunityProfile:
    """Approve a pending upload. Idempotent-ish; 404 if missing/removed."""
    profile = db.get(CommunityProfile, profile_id)
    if profile is None or profile.is_removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    profile.review_status = "approved"
    profile.approved = True
    profile.approved_at = datetime.now(UTC)
    profile.approved_by = "admin"
    profile.rejection_reason = None
    db.flush()

    # Approved -> notify the uploader. actor is the admin (not an instance), so
    # there is never a self-notify; official/beta profiles have no uploader.
    notifications.enqueue(
        db,
        recipient_uuid=profile.uploader_instance_uuid,
        actor_uuid=None,
        type="profile_approved",
        payload={
            "profile_id": profile.id,
            "profile_name": profile.name,
        },
    )
    return profile


def reject(db: Session, profile_id: str, reason: str) -> CommunityProfile:
    """Reject an upload with a reason (visible to the uploader). 404 if missing."""
    profile = db.get(CommunityProfile, profile_id)
    if profile is None or profile.is_removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    profile.review_status = "rejected"
    profile.approved = False
    profile.rejection_reason = reason
    db.flush()

    # Rejected -> notify the uploader with the reason (visible to them only).
    notifications.enqueue(
        db,
        recipient_uuid=profile.uploader_instance_uuid,
        actor_uuid=None,
        type="profile_rejected",
        payload={
            "profile_id": profile.id,
            "profile_name": profile.name,
            "reason": reason,
        },
    )
    return profile
