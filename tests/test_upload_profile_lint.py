"""Profil-Prüfung beim Hochladen: Fehler weist ab, Warnung geht durch und ist sichtbar.

Die Fälle sind echte Defekte aus dem Mess-Audit 2026-10-22 (Hub-Berichte):
TrueNAS „ZPool I/O errors" (awk ``\\\\s`` ⇒ immer 0) und HP ProCurve „Memory"
(freie Bytes als Prozent). Mit DB, über die echte HTTP-API.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import date

from app.profile_lint import lint
from app.services import uploads as uploads_service
from tests.conftest import requires_db
from tests.test_uploads import _bearer

TRUENAS_RUMPF = "zpool status | awk '/^\\\\s+[a-z0-9]/ {s+=$3+$4+$5} END{print s+0}'\n"


def _bundle(*, checks: list, scripts: list | None = None, name: str = "Lint Testprofil") -> dict:
    return {
        "schema_version": 1,
        "profile": {"name": name, "vendor": "ixsystems", "category": "nas"},
        "checks": checks,
        "scripts": scripts or [],
    }


def _kaputt() -> dict:
    return _bundle(
        checks=[
            {
                "name": "ZPool I/O errors",
                "check_type": "ssh_script",
                "check_config": {"script_id": "s1"},
            }
        ],
        scripts=[
            {
                "script_id": "s1",
                "name": "ZPool I/O errors",
                "interpreter": "bash",
                "script_body": TRUENAS_RUMPF,
            }
        ],
    )


def _nur_warnung() -> dict:
    return _bundle(
        checks=[
            {
                "name": "CPU Usage",
                "check_type": "snmp",
                "check_config": {"oid": "1.3.6.1.2.1.25.3.3.1.2.1", "unit": "%"},
            },
            {
                "name": "SSL Certificate",
                "check_type": "ssl_certificate",
                "check_config": {"port": 443},
            },
        ]
    )


@requires_db
def test_upload_mit_sicherem_defekt_wird_mit_422_abgewiesen(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    resp = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _kaputt()}, headers=headers
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith("Profil-Prüfung: 1 Fehler")
    assert "Script ZPool I/O errors" in detail and "wörtlichen" in detail
    # Nichts angelegt: weder Profil noch Version.
    liste = db_app_client.get(
        "/api/v1/admin/review-queue?status=all",
        headers={"X-Admin-Authorization": "Basic YWRtaW46dGVzdC1hZG1pbi1wYXNz"},
    )
    assert all(item["name"] != "Lint Testprofil" for item in liste.json())


@requires_db
def test_neue_version_eines_bestehenden_profils_wird_genauso_geprueft(
    db_app_client, make_login_jwt
):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    ok = db_app_client.post(
        "/api/v1/profiles/upload",
        json={"bundle": _nur_warnung(), "version_tag": "v1"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    zweite = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _kaputt(), "version_tag": "v2"}, headers=headers
    )
    assert zweite.status_code == 422, zweite.text


@requires_db
def test_upload_mit_warnungen_geht_durch_und_nennt_sie(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    resp = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _nur_warnung()}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    regeln = sorted((f["schwere"], f["regel"]) for f in resp.json()["lint"])
    assert regeln == [("hinweis", "ssl_tage"), ("warnung", "snmp_einzelkern")]


@requires_db
def test_sauberes_bundle_hat_keine_funde(db_app_client, make_login_jwt):
    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    sauber = _bundle(
        checks=[
            {"name": "HTTP up", "check_type": "http", "check_config": {"url": "http://{host}/"}}
        ]
    )
    resp = db_app_client.post("/api/v1/profiles/upload", json={"bundle": sauber}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["lint"] == []


@requires_db
def test_web_upload_zeigt_den_fehler_im_formular(db_app_client, make_login_jwt):
    token = make_login_jwt(sub=str(uuid.uuid4()), jti=f"lint-{uuid.uuid4()}")
    db_app_client.get("/auth", params={"token": token}, follow_redirects=False)
    datei = io.BytesIO(json.dumps(_kaputt()).encode())
    resp = db_app_client.post(
        "/upload", files={"bundle": ("p.json", datei, "application/json")}, follow_redirects=False
    )
    assert resp.status_code == 400, resp.text
    assert "Profil-Prüfung: 1 Fehler" in resp.text


@requires_db
def test_mib_typen_aus_der_hub_bibliothek_gelten_beim_upload(db_app_client):
    """ProCurve „Memory": hpLocalMemFreeBytes kennt nur die Hub-Bibliothek (NETSWITCH-MIB)."""
    import app.db as db_mod
    from app.models.mib_module import MibModule

    verz = {
        "format": 1,
        "module": "NETSWITCH-MIB",
        "objects": [
            {
                "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.6",
                "name": "hpLocalMemFreeBytes",
                "kind": "column",
                "syntax": "Integer32",
            }
        ],
    }
    with db_mod.SessionLocal() as db:
        db.add(
            MibModule(
                module="NETSWITCH-MIB",
                revision="",
                directory=verz,
                object_count=1,
                sha256="a" * 64,
                license="test",
                checked_at=date(2026, 10, 22),
                curator="test",
            )
        )
        db.commit()
        bundle = _bundle(
            checks=[
                {
                    "name": "Memory",
                    "check_type": "snmp",
                    "check_config": {
                        "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.6.1",
                        "unit": "%",
                    },
                }
            ]
        )
        try:
            uploads_service.create_or_version_profile(
                db,
                instance_uuid=str(uuid.uuid4()),
                bundle=bundle,
                version_tag=None,
                changelog_md=None,
            )
            raise AssertionError("Upload hätte abgewiesen werden müssen")
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 422
            assert "hpLocalMemFreeBytes" in str(exc.detail)
        db.rollback()


def test_lint_info_nennt_version_und_fingerabdruck(client):
    resp = client.get("/api/v1/profile-lint")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fingerprint"] == lint.FINGERPRINT
    assert body["lint_version"] == lint.LINT_VERSION
    assert set(body["werkzeuge"]) == {"bash", "sh", "awk", "shellcheck"}


@requires_db
def test_admin_pruefung_zeigt_die_warnungen_live(db_app_client, make_login_jwt):
    from tests.test_admin_rework import _login

    headers = _bearer(db_app_client, make_login_jwt, str(uuid.uuid4()))
    resp = db_app_client.post(
        "/api/v1/profiles/upload", json={"bundle": _nur_warnung()}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    _login(db_app_client)
    seite = db_app_client.get(f"/admin/review/{resp.json()['profile_id']}").text
    assert "1 Funde der Profil-Prüfung" in seite
    assert "hrProcessorLoad mit festem Index" in seite
    # Hinweise (SSL-Tage) zählen dort nicht mit — nur Fehler und Warnungen.
    assert "ssl_certificate ohne warn_days" not in seite
