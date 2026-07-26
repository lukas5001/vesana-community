"""Sync job: mirror dashboard-icons + simple-icons into ``library_icons``.

Run inside the container::

    python -m app.sync_icon_library                 # both sources
    python -m app.sync_icon_library --source simple-icons
    python -m app.sync_icon_library --limit 50      # smoke-test run

Merge rule
----------
dashboard-icons is the primary source (colored, recognizable artwork curated
for exactly our niche). simple-icons only fills slugs dashboard-icons does not
have (monochrome brand glyphs — served with ``monochrome=true`` so Vesana can
render them theme-aware via currentColor). A slug that later appears upstream
in dashboard-icons takes the row over. Upstream *removals* are deliberately
NOT propagated — nothing disappears from the library without local curation.

Variant convention (dashboard-icons)
------------------------------------
``{slug}-light.*`` is light-colored artwork (for dark backgrounds) and
``{slug}-dark.*`` is dark-colored artwork. Mapping to our light/dark bodies:

* ``-light`` exists → light body = base file, dark body = ``-light`` file.
* ``-dark`` exists  → light body = ``-dark`` file, dark body = base file.
* neither           → light body = base file, no dark variant.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import io
import json
import re
import sys
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import defer

from app.db import SessionLocal
from app.models.library_icon import LibraryIcon

DASHBOARD_CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons"
SIMPLE_ICONS_REGISTRY = "https://registry.npmjs.org/simple-icons/latest"

_CONCURRENCY = 12
_RETRIES = 2
_COMMIT_EVERY = 200


# ── Pure helpers (unit-tested) ────────────────────────────────────────


@dataclass
class VariantPlan:
    """Which upstream files make up the light/dark bodies of one slug."""

    light_file: str
    dark_file: str | None = None


def plan_variants(slug: str, fmt: str, available: set[str]) -> VariantPlan:
    """Apply the dashboard-icons variant convention (see module docstring)."""

    base = f"{slug}.{fmt}"
    light = f"{slug}-light.{fmt}"
    dark = f"{slug}-dark.{fmt}"
    if light in available:
        return VariantPlan(light_file=base, dark_file=light)
    if dark in available:
        return VariantPlan(light_file=dark, dark_file=base)
    return VariantPlan(light_file=base)


_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def svg_title(body: bytes) -> str | None:
    """Extract the human name from a simple-icons SVG ``<title>``."""

    m = _TITLE_RE.search(body.decode("utf-8", errors="replace"))
    if not m:
        return None
    return html.unescape(m.group(1)).strip() or None


def parse_iso(ts: str | None) -> datetime | None:
    """ISO timestamp → aware UTC datetime.

    Upstream mixes ``…Z``-suffixed and NAIVE timestamps in the same file; a
    naive result would never equal the aware value Postgres hands back, so the
    change detection would re-download those icons on every sync run.
    """

    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def simple_icons_alias_map(data_json: bytes | None) -> dict[str, list[str]]:
    """``title → aliases`` from simple-icons' ``_data/simple-icons.json``.

    The JSON schema shifted over releases (top-level list vs ``{"icons": []}``);
    tolerate both and never fail the sync over alias niceties.
    """

    if not data_json:
        return {}
    try:
        parsed = json.loads(data_json)
    except (ValueError, UnicodeDecodeError):
        return {}
    entries = parsed.get("icons") if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list):
        return {}
    out: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        aliases = entry.get("aliases")
        if not title or not isinstance(aliases, dict):
            continue
        aka = [a for a in (aliases.get("aka") or []) if isinstance(a, str)]
        loc = [v for v in (aliases.get("loc") or {}).values() if isinstance(v, str)]
        merged = aka + loc
        if merged:
            out[title] = merged
    return out


def humanize_slug(slug: str) -> str:
    """Fallback display name: ``ubiquiti-unifi`` → ``Ubiquiti Unifi``."""

    return " ".join(part.capitalize() for part in re.split(r"[-_]+", slug) if part)


# ── Sync state ────────────────────────────────────────────────────────


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)

    def summary(self, source: str) -> str:
        line = f"[{source}] created={self.created} updated={self.updated} skipped={self.skipped}"
        if self.failed:
            line += f" FAILED={len(self.failed)} ({', '.join(self.failed[:10])}…)"
        return line


def _existing_stmt():
    """All rows, bodies deferred — the sync never needs old bytes in memory."""

    return select(LibraryIcon).options(defer(LibraryIcon.body), defer(LibraryIcon.dark_body))


async def _get_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    last_exc: Exception | None = None
    for _ in range(_RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:  # noqa: PERF203
            last_exc = exc
            await asyncio.sleep(0.5)
    raise last_exc  # type: ignore[misc]


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


# ── dashboard-icons ───────────────────────────────────────────────────


async def sync_dashboard_icons(limit: int | None = None) -> SyncStats:
    stats = SyncStats()
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        metadata = json.loads(await _get_bytes(client, f"{DASHBOARD_CDN}/metadata.json"))
        tree = json.loads(await _get_bytes(client, f"{DASHBOARD_CDN}/tree.json"))
        svg_files = set(tree.get("svg") or [])
        png_files = set(tree.get("png") or [])

        with SessionLocal() as db:
            existing = {row.slug: row for row in db.execute(_existing_stmt()).scalars()}

            slugs = sorted(metadata.keys())
            if limit:
                slugs = slugs[:limit]

            sem = asyncio.Semaphore(_CONCURRENCY)
            pending = 0

            async def _one(slug: str) -> None:
                nonlocal pending
                meta = metadata.get(slug) or {}
                fmt = "svg" if meta.get("base") == "svg" else "png"
                available = svg_files if fmt == "svg" else png_files
                if f"{slug}.{fmt}" not in available and f"{slug}-dark.{fmt}" not in available:
                    stats.skipped += 1
                    return
                upstream_ts = parse_iso((meta.get("update") or {}).get("timestamp"))

                row = existing.get(slug)
                if (
                    row is not None
                    and row.source == "dashboard-icons"
                    and upstream_ts is not None
                    and row.upstream_updated_at == upstream_ts
                ):
                    stats.skipped += 1
                    return

                plan = plan_variants(slug, fmt, available)
                try:
                    async with sem:
                        body = await _get_bytes(client, f"{DASHBOARD_CDN}/{fmt}/{plan.light_file}")
                        dark_body = (
                            await _get_bytes(client, f"{DASHBOARD_CDN}/{fmt}/{plan.dark_file}")
                            if plan.dark_file
                            else None
                        )
                except Exception:  # noqa: BLE001 — ein kaputter Download killt nie den Lauf
                    stats.failed.append(slug)
                    return

                aliases = [a for a in (meta.get("aliases") or []) if isinstance(a, str)]
                categories = [c for c in (meta.get("categories") or []) if isinstance(c, str)]
                _upsert(
                    db,
                    existing,
                    slug=slug,
                    name=humanize_slug(slug),
                    aliases=aliases,
                    categories=categories,
                    source="dashboard-icons",
                    monochrome=False,
                    file_format=fmt,
                    body=body,
                    dark_format=fmt if dark_body else None,
                    dark_body=dark_body,
                    upstream_updated_at=upstream_ts,
                    stats=stats,
                )
                pending += 1
                if pending % _COMMIT_EVERY == 0:
                    db.commit()

            await asyncio.gather(*(_one(s) for s in slugs))
            db.commit()
    return stats


# ── simple-icons ──────────────────────────────────────────────────────


async def sync_simple_icons(limit: int | None = None) -> SyncStats:
    stats = SyncStats()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        registry = json.loads(await _get_bytes(client, SIMPLE_ICONS_REGISTRY))
        tarball_url = (registry.get("dist") or {}).get("tarball")
        if not tarball_url:
            raise RuntimeError("npm registry response had no dist.tarball")
        tarball = await _get_bytes(client, tarball_url)

    icons: list[tuple[str, bytes]] = []
    data_json: bytes | None = None
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        for member in tf.getmembers():
            if member.name.endswith("_data/simple-icons.json"):
                f = tf.extractfile(member)
                data_json = f.read() if f else None
            elif member.name.startswith("package/icons/") and member.name.endswith(".svg"):
                slug = member.name.rsplit("/", 1)[-1][: -len(".svg")]
                f = tf.extractfile(member)
                if f:
                    icons.append((slug, f.read()))

    aliases_by_title = simple_icons_alias_map(data_json)

    icons.sort(key=lambda t: t[0])
    if limit:
        icons = icons[:limit]

    with SessionLocal() as db:
        existing = {row.slug: row for row in db.execute(_existing_stmt()).scalars()}
        pending = 0
        for slug, body in icons:
            row = existing.get(slug)
            # dashboard-icons owns the slug → simple-icons never overwrites.
            if row is not None and row.source == "dashboard-icons":
                stats.skipped += 1
                continue
            if row is not None and row.sha256 == _sha(body):
                stats.skipped += 1
                continue
            title = svg_title(body) or humanize_slug(slug)
            _upsert(
                db,
                existing,
                slug=slug,
                name=title,
                aliases=aliases_by_title.get(title, []),
                categories=[],
                source="simple-icons",
                monochrome=True,
                file_format="svg",
                body=body,
                dark_format=None,
                dark_body=None,
                upstream_updated_at=None,
                stats=stats,
            )
            pending += 1
            if pending % _COMMIT_EVERY == 0:
                db.commit()
        db.commit()
    return stats


# ── Upsert ────────────────────────────────────────────────────────────


def _upsert(
    db,
    existing: dict[str, LibraryIcon],
    *,
    slug: str,
    name: str,
    aliases: list[str],
    categories: list[str],
    source: str,
    monochrome: bool,
    file_format: str,
    body: bytes,
    dark_format: str | None,
    dark_body: bytes | None,
    upstream_updated_at: datetime | None,
    stats: SyncStats,
) -> None:
    row = existing.get(slug)
    if row is None:
        row = LibraryIcon(slug=slug)
        db.add(row)
        existing[slug] = row
        stats.created += 1
    else:
        stats.updated += 1
    row.name = name
    row.aliases = aliases or None
    row.categories = categories or None
    row.source = source
    row.monochrome = monochrome
    row.file_format = file_format
    row.body = body
    row.sha256 = _sha(body)
    row.file_size_bytes = len(body)
    row.dark_file_format = dark_format
    row.dark_body = dark_body
    row.dark_sha256 = _sha(dark_body) if dark_body else None
    row.upstream_updated_at = upstream_updated_at


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror icon collections into library_icons")
    parser.add_argument(
        "--source",
        choices=["all", "dashboard-icons", "simple-icons"],
        default="all",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap per source (smoke tests)")
    args = parser.parse_args(argv)

    exit_code = 0
    if args.source in ("all", "dashboard-icons"):
        stats = asyncio.run(sync_dashboard_icons(limit=args.limit))
        print(stats.summary("dashboard-icons"))
        if stats.failed:
            exit_code = 1
    if args.source in ("all", "simple-icons"):
        stats = asyncio.run(sync_simple_icons(limit=args.limit))
        print(stats.summary("simple-icons"))
        if stats.failed:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
