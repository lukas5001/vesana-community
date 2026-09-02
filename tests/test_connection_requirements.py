"""🔒 Verbindungsbedarf kommt aus der check_config, NIE aus dem Check-TYP.

Zwei Fehler derselben Bauart, beide hier festgenagelt:

* Ein `http_json`-Check galt pauschal als „API-Token nötig" — damit trug jedes
  Profil, das eine UNAUTHENTIFIZIERTE Geräte-API abfragt (Sonos auf :1400),
  eine Token-Pille im Host-Assistenten der Instanzen.
* `auth: "device_api"` (Benutzer + Passwort, OPNsense) war gar nicht bekannt —
  der Assistent sagte nichts, der Nutzer legte den Host ohne Konto an und sah
  danach UNKNOWN-Checks.
"""

from app.services.uploads import _derive_connection_requirements


def _bundle(*configs, typ: str = "http_json"):
    return {"checks": [{"check_type": typ, "check_config": c} for c in configs]}


def test_http_json_allein_verlangt_keinen_token():
    # Sonos & Co.: die Geräte-API fragt niemanden nach Zugangsdaten.
    d = _derive_connection_requirements(_bundle({"url": "http://{host}:1400/status"}))
    assert d["api_token"] is False
    assert d["device_api"] is False


def test_platzhalter_verlangt_den_token():
    d = _derive_connection_requirements(
        _bundle(
            {
                "url": "https://{host}:8006/api2/json",
                "headers": {"Authorization": "PVEAPIToken={api_token}"},
            }
        )
    )
    assert d["api_token"] is True


def test_device_api_wird_erkannt():
    d = _derive_connection_requirements(
        _bundle({"url": "https://{host}/api/core/system/status", "auth": "device_api"})
    )
    assert d["device_api"] is True
    # EINE Zugangsart: „braucht API-Zugang" gilt auch fürs Geräte-KONTO — die
    # Instanz-Seite hat dafür ein Feldpaar, nicht zwei Formulare.
    assert d["api_token"] is True


def test_vsphere_braucht_api_zugang_ohne_platzhalter():
    d = _derive_connection_requirements(
        {"checks": [{"check_type": "vsphere", "check_config": {"metric": "cpu"}}]}
    )
    assert d["api_token"] is True


def test_snmp_und_ssh_haengen_am_typ():
    d = _derive_connection_requirements(
        {
            "checks": [
                {"check_type": "snmp_oid", "check_config": {"oid": "1.3.6.1.2.1.1.3.0"}},
                {"check_type": "ssh_script", "check_config": {"script_id": "x"}},
            ]
        }
    )
    assert d["snmp"] is True
    assert d["ssh"] is True
    assert d["api_token"] is False
    assert d["device_api"] is False


def test_leeres_bundle_verlangt_nichts():
    assert _derive_connection_requirements({}) == {
        "snmp": False,
        "ssh": False,
        "api_token": False,
        "device_api": False,
    }
