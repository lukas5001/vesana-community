"""Icon library: pure sync-helper tests + DB-backed API tests."""

from __future__ import annotations

import hashlib

from app.sync_icon_library import (
    humanize_slug,
    parse_iso,
    plan_variants,
    simple_icons_alias_map,
    svg_title,
)
from tests.conftest import requires_db

# --------------------------------------------------------------------------- #
# Pure helpers (always run)
# --------------------------------------------------------------------------- #


def test_plan_variants_light_suffix_becomes_dark_body() -> None:
    plan = plan_variants("github", "svg", {"github.svg", "github-light.svg"})
    assert plan.light_file == "github.svg"
    assert plan.dark_file == "github-light.svg"


def test_plan_variants_dark_suffix_becomes_light_body() -> None:
    # Base artwork is light-colored → the "-dark" file is the light-mode body
    # and the base file serves dark mode.
    plan = plan_variants("1password", "svg", {"1password.svg", "1password-dark.svg"})
    assert plan.light_file == "1password-dark.svg"
    assert plan.dark_file == "1password.svg"


def test_plan_variants_no_variant() -> None:
    plan = plan_variants("cisco", "svg", {"cisco.svg"})
    assert plan.light_file == "cisco.svg"
    assert plan.dark_file is None


def test_svg_title_extracts_and_unescapes() -> None:
    body = b'<svg xmlns="x"><title>AT&amp;T</title><path d="M0 0"/></svg>'
    assert svg_title(body) == "AT&T"


def test_svg_title_missing_returns_none() -> None:
    assert svg_title(b"<svg><path d='M0 0'/></svg>") is None


def test_simple_icons_alias_map_tolerates_both_schemas() -> None:
    flat = b'[{"title": "Amazon Web Services", "aliases": {"aka": ["AWS"]}}]'
    wrapped = (
        b'{"icons": [{"title": "VMware",'
        b' "aliases": {"aka": ["ESXi"], "loc": {"de-DE": "VMware GmbH"}}}]}'
    )
    assert simple_icons_alias_map(flat) == {"Amazon Web Services": ["AWS"]}
    assert simple_icons_alias_map(wrapped) == {"VMware": ["ESXi", "VMware GmbH"]}


def test_simple_icons_alias_map_garbage_is_empty() -> None:
    assert simple_icons_alias_map(None) == {}
    assert simple_icons_alias_map(b"not json") == {}
    assert simple_icons_alias_map(b'{"icons": "nope"}') == {}


def test_humanize_slug() -> None:
    assert humanize_slug("ubiquiti-unifi") == "Ubiquiti Unifi"
    assert humanize_slug("proxmox") == "Proxmox"


def test_parse_iso_handles_z_and_garbage() -> None:
    ts = parse_iso("2026-01-20T12:24:26.174Z")
    assert ts is not None and ts.tzinfo is not None
    assert parse_iso("wat") is None
    assert parse_iso(None) is None


def test_parse_iso_naive_timestamps_become_utc() -> None:
    # Upstream metadata mixes "…Z" and naive timestamps; naive must come back
    # aware (UTC), otherwise the sync's change detection re-downloads forever.
    naive = parse_iso("2025-10-04T13:23:43.208364")
    zulu = parse_iso("2025-10-04T13:23:43.208364Z")
    assert naive is not None and naive.tzinfo is not None
    assert naive == zulu


# --------------------------------------------------------------------------- #
# DB-backed API tests
# --------------------------------------------------------------------------- #

_SVG_LIGHT = b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="#1b1f23" d="M0 0h4v4H0z"/></svg>'
_SVG_DARK = b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="#fff" d="M0 0h4v4H0z"/></svg>'


def _make_icon(
    db,
    *,
    slug: str,
    name: str,
    source: str = "dashboard-icons",
    aliases: list[str] | None = None,
    monochrome: bool = False,
    dark: bool = False,
):
    from app.models.library_icon import LibraryIcon

    icon = LibraryIcon(
        slug=slug,
        name=name,
        aliases=aliases,
        categories=["network"],
        source=source,
        monochrome=monochrome,
        file_format="svg",
        body=_SVG_LIGHT,
        sha256=hashlib.sha256(_SVG_LIGHT).hexdigest(),
        file_size_bytes=len(_SVG_LIGHT),
        dark_file_format="svg" if dark else None,
        dark_body=_SVG_DARK if dark else None,
        dark_sha256=hashlib.sha256(_SVG_DARK).hexdigest() if dark else None,
    )
    db.add(icon)
    db.commit()
    return icon.slug


def _seed(rows):
    from app.db import SessionLocal

    with SessionLocal() as db:
        return rows(db)


@requires_db
def test_list_and_alias_search(db_app_client) -> None:
    _seed(lambda db: _make_icon(db, slug="proxmox", name="Proxmox", dark=True))
    _seed(
        lambda db: _make_icon(
            db,
            slug="vmware",
            name="VMware",
            source="simple-icons",
            aliases=["ESXi"],
            monochrome=True,
        )
    )

    r = db_app_client.get("/api/v1/icon-library")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    by_slug = {i["slug"]: i for i in data["items"]}
    assert by_slug["proxmox"]["has_dark"] is True
    assert by_slug["vmware"]["monochrome"] is True

    # Alias search: "esxi" only matches vmware.
    r = db_app_client.get("/api/v1/icon-library", params={"q": "esxi"})
    slugs = [i["slug"] for i in r.json()["items"]]
    assert slugs == ["vmware"]

    # Source filter.
    r = db_app_client.get("/api/v1/icon-library", params={"source": "simple-icons"})
    assert all(i["source"] == "simple-icons" for i in r.json()["items"])


@requires_db
def test_file_serving_variants_and_etag(db_app_client) -> None:
    _seed(lambda db: _make_icon(db, slug="synology", name="Synology", dark=True))
    _seed(lambda db: _make_icon(db, slug="qnap", name="QNAP", dark=False))

    r = db_app_client.get("/api/v1/icon-library/synology/file")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.content == _SVG_LIGHT
    etag = r.headers["etag"]

    # ETag revalidation → 304 without a body.
    r304 = db_app_client.get("/api/v1/icon-library/synology/file", headers={"If-None-Match": etag})
    assert r304.status_code == 304

    # Dark variant served when present …
    rd = db_app_client.get("/api/v1/icon-library/synology/file", params={"variant": "dark"})
    assert rd.content == _SVG_DARK
    # … and silently falls back to light when absent.
    rf = db_app_client.get("/api/v1/icon-library/qnap/file", params={"variant": "dark"})
    assert rf.content == _SVG_LIGHT

    r404 = db_app_client.get("/api/v1/icon-library/does-not-exist/file")
    assert r404.status_code == 404


@requires_db
def test_stats(db_app_client) -> None:
    _seed(lambda db: _make_icon(db, slug="fortinet", name="Fortinet"))
    r = db_app_client.get("/api/v1/icon-library/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert "dashboard-icons" in data["sources"]
