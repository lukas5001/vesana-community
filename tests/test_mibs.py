"""MIB-Bibliothek: Veröffentlichen nur durch Kuratoren, Lizenz-Linie, Suche,
neueste Revision, Bundle-Verweise (Vesana-Plan mib-und-rate.md §2.5/§6)."""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from app.services.mib_library import mib_refs_of_bundle, revision_key
from app.services.uploads import validate_bundle
from tests.conftest import requires_db

_BASIC = "Basic " + base64.b64encode(b"admin:test-admin-pass").decode()
ADMIN = {"X-Admin-Authorization": _BASIC}

ENT = "1.3.6.1.4.1.14988"


def _directory(revision: str = "202301010000Z", desc: str | None = "Board temperature") -> dict:
    return {
        "format": 1,
        "module": "MIKROTIK-MIB",
        "revision": revision,
        "organization": "MikroTik",
        "identity_oid": ENT + ".1",
        "imports": ["SNMPv2-SMI"],
        "descriptions_included": desc is not None,
        "objects": [
            {
                "oid": ENT + ".1.1.3.11",
                "name": "mtxrHlTemperature",
                "kind": "scalar",
                "syntax": "Integer32",
                "units": "°C",
                "description": desc,
            },
            {
                "oid": ENT + ".1.1.3.15",
                "name": "mtxrHlPowerSupplyState",
                "kind": "scalar",
                "syntax": "Integer32",
                "enum": [[0, "false"], [1, "true"]],
            },
        ],
    }


def _publish(client, **over):
    body = {
        "directory": _directory(),
        "license": "MikroTik MIB — redistribution not granted (derived directory only)",
        "license_allows_source": False,
        "checked_at": "2026-10-14",
        "source_url": "https://box.mikrotik.com/d/mikrotik-mib",
        "source_text": "MIKROTIK-MIB DEFINITIONS ::= BEGIN ... END",
    }
    body.update(over)
    return client.post("/api/v1/admin/mibs", json=body, headers=ADMIN)


# ── reine Logik ────────────────────────────────────────────────────────────


def test_revision_key_ordnet_zweistellige_jahre_vor_vierstellige():
    assert revision_key("9901010000Z") < revision_key("202001010000Z")
    assert revision_key("202301010000Z") > revision_key("202001010000Z")


def test_bundle_mit_mib_verweisen_wird_angenommen():
    b = {
        "schema_version": 1,
        "profile": {"name": "X"},
        "checks": [],
        "mibs": [{"module": "MIKROTIK-MIB", "revision": None}],
    }
    assert validate_bundle(b) is b
    assert mib_refs_of_bundle(b) == [{"module": "MIKROTIK-MIB", "revision": None}]


@pytest.mark.parametrize(
    "mibs",
    [
        "MIKROTIK-MIB",
        [{"module": "../etc/passwd"}],
        [{"module": "X", "url": "http://evil"}],
        [{"module": "X", "revision": 5}],
        [{"module": f"M{i}"} for i in range(51)],
    ],
)
def test_bundle_mit_kaputten_mib_verweisen_wird_abgelehnt(mibs):
    b = {"schema_version": 1, "profile": {"name": "X"}, "checks": [], "mibs": mibs}
    with pytest.raises(HTTPException) as exc:
        validate_bundle(b)
    assert exc.value.status_code == 400


def test_altes_bundle_ohne_mibs_bleibt_unveraendert():
    b = {"schema_version": 1, "profile": {"name": "X"}, "checks": []}
    assert validate_bundle(b) is b
    assert mib_refs_of_bundle(b) == []


# ── API gegen echte DB ─────────────────────────────────────────────────────


@requires_db
def test_veroeffentlichen_nur_mit_admin(db_app_client):
    r = db_app_client.post(
        "/api/v1/admin/mibs",
        json={"directory": _directory(), "license": "x", "checked_at": "2026-10-14"},
    )
    assert r.status_code == 401
    assert db_app_client.get("/api/v1/mibs").json()["total"] == 0


@requires_db
def test_lizenz_ohne_weitergabe_entfernt_text_und_beschreibungen(db_app_client):
    r = _publish(db_app_client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True and body["has_source"] is False
    assert body["enterprise"] == ENT and body["object_count"] == 2
    detail = db_app_client.get("/api/v1/mibs/MIKROTIK-MIB").json()
    assert detail["directory"]["descriptions_included"] is False
    assert all(o.get("description") is None for o in detail["directory"]["objects"])
    assert db_app_client.get("/api/v1/mibs/MIKROTIK-MIB/source").status_code == 404


@requires_db
def test_freie_lizenz_liefert_originaltext(db_app_client):
    assert (
        _publish(db_app_client, license="IETF Trust (BSD)", license_allows_source=True).status_code
        == 201
    )
    detail = db_app_client.get("/api/v1/mibs/MIKROTIK-MIB").json()
    assert detail["directory"]["objects"][0]["description"] == "Board temperature"
    src = db_app_client.get("/api/v1/mibs/MIKROTIK-MIB/source")
    assert src.status_code == 200 and "DEFINITIONS" in src.text
    assert src.headers["x-content-type-options"] == "nosniff"


@requires_db
def test_kaputtes_verzeichnis_wird_abgelehnt(db_app_client):
    bad = _directory()
    bad["objects"][0]["oid"] = "iso.3.6"  # symbolisch statt numerisch
    r = _publish(db_app_client, directory=bad)
    assert r.status_code == 422
    assert "directory invalid" in r.json()["detail"]


@requires_db
def test_neueste_revision_gewinnt_und_aeltere_bleibt_abrufbar(db_app_client):
    assert _publish(db_app_client, directory=_directory("202001010000Z")).status_code == 201
    assert _publish(db_app_client, directory=_directory("202301010000Z")).status_code == 201
    # dieselbe Revision erneut = Ersetzen, keine dritte Zeile
    again = _publish(db_app_client, directory=_directory("202301010000Z"))
    assert again.json()["created"] is False
    lst = db_app_client.get("/api/v1/mibs").json()
    assert lst["total"] == 1 and lst["items"][0]["revision"] == "202301010000Z"
    old = db_app_client.get("/api/v1/mibs/MIKROTIK-MIB", params={"revision": "202001010000Z"})
    assert old.status_code == 200 and old.json()["revision"] == "202001010000Z"


@requires_db
def test_suche_nach_name_hersteller_und_sysobjectid(db_app_client):
    _publish(db_app_client)
    assert db_app_client.get("/api/v1/mibs", params={"q": "mikro"}).json()["total"] == 1
    assert db_app_client.get("/api/v1/mibs", params={"enterprise": ENT}).json()["total"] == 1
    assert db_app_client.get("/api/v1/mibs", params={"oid": ENT + ".1.0"}).json()["total"] == 1
    assert (
        db_app_client.get("/api/v1/mibs", params={"oid": "1.3.6.1.4.1.149880"}).json()["total"] == 0
    )
    assert db_app_client.get("/api/v1/mibs", params={"q": "cisco"}).json()["total"] == 0


@requires_db
def test_etag_spart_den_zweiten_abruf(db_app_client):
    _publish(db_app_client)
    first = db_app_client.get("/api/v1/mibs/MIKROTIK-MIB")
    etag = first.headers["etag"]
    second = db_app_client.get("/api/v1/mibs/MIKROTIK-MIB", headers={"If-None-Match": etag})
    assert second.status_code == 304


@requires_db
def test_entfernen_ist_weich_und_protokolliert(db_app_client):
    _publish(db_app_client)
    assert db_app_client.delete("/api/v1/admin/mibs/MIKROTIK-MIB").status_code == 401
    r = db_app_client.delete("/api/v1/admin/mibs/MIKROTIK-MIB", headers=ADMIN)
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert db_app_client.get("/api/v1/mibs/MIKROTIK-MIB").status_code == 404
    admin_list = db_app_client.get("/api/v1/admin/mibs", headers=ADMIN).json()
    assert admin_list["items"][0]["removed"] is True

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.admin_audit import AdminAuditLog

    with SessionLocal() as db:
        actions = [a.action for a in db.execute(select(AdminAuditLog)).scalars()]
    assert "mib.publish" in actions and "mib.remove" in actions


@requires_db
def test_unbekanntes_modul_ist_404(db_app_client):
    assert db_app_client.get("/api/v1/mibs/NOPE-MIB").status_code == 404
    assert db_app_client.get("/api/v1/mibs/..%2Fx").status_code == 404
