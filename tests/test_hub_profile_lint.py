"""Profil-Prüfung im Hub: Fingerabdruck für Vesana, MIB-Bibliothek als Typquelle,
Live-Funde in der Admin-Prüfung.

Die Fälle sind echte Defekte aus dem Mess-Audit 2026-10-22 (Hub-Berichte):
TrueNAS „ZPool I/O errors" und HP ProCurve „Memory" (freie Bytes als Prozent).
Uploads von Instanzen gibt es seit dem Umbau zur Vesana-Bibliothek nicht mehr —
die Prüfung läuft deshalb direkt über den Dienst.
"""

from __future__ import annotations

from datetime import date

from app.profile_lint import lint
from app.services import profile_lint as profile_lint_service
from tests.conftest import requires_db

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


def test_defektes_bundle_hat_einen_fehler() -> None:
    ergebnis = lint.pruefe_bundle(_kaputt(), lint.MibAufloeser.mit_standard([]))
    assert ergebnis.fehler, "TrueNAS-awk-Defekt muss als Fehler erkannt werden"


@requires_db
def test_mib_typen_aus_der_hub_bibliothek_gelten_in_der_pruefung(db_app_client):
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
        fehler = profile_lint_service.pruefe(db, bundle).fehler
        assert fehler, "freie Bytes als Prozent muss ein Fehler sein"
        assert "hpLocalMemFreeBytes" in lint.kurztext(fehler)


def test_lint_info_nennt_version_und_fingerabdruck(client):
    resp = client.get("/api/v1/profile-lint")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fingerprint"] == lint.FINGERPRINT
    assert body["lint_version"] == lint.LINT_VERSION
    assert set(body["werkzeuge"]) == {"bash", "sh", "awk", "shellcheck"}


@requires_db
def test_admin_pruefung_zeigt_die_warnungen_live(db_app_client):
    from app.db import SessionLocal
    from app.models.community_profile import CommunityProfile
    from app.models.community_profile_version import CommunityProfileVersion
    from tests.test_admin_rework import _login

    with SessionLocal() as db:
        profile = CommunityProfile(name="Lint Testprofil", tier="official", approved=True)
        db.add(profile)
        db.flush()
        db.add(
            CommunityProfileVersion(
                profile_id=profile.id,
                version_tag="1.0.0",
                bundle_json=_nur_warnung(),
                is_current=True,
            )
        )
        db.commit()
        pid = profile.id
    _login(db_app_client)
    seite = db_app_client.get(f"/admin/profiles/{pid}").text
    assert "1 Funde der Profil-Prüfung" in seite
    assert "hrProcessorLoad mit festem Index" in seite
    # Hinweise (SSL-Tage) zählen dort nicht mit — nur Fehler und Warnungen.
    assert "ssl_certificate ohne warn_days" not in seite
