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


@requires_db
def test_kurator_befehl_veroeffentlicht_ueber_denselben_dienst(db_app_client, monkeypatch, capsys):
    import io
    import json as _json

    from app import publish_mib

    eintrag = {
        "directory": _directory(),
        "license": "MikroTik — derived directory only",
        "license_allows_source": False,
        "checked_at": "2026-10-14",
        "source_text": "MIKROTIK-MIB DEFINITIONS ::= BEGIN END",
    }
    kaputt = {
        "directory": {"format": 1, "module": "bad name"},
        "license": "x",
        "checked_at": "2026-10-14",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(_json.dumps([eintrag, kaputt])))
    rc = publish_mib.main(["--curator", "test"])
    out = capsys.readouterr()
    assert rc == 1 and "FEHLER" in out.err and "neu: MIKROTIK-MIB" in out.out
    detail = db_app_client.get("/api/v1/mibs/MIKROTIK-MIB").json()
    # Lizenz-Linie gilt auch hier: keine Beschreibungen, kein Text.
    assert all(o.get("description") is None for o in detail["directory"]["objects"])
    assert db_app_client.get("/api/v1/mibs/MIKROTIK-MIB/source").status_code == 404


@requires_db
def test_kurator_befehl_probelauf_speichert_nichts(db_app_client, monkeypatch):
    import io
    import json as _json

    from app import publish_mib

    eintrag = {"directory": _directory(), "license": "x", "checked_at": "2026-10-14"}
    monkeypatch.setattr("sys.stdin", io.StringIO(_json.dumps([eintrag])))
    assert publish_mib.main(["--curator", "test", "--dry-run"]) == 0
    assert db_app_client.get("/api/v1/mibs").json()["total"] == 0


# ── Hub-Seiten: Menschen sehen, was Instanzen holen ────────────────────────

from app.services.mib_library import object_page, revision_date  # noqa: E402


def _big_directory(n: int = 450) -> dict:
    d = _directory()
    d["objects"] = d["objects"] + [
        {"oid": f"{ENT}.1.1.9.{i}", "name": f"mtxrFill{i}", "kind": "column", "syntax": "Gauge32"}
        for i in range(n)
    ]
    return d


def test_revision_datum_lesbar_auch_mit_zweistelligem_jahr():
    assert revision_date("202607070000Z") == "2026-07-07"
    assert revision_date("9901010000Z") == "1999-01-01"
    assert revision_date(None) is None and revision_date("") is None


def test_objektseite_filtert_nach_name_oid_und_art_und_blaettert():
    d = _big_directory()
    alle = object_page(d)
    assert alle.total_all == 452 and alle.pages == 3 and len(alle.items) == 200
    assert object_page(d, page=99).page == 3  # über das Ende ⇒ letzte Seite, nie leer
    assert [o["name"] for o in object_page(d, q="temperature").items] == ["mtxrHlTemperature"]
    nach_oid = object_page(d, q=ENT + ".1.1.3")
    assert {o["name"] for o in nach_oid.items} == {"mtxrHlTemperature", "mtxrHlPowerSupplyState"}
    # „1.3" ist zu kurz geschrieben, um als OID zu gelten? Nein — Präfix mit Punkt zählt.
    assert object_page(d, kind="scalar").total == 2
    assert object_page(d, kind="column").total == 450
    assert object_page(d, kind="unsinn").total == 452  # unbekannte Art ⇒ kein Filter
    enum = object_page(d, q="PowerSupply").items[0]
    assert enum["enum"] == [{"value": 0, "label": "false"}, {"value": 1, "label": "true"}]
    assert enum["enum_more"] == 0


def _visitor(client, make_login_jwt, sub="77777777-7777-7777-7777-777777777777"):
    token = make_login_jwt(sub=sub, display_name="MibVisitor", jti=f"mib-{sub[:4]}")
    client.get("/auth", params={"token": token}, follow_redirects=False)


def _profile_with_mibs(client, make_login_jwt, name, mibs, sub):
    login = make_login_jwt(sub=sub, display_name=name, jti=f"up-{sub[:6]}")
    api = client.post("/api/v1/auth/exchange", json={"token": login}).json()["api_token"]
    bundle = {
        "schema_version": 1,
        "profile": {"name": name, "vendor": "MikroTik", "category": "network"},
        "checks": [
            {"name": "Temp", "check_type": "snmp", "check_config": {"oid": ENT + ".1.1.3.11.0"}}
        ],
    }
    if mibs is not None:
        bundle["mibs"] = mibs
    r = client.post(
        "/api/v1/profiles/upload",
        json={"bundle": bundle},
        headers={"Authorization": f"Bearer {api}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["profile_id"]


@requires_db
def test_mib_seiten_brauchen_wie_alle_seiten_eine_sitzung(db_app_client):
    _publish(db_app_client)
    assert db_app_client.get("/mibs").status_code == 403
    assert db_app_client.get("/mibs/MIKROTIK-MIB").status_code == 403
    # die Maschinen-API bleibt offen (Instanzen holen ohne Sitzung)
    assert db_app_client.get("/api/v1/mibs").status_code == 200


@requires_db
def test_bibliothek_zeigt_module_nutzer_und_fehlende_mibs(db_app_client, make_login_jwt):
    c = db_app_client
    _publish(c)
    pid = _profile_with_mibs(
        c,
        make_login_jwt,
        "MikroTik Router",
        [{"module": "MIKROTIK-MIB"}, {"module": "GHOST-MIB"}],
        "88888888-8888-8888-8888-888888888888",
    )
    _visitor(c, make_login_jwt)
    page = c.get("/mibs").text
    assert 'href="/mibs/MIKROTIK-MIB"' in page
    assert f'href="/p/{pid}"' in page  # „benutzt von"
    assert "GHOST-MIB" in page  # genannt, aber nicht in der Bibliothek — ehrlich gesagt
    assert 'href="/mibs"' in page and 'aria-current="page"' in page  # Navigation
    # Profilseite verlinkt die vorhandene MIB, die fehlende nicht
    detail = c.get(f"/p/{pid}").text
    assert '<a href="/mibs/MIKROTIK-MIB">' in detail
    assert 'href="/mibs/GHOST-MIB"' not in detail


@requires_db
def test_objektsuche_findet_die_mib_eines_objekts(db_app_client, make_login_jwt):
    c = db_app_client
    _publish(c, directory=_big_directory(5))
    _visitor(c, make_login_jwt)
    by_name = c.get("/mibs", params={"q": "hltemp"}).text
    assert "mtxrHlTemperature" in by_name and "/mibs/MIKROTIK-MIB?q=mtxrHlTemperature" in by_name
    by_oid = c.get("/mibs", params={"q": ENT + ".1.1.3.15"}).text
    assert "mtxrHlPowerSupplyState" in by_oid and "mtxrHlTemperature" not in by_oid
    # LIKE-Zeichen sind Text, kein Joker
    assert "mtxrFill" not in c.get("/mibs", params={"q": "mtxr%Fill"}).text


@requires_db
def test_modulseite_zeigt_objekte_werte_und_lizenz(db_app_client, make_login_jwt):
    c = db_app_client
    _publish(c, directory=_big_directory(450))
    _visitor(c, make_login_jwt)
    page = c.get("/mibs/MIKROTIK-MIB")
    assert page.status_code == 200
    html = page.text
    assert "mtxrHlTemperature" in html and ENT + ".1.1.3.11" in html
    assert "°C" in html and '<b class="num">1</b> true' in html
    assert "Original-MIB herunterladen" not in html and "Download original MIB" not in html
    assert "page=2" in html  # 452 Objekte ⇒ blättern
    assert "mtxrHlTemperature" not in c.get("/mibs/MIKROTIK-MIB", params={"page": 2}).text
    gefiltert = c.get("/mibs/MIKROTIK-MIB", params={"kind": "scalar"}).text
    assert "mtxrFill0" not in gefiltert and "mtxrHlPowerSupplyState" in gefiltert
    assert c.get("/mibs/NOPE-MIB").status_code == 404


@requires_db
def test_download_nur_wenn_lizenz_es_erlaubt_und_text_da_ist(db_app_client, make_login_jwt):
    c = db_app_client
    # Lizenz erlaubt, aber kein Text geschickt ⇒ kein Download-Versprechen
    r = _publish(c, license="IETF (BSD)", license_allows_source=True, source_text=None)
    assert r.status_code == 201 and r.json()["has_source"] is False
    assert c.get("/api/v1/mibs").json()["items"][0]["has_source"] is False
    _visitor(c, make_login_jwt)
    assert "/api/v1/mibs/MIKROTIK-MIB/source" not in c.get("/mibs/MIKROTIK-MIB").text
    # mit Text ⇒ Download da und funktioniert
    _publish(c, license="IETF (BSD)", license_allows_source=True)
    html = c.get("/mibs/MIKROTIK-MIB").text
    assert 'href="/api/v1/mibs/MIKROTIK-MIB/source"' in html
    assert c.get("/api/v1/mibs/MIKROTIK-MIB/source").status_code == 200


@requires_db
def test_admin_mib_seite_entfernen_und_wiederherstellen(db_app_client, make_login_jwt):
    import re

    c = db_app_client
    _publish(c)
    _profile_with_mibs(
        c, make_login_jwt, "SNMP ohne Verweis", None, "99999999-9999-9999-9999-999999999999"
    )
    c.post("/admin/login", data={"username": "admin", "password": "test-admin-pass"})
    html = c.get("/admin/mibs").text
    assert "MIKROTIK-MIB" in html and "SNMP ohne Verweis" in html
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)
    assert c.post("/admin/mibs/MIKROTIK-MIB/remove").status_code in (400, 403)  # ohne CSRF nie
    r = c.post("/admin/mibs/MIKROTIK-MIB/remove", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/api/v1/mibs/MIKROTIK-MIB").status_code == 404
    assert "row--removed" in c.get("/admin/mibs").text
    r = c.post("/admin/mibs/MIKROTIK-MIB/restore", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/api/v1/mibs/MIKROTIK-MIB").status_code == 200

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.admin_audit import AdminAuditLog

    with SessionLocal() as db:
        actions = [a.action for a in db.execute(select(AdminAuditLog)).scalars()]
    assert "mib.remove" in actions and "mib.restore" in actions
