"""MIB-Vorschläge aus Instanzen: unsichtbar bis zur Freigabe, Freigabe über
denselben Dienst wie der Kurator (Lizenz-Linie), Daten nach der Entscheidung
gelöscht, Admin-Seiten mit CSRF."""

from __future__ import annotations

import re

from sqlalchemy import select

from tests.conftest import requires_db
from tests.test_mibs import ADMIN, _directory, _publish

SUB_A = "a1a1a1a1-0000-4000-8000-000000000001"
SUB_B = "b2b2b2b2-0000-4000-8000-000000000002"


def _bearer(client, make_login_jwt, sub: str) -> dict:
    login = make_login_jwt(sub=sub, display_name=f"inst-{sub[:4]}", jti=f"sub-{sub[:8]}")
    token = client.post("/api/v1/auth/exchange", json={"token": login}).json()["api_token"]
    return {"Authorization": f"Bearer {token}"}


def _submit(client, headers, **over):
    body = {
        "directory": _directory(),
        "license": "MikroTik MIB — redistribution allowed with copyright notice",
        "allows_source": True,
        "source_url": "https://box.mikrotik.com/d/mikrotik-mib",
        "source_text": "-- Copyright MikroTik\nMIKROTIK-MIB DEFINITIONS ::= BEGIN ... END",
        "note": "von unserem CCR2004",
    }
    body.update(over)
    return client.post("/api/v1/mib-submissions", json=body, headers=headers)


def _row(client_db_id: str):
    from app.db import SessionLocal
    from app.models.mib_submission import MibSubmission

    with SessionLocal() as db:
        return db.execute(
            select(MibSubmission).where(MibSubmission.id == client_db_id)
        ).scalar_one()


@requires_db
def test_vorschlag_ist_bis_zur_freigabe_unsichtbar(db_app_client, make_login_jwt):
    c = db_app_client
    r = _submit(c, _bearer(c, make_login_jwt, SUB_A))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending" and body["module"] == "MIKROTIK-MIB"
    assert body["duplicate"] is False
    assert c.get("/api/v1/mibs").json()["total"] == 0
    assert c.get("/api/v1/mibs/MIKROTIK-MIB").status_code == 404


@requires_db
def test_vorschlagen_nur_mit_instanz_token(db_app_client):
    assert _submit(db_app_client, {}).status_code == 401


@requires_db
def test_dasselbe_verzeichnis_zweimal_ist_kein_zweiter_vorschlag(db_app_client, make_login_jwt):
    c = db_app_client
    h = _bearer(c, make_login_jwt, SUB_A)
    first = _submit(c, h).json()
    second = _submit(c, h).json()
    assert second["duplicate"] is True and second["id"] == first["id"]
    assert len(c.get("/api/v1/mib-submissions", headers=h).json()["items"]) == 1


@requires_db
def test_schon_in_der_bibliothek_ist_409(db_app_client, make_login_jwt):
    c = db_app_client
    _publish(c)
    r = _submit(c, _bearer(c, make_login_jwt, SUB_A))
    assert r.status_code == 409 and r.json()["detail"] == "already_in_library"
    # eine NEUERE Revision ist willkommen
    newer = _submit(
        c, _bearer(c, make_login_jwt, SUB_B), directory=_directory(revision="202501010000Z")
    )
    assert newer.status_code == 201


@requires_db
def test_kaputtes_oder_leeres_verzeichnis_wird_abgelehnt(db_app_client, make_login_jwt):
    c = db_app_client
    h = _bearer(c, make_login_jwt, SUB_A)
    broken = _directory()
    broken["objects"][0]["oid"] = "not-an-oid"
    r = _submit(c, h, directory=broken)
    assert r.status_code == 422 and "objects" in r.json()["detail"]
    empty = _directory()
    empty["objects"] = []
    assert _submit(c, h, directory=empty).status_code == 422
    assert _submit(c, h, source_url="javascript:alert(1)").status_code == 422


@requires_db
def test_zu_viele_offene_vorschlaege_ist_429(db_app_client, make_login_jwt, monkeypatch):
    from app.services import mib_submissions

    monkeypatch.setattr(mib_submissions, "MAX_PENDING_PER_INSTANCE", 1)
    c = db_app_client
    h = _bearer(c, make_login_jwt, SUB_A)
    assert _submit(c, h).status_code == 201
    r = _submit(c, h, directory=_directory(revision="202501010000Z"))
    assert r.status_code == 429


@requires_db
def test_fremde_instanz_sieht_und_zieht_nichts_zurueck(db_app_client, make_login_jwt):
    c = db_app_client
    a = _bearer(c, make_login_jwt, SUB_A)
    b = _bearer(c, make_login_jwt, SUB_B)
    sid = _submit(c, a).json()["id"]
    assert c.get("/api/v1/mib-submissions", headers=b).json()["items"] == []
    assert c.delete(f"/api/v1/mib-submissions/{sid}", headers=b).status_code == 404
    r = c.delete(f"/api/v1/mib-submissions/{sid}", headers=a)
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    assert c.delete(f"/api/v1/mib-submissions/{sid}", headers=a).status_code == 409
    row = _row(sid)
    assert row.directory is None and row.source_text is None


@requires_db
def test_freigabe_folgt_der_lizenz_des_admins_nicht_der_angabe(db_app_client, make_login_jwt):
    c = db_app_client
    h = _bearer(c, make_login_jwt, SUB_A)
    sid = _submit(c, h).json()["id"]  # Einsender behauptet: Weitergabe erlaubt
    assert (
        c.post(f"/api/v1/admin/mib-submissions/{sid}/approve", json={"license": "x"}).status_code
        == 401
    )
    r = c.post(
        f"/api/v1/admin/mib-submissions/{sid}/approve",
        json={
            "license": "MikroTik MIB — redistribution not granted",
            "license_allows_source": False,
        },
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    mib = c.get("/api/v1/mibs/MIKROTIK-MIB").json()
    assert mib["license"].startswith("MikroTik MIB — redistribution not granted")
    assert mib["descriptions_included"] is False
    assert all(o.get("description") is None for o in mib["directory"]["objects"])
    assert c.get("/api/v1/mibs/MIKROTIK-MIB/source").status_code == 404
    # aufbewahrt bleibt nur, was die Bibliothek trägt
    row = _row(sid)
    assert row.directory is None and row.source_text is None and row.reviewed_by
    items = c.get("/api/v1/mib-submissions", headers=h).json()["items"]
    assert items[0]["status"] == "approved"
    # das Admin-Protokoll nennt Vorschlag und Instanz
    from app.db import SessionLocal
    from app.models.admin_audit import AdminAuditLog

    with SessionLocal() as db:
        log = db.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "mib.submission.approve")
        ).scalar_one()
    assert log.details["submission"] == sid and log.details["license_allows_source"] is False


@requires_db
def test_freigabe_mit_erlaubter_weitergabe_liefert_text(db_app_client, make_login_jwt):
    c = db_app_client
    sid = _submit(c, _bearer(c, make_login_jwt, SUB_A)).json()["id"]
    c.post(
        f"/api/v1/admin/mib-submissions/{sid}/approve",
        json={"license": "BSD", "license_allows_source": True},
        headers=ADMIN,
    )
    src = c.get("/api/v1/mibs/MIKROTIK-MIB/source")
    assert src.status_code == 200 and "Copyright MikroTik" in src.text


@requires_db
def test_ablehnung_nennt_den_grund_und_ist_endgueltig(db_app_client, make_login_jwt):
    c = db_app_client
    h = _bearer(c, make_login_jwt, SUB_A)
    sid = _submit(c, h).json()["id"]
    r = c.post(
        f"/api/v1/admin/mib-submissions/{sid}/reject",
        json={"reason": "Lizenz verbietet die Weitergabe"},
        headers=ADMIN,
    )
    assert r.status_code == 200
    item = c.get("/api/v1/mib-submissions", headers=h).json()["items"][0]
    assert item["status"] == "rejected" and item["reason"] == "Lizenz verbietet die Weitergabe"
    again = c.post(
        f"/api/v1/admin/mib-submissions/{sid}/approve", json={"license": "x"}, headers=ADMIN
    )
    assert again.status_code == 409
    assert c.get("/api/v1/mibs/MIKROTIK-MIB").status_code == 404
    assert _row(sid).directory is None


@requires_db
def test_admin_seiten_pruefen_und_freigeben(db_app_client, make_login_jwt):
    c = db_app_client
    sid = _submit(c, _bearer(c, make_login_jwt, SUB_A)).json()["id"]
    # ohne Admin-Sitzung kein Originaltext
    assert c.get(f"/admin/mibs/submissions/{sid}/source", follow_redirects=False).status_code in (
        303,
        401,
        403,
    )
    c.post("/admin/login", data={"username": "admin", "password": "test-admin-pass"})
    dash = c.get("/admin").text
    assert "/admin/mibs" in dash and "MIB" in dash
    overview = c.get("/admin/mibs").text
    assert f"/admin/mibs/submissions/{sid}" in overview
    detail = c.get(f"/admin/mibs/submissions/{sid}").text
    assert "Copyright MikroTik" in detail and "mtxrHlTemperature" in detail
    assert "Copyright MikroTik" in c.get(f"/admin/mibs/submissions/{sid}/source").text
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', detail).group(1)
    url = f"/admin/mibs/submissions/{sid}/approve"
    assert c.post(url, data={"license": "BSD"}).status_code in (400, 403)  # ohne CSRF nie
    r = c.post(
        url,
        data={"csrf": csrf, "license": "BSD-2-Clause", "license_allows_source": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert c.get("/api/v1/mibs/MIKROTIK-MIB").json()["license"] == "BSD-2-Clause"
    assert c.get(f"/admin/mibs/submissions/{sid}/source").status_code == 404
    # entschieden ⇒ nicht mehr in der Warteschlange, aber unter „zuletzt entschieden"
    after = c.get("/admin/mibs").text
    assert f"/admin/mibs/submissions/{sid}" not in after and "MIKROTIK-MIB" in after


@requires_db
def test_admin_seite_lehnt_ab_ohne_lizenz_wird_nichts_veroeffentlicht(
    db_app_client, make_login_jwt
):
    c = db_app_client
    sid = _submit(c, _bearer(c, make_login_jwt, SUB_A)).json()["id"]
    c.post("/admin/login", data={"username": "admin", "password": "test-admin-pass"})
    detail = c.get(f"/admin/mibs/submissions/{sid}").text
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', detail).group(1)
    r = c.post(
        f"/admin/mibs/submissions/{sid}/approve",
        data={"csrf": csrf, "license": "  "},
        follow_redirects=False,
    )
    assert r.status_code == 303 and c.get("/api/v1/mibs/MIKROTIK-MIB").status_code == 404
    r = c.post(
        f"/admin/mibs/submissions/{sid}/reject",
        data={"csrf": csrf, "reason": "nur Testdatei"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "nur Testdatei" in c.get(f"/admin/mibs/submissions/{sid}").text
