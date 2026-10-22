"""Profil-Prüfung (``app/profile_lint``) — jede Regel an einem echten Fall aus dem
Mess-Audit 2026-10-22 (Hub-Berichte), dazu das Gegenstück, das NICHT anschlagen darf.

Die Bibliothek liegt wortgleich im Vesana-Repo
(``tools/checkwahrheit/wahrheit/profile_lint/``); der Fingerabdruck-Anker unten
steht dort mit demselben Wert.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from app.profile_lint import lint

# 🔒 Gleicher Wert in Vesana tests/test_profile_lint_vendored.py — wer lint.py oder
# standard_mib_types.json ändert, ändert beide Repos und beide Konstanten.
FINGERPRINT = "57270ce17fa678a61b920f8eb7a84025eb67b0e8bae3d97ebeb8f78d1765e695"

PAKET = Path(lint.__file__).resolve().parent

# --- Minimale Hersteller-Verzeichnisse (Format 1, wie mib_modules.directory) ----------


def _verz(modul: str, *objekte: dict) -> dict:
    return {"format": 1, "module": modul, "objects": list(objekte)}


NETSWITCH = _verz(
    "NETSWITCH-MIB",
    {
        "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.5",
        "name": "hpLocalMemTotalBytes",
        "kind": "column",
        "syntax": "Integer32",
    },
    {
        "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.6",
        "name": "hpLocalMemFreeBytes",
        "kind": "column",
        "syntax": "Integer32",
    },
    {
        "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.7",
        "name": "hpLocalMemAllocBytes",
        "kind": "column",
        "syntax": "Integer32",
    },
)
FORTINET = _verz(
    "FORTINET-FORTIGATE-MIB",
    {
        "oid": "1.3.6.1.4.1.12356.101.4.1.4",
        "name": "fgSysMemUsage",
        "kind": "scalar",
        "syntax": "Gauge32",
    },
    {
        "oid": "1.3.6.1.4.1.12356.101.4.1.6",
        "name": "fgSysDiskUsage",
        "kind": "scalar",
        "syntax": "Gauge32",
    },
    {
        "oid": "1.3.6.1.4.1.12356.101.4.1.7",
        "name": "fgSysDiskCapacity",
        "kind": "scalar",
        "syntax": "Gauge32",
    },
)
CISCO_FW = _verz(
    "CISCO-FIREWALL-MIB",
    {
        "oid": "1.3.6.1.4.1.9.9.147.1.2.1.1.1.2",
        "name": "cfwHardwareInformation",
        "kind": "column",
        "syntax": "OCTET STRING",
        "tc_name": "SnmpAdminString",
    },
    {
        "oid": "1.3.6.1.4.1.9.9.147.1.2.1.1.1.3",
        "name": "cfwHardwareStatusValue",
        "kind": "column",
        "syntax": "Integer32",
        "enum": [
            [1, "other"],
            [2, "up"],
            [3, "down"],
            [4, "error"],
            [5, "overTemp"],
            [6, "busy"],
            [7, "noMedia"],
            [8, "backup"],
            [9, "active"],
            [10, "standby"],
        ],
    },
)
STATUS_PROBE = [
    [1, "other"],
    [2, "unknown"],
    [3, "ok"],
    [4, "nonCriticalUpper"],
    [5, "criticalUpper"],
    [6, "nonRecoverableUpper"],
    [7, "nonCriticalLower"],
    [8, "criticalLower"],
    [9, "nonRecoverableLower"],
    [10, "failed"],
]
IDRAC = _verz(
    "IDRAC-MIB-SMIv2",
    {
        "oid": "1.3.6.1.4.1.674.10892.5.4.700.12.1.5",
        "name": "coolingDeviceStatus",
        "kind": "column",
        "syntax": "Integer32",
        "tc_name": "StatusProbeEnum",
        "enum": STATUS_PROBE,
    },
)
NAS = _verz(
    "NAS-MIB",
    {
        "oid": "1.3.6.1.4.1.24681.1.2.5",
        "name": "cpu-Temperature",
        "kind": "scalar",
        "syntax": "OCTET STRING",
        "tc_name": "DisplayString",
    },
    {
        "oid": "1.3.6.1.4.1.24681.1.2.6",
        "name": "systemTemperature",
        "kind": "scalar",
        "syntax": "OCTET STRING",
        "tc_name": "DisplayString",
    },
    {
        "oid": "1.3.6.1.4.1.24681.1.2.11.1.4",
        "name": "hdStatus",
        "kind": "column",
        "syntax": "Integer32",
    },
    {
        "oid": "1.3.6.1.4.1.24681.1.2.11.1.7",
        "name": "hdSmartInfo",
        "kind": "column",
        "syntax": "OCTET STRING",
        "tc_name": "DisplayString",
    },
    {
        "oid": "1.3.6.1.4.1.24681.1.2.17.1.4",
        "name": "sysVolumeTotalSize",
        "kind": "column",
        "syntax": "OCTET STRING",
        "tc_name": "DisplayString",
    },
    {
        "oid": "1.3.6.1.4.1.24681.1.2.17.1.5",
        "name": "sysVolumeFreeSize",
        "kind": "column",
        "syntax": "OCTET STRING",
        "tc_name": "DisplayString",
    },
)
SFOS = _verz(
    "SFOS-FIREWALL-MIB",
    {
        "oid": "1.3.6.1.4.1.2604.5.1.2.2",
        "name": "sfosUpTime",
        "kind": "scalar",
        "syntax": "TimeTicks",
    },
)
SYNO_RAID = _verz(
    "SYNOLOGY-RAID-MIB",
    {
        "oid": "1.3.6.1.4.1.6574.3.1.1.4",
        "name": "raidFreeSize",
        "kind": "column",
        "syntax": "Counter64",
    },
)
CISCOSB = _verz(
    "CISCOSB-rndMng",
    {
        "oid": "1.3.6.1.4.1.9.6.1.101.1.8",
        "name": "rlCpuUtilDuringLastMinute",
        "kind": "scalar",
        "syntax": "Integer32",
    },
    {
        "oid": "1.3.6.1.4.1.9.6.1.101.1.9",
        "name": "rlCpuUtilDuringLast5Minutes",
        "kind": "scalar",
        "syntax": "Integer32",
    },
)


@pytest.fixture(scope="module")
def mibs() -> lint.MibAufloeser:
    return lint.MibAufloeser.mit_standard(
        [NETSWITCH, FORTINET, CISCO_FW, IDRAC, NAS, SFOS, SYNO_RAID, CISCOSB]
    )


OHNE_SHELLCHECK = lint.Werkzeuge(
    bash=shutil.which("bash"),
    sh=shutil.which("dash") or shutil.which("sh"),
    awk=(
        ["mawk", "-W", "dump"]
        if shutil.which("mawk")
        else (["gawk", "--pretty-print=/dev/null"] if shutil.which("gawk") else None)
    ),
    shellcheck=None,
)


def _bundle(checks=(), scripts=(), **profil) -> dict:
    p = {"name": "Testprofil", "vendor": "generic", "category": "server", **profil}
    return {"schema_version": 1, "profile": p, "checks": list(checks), "scripts": list(scripts)}


def _check(name, typ, cfg, **rest) -> dict:
    return {"name": name, "check_type": typ, "check_config": cfg, **rest}


def _funde(bundle, mibs, werkzeuge=OHNE_SHELLCHECK) -> list[lint.Fund]:
    return [
        f for f in lint.pruefe_bundle(bundle, mibs, werkzeuge).funde if f.regel != "werkzeug_fehlt"
    ]


def _regeln(bundle, mibs, schwere=None) -> list[tuple[str, str]]:
    return sorted(
        (f.schwere, f.regel)
        for f in _funde(bundle, mibs)
        if schwere is None or f.schwere == schwere
    )


def _script(name, rumpf, interpreter="bash", sid="s1", **rest) -> dict:
    return {
        "script_id": sid,
        "name": name,
        "interpreter": interpreter,
        "script_body": rumpf,
        **rest,
    }


# --- Anker ------------------------------------------------------------------------------


def test_fingerprint_ist_gepinnt_und_gleich_dem_vesana_anker():
    h = hashlib.sha256()
    h.update((PAKET / "lint.py").read_bytes())
    h.update((PAKET / "standard_mib_types.json").read_bytes())
    assert h.hexdigest() == lint.FINGERPRINT
    assert lint.FINGERPRINT == FINGERPRINT, (
        "lint.py/standard_mib_types.json geändert: Kopie nach Vesana "
        "(tools/checkwahrheit/wahrheit/profile_lint/) und BEIDE Anker-Konstanten nachziehen"
    )


def test_bibliothek_importiert_nur_die_standardbibliothek():
    import ast

    baum = ast.parse((PAKET / "lint.py").read_text(encoding="utf-8"))
    module = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module.update(a.name.split(".")[0] for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            module.add(knoten.module.split(".")[0])
    assert module <= {
        "__future__",
        "hashlib",
        "json",
        "re",
        "shutil",
        "subprocess",
        "collections",
        "dataclasses",
        "pathlib",
        "typing",
    }, module


# --- MIB-Auflösung ------------------------------------------------------------------------


def test_skalar_nur_mit_null_spalte_mit_index(mibs):
    assert mibs.finde("1.3.6.1.2.1.25.1.1.0").name == "hrSystemUptime"
    assert mibs.finde(".1.3.6.1.2.1.25.1.1").name == "hrSystemUptime"
    assert mibs.finde("1.3.6.1.2.1.25.3.3.1.2.196608").name == "hrProcessorLoad"
    # Unter einem Skalar liegt kein Objekt (QNAP „Power Status" walkt …1.2.5.1.5).
    assert mibs.finde("1.3.6.1.4.1.24681.1.2.5.1.5") is None
    assert mibs.finde("1.3.6.1.4.1.99999.1.1.0") is None
    assert mibs.finde("kein-oid") is None


def test_nicht_aufloesbare_oid_ist_nie_ok_sondern_nicht_pruefbar(mibs):
    b = _bundle([_check("Toner", "snmp", {"oid": "1.3.6.1.4.1.1347.43.5.1.1.28.1.1", "unit": "%"})])
    assert _regeln(b, mibs) == [("nicht_pruefbar", "mib_unbekannt")]
    ohne = lint.pruefe_bundle(b, None, OHNE_SHELLCHECK).funde
    assert [(f.schwere, f.regel) for f in ohne] == [("nicht_pruefbar", "mib_fehlt")]


def test_zaehler_mit_gauge_bedeutung_ist_kein_zaehler(mibs):
    assert mibs.finde("1.3.6.1.2.1.2.2.1.14.3").ist_zaehler  # ifInErrors
    assert not mibs.finde("1.3.6.1.4.1.2021.4.27.0").ist_zaehler  # memSysAvail CounterBasedGauge64
    assert not mibs.finde("1.3.6.1.4.1.6574.3.1.1.4.0").ist_zaehler  # Synology raidFreeSize


# --- SNMP ---------------------------------------------------------------------------------


def test_prozent_auf_freien_bytes_ist_fehler_procurve(mibs):
    kaputt = _check(
        "Memory",
        "snmp",
        {"oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.6.1", "unit": "%"},
        threshold_warn=85,
        threshold_crit=95,
    )
    assert _regeln(_bundle([kaputt]), mibs) == [("fehler", "snmp_prozent_auf_bytes")]
    heil = _check(
        "Memory",
        "snmp",
        {
            "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.7.1",
            "total_oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.5.1",
            "unit": "%",
        },
        threshold_warn=85,
        threshold_crit=95,
    )
    assert _regeln(_bundle([heil]), mibs) == []


def test_prozent_auf_mb_ist_fehler_fortigate_und_echte_prozent_bleiben_still(mibs):
    disk = _check(
        "FortiGate — Disk Usage", "snmp", {"oid": "1.3.6.1.4.1.12356.101.4.1.6.0", "unit": "%"}
    )
    assert _regeln(_bundle([disk]), mibs) == [("fehler", "snmp_prozent_auf_bytes")]
    mem = _check(
        "FortiGate — Memory Usage", "snmp", {"oid": "1.3.6.1.4.1.12356.101.4.1.4.0", "unit": "%"}
    )
    assert _regeln(_bundle([mem]), mibs) == []
    mit_total = _check(
        "Disk",
        "snmp",
        {
            "oid": "1.3.6.1.4.1.12356.101.4.1.6.0",
            "total_oid": "1.3.6.1.4.1.12356.101.4.1.7.0",
            "unit": "%",
        },
    )
    assert _regeln(_bundle([mit_total]), mibs) == []


def test_prozent_auf_zaehler_ist_fehler(mibs):
    b = _bundle([_check("Errors %", "snmp", {"oid": "1.3.6.1.2.1.2.2.1.14.2", "unit": "%"})])
    assert ("fehler", "snmp_prozent_auf_zaehler") in _regeln(b, mibs)


def test_integer_konstante_als_snmp_counter_ist_fehler_stp(mibs):
    kaputt = _check(
        "STP Topology Changes (per hour)", "snmp_counter", {"oid": "1.3.6.1.2.1.17.2.1.0"}
    )
    assert _regeln(_bundle([kaputt]), mibs) == [("fehler", "snmp_counter_konstante")]
    heil = _check(
        "STP Topology Changes (per hour)", "snmp_counter", {"oid": "1.3.6.1.2.1.17.2.4.0"}
    )
    assert _regeln(_bundle([heil]), mibs) == []


def test_zaehler_mit_absoluter_grenze_ist_warnung(mibs):
    b = _bundle([_check("IF errors", "snmp", {"oid": "1.3.6.1.2.1.2.2.1.14.1"}, threshold_warn=10)])
    assert _regeln(b, mibs) == [("warnung", "snmp_zaehler_grenze")]
    gauge = _check("Memory", "snmp", {"oid": "1.3.6.1.4.1.2021.4.27.0"}, threshold_warn=10)
    assert _regeln(_bundle([gauge]), mibs) == []


def test_textspalte_als_status_ist_fehler_asa_und_qnap(mibs):
    asa = _check(
        "Failover State",
        "snmp_string",
        {"oid": "1.3.6.1.4.1.9.9.147.1.2.1.1.1.2.6", "value_map": {"9": "OK", "3": "CRITICAL"}},
    )
    assert _regeln(_bundle([asa]), mibs) == [("fehler", "snmp_textspalte_status")]
    asa_heil = _check(
        "Failover State",
        "snmp_string",
        {
            "oid": "1.3.6.1.4.1.9.9.147.1.2.1.1.1.3.6",
            "value_map": {"9": "OK", "10": "OK", "2": "OK", "3": "CRITICAL", "4": "CRITICAL"},
        },
    )
    assert _regeln(_bundle([asa_heil]), mibs) == []
    qnap = _check(
        "QNAP — Disk Status (all)",
        "snmp_table_state",
        {"walk_oid": "1.3.6.1.4.1.24681.1.2.11.1.7", "ok_values": [0], "crit_values": [-5]},
    )
    assert _regeln(_bundle([qnap]), mibs) == [("fehler", "snmp_textspalte_status")]
    text_zuordnung = _check(
        "Smart",
        "snmp_string",
        {"oid": "1.3.6.1.4.1.24681.1.2.11.1.7.1", "value_map": {"GOOD": "OK"}},
    )
    assert _regeln(_bundle([text_zuordnung]), mibs) == []


def test_enum_falsch_zugeordnet_ist_warnung_idrac(mibs):
    kaputt = _check(
        "Dell iDRAC — Fans (all)",
        "snmp_table_state",
        {
            "walk_oid": "1.3.6.1.4.1.674.10892.5.4.700.12.1.5",
            "ok_values": [3],
            "warn_values": [1, 2, 4],
            "crit_values": [5, 6],
        },
    )
    funde = _funde(_bundle([kaputt]), mibs)
    assert [(f.schwere, f.regel) for f in funde] == [("warnung", "snmp_enum")]
    assert "10 failed" in funde[0].text
    heil = _check(
        "Dell iDRAC — Fans (all)",
        "snmp_table_state",
        {
            "walk_oid": "1.3.6.1.4.1.674.10892.5.4.700.12.1.5",
            "ok_values": [3],
            "warn_values": [4, 7],
            "crit_values": [5, 6, 8, 9, 10],
        },
    )
    assert _regeln(_bundle([heil]), mibs) == []
    fremd = _check(
        "Fans",
        "snmp_table_state",
        {
            "walk_oid": "1.3.6.1.4.1.674.10892.5.4.700.12.1.5",
            "ok_values": [3, 42],
            "warn_values": [4, 7],
            "crit_values": [5, 6, 8, 9, 10],
        },
    )
    assert _regeln(_bundle([fremd]), mibs) == [("warnung", "snmp_enum")]


def test_timeticks_ohne_dauerformat_ist_warnung_sophos(mibs):
    kaputt = _check(
        "Uptime", "snmp_oid", {"oid": "1.3.6.1.4.1.2604.5.1.2.2.0", "value_format": "duration_s"}
    )
    assert _regeln(_bundle([kaputt]), mibs) == [("warnung", "snmp_timeticks")]
    for cfg in (
        {"oid": "1.3.6.1.4.1.2604.5.1.2.2.0", "value_format": "duration_ticks"},
        {"oid": "1.3.6.1.2.1.25.1.1.0", "scale": 0.01, "value_format": "duration_s", "unit": "s"},
        {"oid": "1.3.6.1.2.1.25.1.1.0", "unit": "ticks"},
    ):
        assert _regeln(_bundle([_check("Uptime", "snmp", cfg)]), mibs) == [], cfg


def test_einzelkern_auf_hrprocessorload_ist_warnung_mikrotik(mibs):
    kaputt = _check("CPU Usage", "snmp", {"oid": "1.3.6.1.2.1.25.3.3.1.2.1", "unit": "%"})
    assert _regeln(_bundle([kaputt]), mibs) == [("warnung", "snmp_einzelkern")]
    heil = _check(
        "CPU Usage", "snmp", {"walk_oid": "1.3.6.1.2.1.25.3.3.1.2", "aggregate": "avg", "unit": "%"}
    )
    assert _regeln(_bundle([heil]), mibs) == []


def test_text_oids_als_anteil_ist_warnung_qnap_volume(mibs):
    b = _bundle(
        [
            _check(
                "QNAP — Volume Usage (Volume 1, %)",
                "snmp",
                {
                    "oid": "1.3.6.1.4.1.24681.1.2.17.1.5.1",
                    "total_oid": "1.3.6.1.4.1.24681.1.2.17.1.4.1",
                    "invert": True,
                    "unit": "%",
                },
            )
        ]
    )
    assert _regeln(b, mibs) == [("warnung", "snmp_text_als_zahl")]


def test_name_und_oid_widersprechen_sich(mibs):
    sb = _check("CPU Usage (1 min)", "snmp", {"oid": "1.3.6.1.4.1.9.6.1.101.1.9.0", "unit": "%"})
    assert _regeln(_bundle([sb]), mibs) == [("warnung", "snmp_fenster")]
    sb_heil = _check(
        "CPU Usage (1 min)", "snmp", {"oid": "1.3.6.1.4.1.9.6.1.101.1.8.0", "unit": "%"}
    )
    assert _regeln(_bundle([sb_heil]), mibs) == []
    fan = _check("System Fan", "snmp", {"oid": "1.3.6.1.4.1.24681.1.2.6.0"})
    assert _regeln(_bundle([fan]), mibs) == [("warnung", "snmp_name_passt_nicht")]
    sophos = _check("CPU Usage", "snmp_oid", {"oid": "1.3.6.1.4.1.2021.11.10.0", "unit": "%"})
    assert _regeln(_bundle([sophos]), mibs) == [("warnung", "snmp_cpu_teilanteil")]
    gateway = _check(
        "CPU", "snmp", {"oid": "1.3.6.1.4.1.2021.11.11.0", "scale": -1, "offset": 100, "unit": "%"}
    )
    assert _regeln(_bundle([gateway]), mibs) == []


def test_ssl_ohne_tage_in_der_config_ist_hinweis(mibs):
    ohne = _check(
        "SSL Certificate", "ssl_certificate", {"port": 443}, threshold_warn=30, threshold_crit=7
    )
    assert _regeln(_bundle([ohne]), mibs) == [("hinweis", "ssl_tage")]
    mit = _check(
        "SSL Certificate", "ssl_certificate", {"port": 443, "warn_days": 30, "crit_days": 7}
    )
    assert _regeln(_bundle([mit]), mibs) == []


# --- Scripts ------------------------------------------------------------------------------

TRUENAS_KAPUTT = """if [ -n "$VESANA_POOLS" ]; then
  zpool status | awk '/^[[:space:]]+[a-z0-9]/ {s+=$3+$4+$5} END{print s+0}'
else
  zpool status | awk '/^\\\\s+[a-z0-9]/ {s+=$3+$4+$5} END{print s+0}'
fi
"""


def _ssh(name, sid="s1"):
    return _check(name, "ssh_script", {"script_id": sid})


def test_awk_doppelter_backslash_ist_fehler_truenas(mibs):
    b = _bundle([_ssh("ZPool I/O errors")], [_script("ZPool I/O errors", TRUENAS_KAPUTT)])
    assert _regeln(b, mibs) == [("fehler", "awk_doppelter_backslash")]
    heil = TRUENAS_KAPUTT.replace("\\\\s", "[[:space:]]")
    assert _regeln(_bundle([_ssh("x")], [_script("x", heil)]), mibs) == []


@pytest.mark.skipif(OHNE_SHELLCHECK.awk is None, reason="weder mawk noch gawk installiert")
def test_awk_mit_backslash_quote_in_einfachen_anfuehrungszeichen_ist_fehler(mibs):
    """ASA „Failover state" bis v8: '\\"' in einfachen Anführungszeichen."""
    kaputt = (
        "show failover state | awk '$0 ~ /This host/ {print ($3==\\\"Active\\\")?1:0}' || echo 1\n"
    )
    b = _bundle([_ssh("Failover")], [_script("Failover", kaputt)])
    assert _regeln(b, mibs) == [("fehler", "awk_syntax")]
    heil = "show failover state | awk '$0 ~ /This host/ {print ($3==\"Active\")?1:0}' || echo 1\n"
    assert _regeln(_bundle([_ssh("x")], [_script("x", heil)]), mibs) == []


def test_grep_doppelt_escapte_alternative_ist_fehler_qnap(mibs):
    kaputt = "getsysinfo sysfanstatus | grep -c 'fail\\\\|error'\n"
    assert _regeln(_bundle([_ssh("Fans")], [_script("Fans", kaputt)]), mibs) == [
        ("fehler", "grep_doppelter_backslash")
    ]
    for heil in (
        "x | grep -c 'fail\\|error'\n",
        "x | grep -ciE 'fail|error'\n",
        "x | grep -E 'a\\\\|b'\n",
    ):
        assert _regeln(_bundle([_ssh("x")], [_script("x", heil)]), mibs) == [], heil


def test_python_c_mit_backslash_quote_ist_fehler_unifi(mibs):
    kaputt = (
        'curl -s "$URL" | python3 -c \'import sys,json,os; '
        'd=json.load(sys.stdin)[\\"data\\"]; print(len(d))\' || echo 0\n'
    )
    assert _regeln(_bundle([_ssh("Clients")], [_script("Clients", kaputt)]), mibs) == [
        ("fehler", "python_syntax")
    ]
    heil = (
        'curl -s "$URL" | python3 -c '
        "'import sys,json; d=json.load(sys.stdin)[\"data\"]; print(len(d))'\n"
    )
    assert _regeln(_bundle([_ssh("x")], [_script("x", heil)]), mibs) == []


def test_python_heredoc_und_python_script_werden_uebersetzt(mibs):
    heredoc = "python3 - <<'EOF'\nprint('ok'\nEOF\n"
    assert _regeln(_bundle([_ssh("x")], [_script("x", heredoc)]), mibs) == [
        ("fehler", "python_syntax")
    ]
    py = _script("p", "import os\nif True\n    pass\n", interpreter="python")
    assert _regeln(_bundle([_check("p", "agent_script", {"script_id": "s1"})], [py]), mibs) == [
        ("fehler", "python_syntax")
    ]
    py_heil = _script("p", "import os\nprint(os.getcwd())\n", interpreter="python")
    assert (
        _regeln(_bundle([_check("p", "agent_script", {"script_id": "s1"})], [py_heil]), mibs) == []
    )


@pytest.mark.skipif(not shutil.which("bash"), reason="kein bash")
def test_bash_syntaxfehler_ist_fehler(mibs):
    kaputt = "if [ -f /etc/x ]; then\n  echo 1\n"
    assert _regeln(_bundle([_ssh("x")], [_script("x", kaputt)]), mibs) == [
        ("fehler", "shell_syntax")
    ]
    assert _regeln(_bundle([_ssh("x")], [_script("x", "echo 1\n")]), mibs) == []


def test_ohne_shellcheck_wird_es_einmal_als_nicht_pruefbar_gemeldet(mibs):
    b = _bundle([_ssh("x")], [_script("x", "echo 1\n")])
    funde = lint.pruefe_bundle(b, mibs, OHNE_SHELLCHECK).funde
    assert [(f.schwere, f.regel) for f in funde] == [("nicht_pruefbar", "werkzeug_fehlt")]
    ohne_shell = _bundle(
        [_check("ps", "agent_script", {"script_id": "s1"})],
        [_script("ps", "Write-Output 1", interpreter="powershell")],
    )
    assert lint.pruefe_bundle(ohne_shell, mibs, OHNE_SHELLCHECK).funde == []


# --- PowerShell 5.1 -------------------------------------------------------------------------

EXCHANGE_OWA = """# @vesana-param SKIP_CERT_CHECK boolean "Skip cert" default=yes
$skipCert = $env:VESANA_SKIP_CERT_CHECK -eq '1'
$params = @{ Uri = 'https://localhost/owa/healthcheck.htm'; UseBasicParsing = $true }
if ($skipCert) { $params.SkipCertificateCheck = $true }
try { Invoke-WebRequest @params | Out-Null; 'OK' } catch { 'OWA not responding'; exit 2 }
"""


def _ps(rumpf, typ="agent_script"):
    return _bundle(
        [_check("ps", typ, {"script_id": "s1"})], [_script("ps", rumpf, interpreter="powershell")]
    )


def test_powershell7_parameter_unter_51_ist_fehler_exchange(mibs):
    assert _regeln(_ps(EXCHANGE_OWA), mibs) == [("fehler", "powershell_51")]
    mit_weiche = (
        "if ($PSVersionTable.PSVersion.Major -ge 6) "
        "{ Invoke-RestMethod -Uri $u -SkipCertificateCheck }\n"
    )
    assert _regeln(_ps(mit_weiche), mibs) == [("warnung", "powershell_51")]
    variable = "param([switch]$SkipCertificateCheck)\nif ($SkipCertificateCheck) { 'x' }\n"
    assert _regeln(_ps(variable), mibs) == []


@pytest.mark.parametrize(
    "rumpf",
    [
        "$x = $null ?? 'default'\n",
        "$n = $obj?.Name\n",
        "Test-Path C:\\x && Write-Output 1\n",
        "$s = ($a -gt 1) ? 'big' : 'small'\n",
    ],
)
def test_powershell7_syntax_unter_51_ist_fehler(mibs, rumpf):
    assert _regeln(_ps(rumpf), mibs) == [("fehler", "powershell_51")]


@pytest.mark.parametrize(
    "rumpf",
    [
        "Get-Service | ? { $_.Status -eq 'Running' } | Measure-Object\n",
        "$t = 'a ?? b ?. c && d'  # ?? im Kommentar\n",
        '$m = [Math]::Round(1.5)\nWrite-Output "Wert: $m ? ok : nein"\n',
        "<# 5.1-sicher: kein -SkipCertificateCheck #>\nWrite-Output 1\n",
    ],
)
def test_powershell_51_kein_fehlalarm(mibs, rumpf):
    assert _regeln(_ps(rumpf), mibs) == []


def test_powershell_ausserhalb_des_agents_ist_nur_warnung(mibs):
    assert _regeln(_ps("$x = $a ?? 1\n", typ="api_script"), mibs) == [("warnung", "powershell_51")]


# --- Ziel-Shell ------------------------------------------------------------------------------

ASA_VPN = """# @vesana-param SUMMARY_ROW text "Summary row" default=IPsec
ROW="${VESANA_SUMMARY_ROW:-IPsec}"
show vpn-sessiondb summary 2>/dev/null | awk -v pat="$ROW" '$0 ~ pat {print $3+0; exit}' || echo 0
"""


def test_shell_script_auf_geraete_cli_ist_fehler(mibs):
    for profil in (
        {"vendor": "cisco", "category": "firewall", "name": "Cisco ASA Firewall"},
        {"vendor": "fortinet", "category": "firewall", "name": "Fortinet FortiGate Firewall"},
        {"vendor": "hp", "category": "switch", "name": "HP/Aruba ProCurve Switch"},
    ):
        b = _bundle([_ssh("VPN")], [_script("VPN", ASA_VPN)], **profil)
        assert _regeln(b, mibs) == [("fehler", "ziel_shell")], profil
    # Dasselbe Script auf einem Linux-Gerät ist in Ordnung.
    linux = _bundle([_ssh("VPN")], [_script("VPN", ASA_VPN)], vendor="generic", category="server")
    assert _regeln(linux, mibs) == []


def test_reiner_geraetebefehl_ohne_werte_ist_auf_der_cli_erlaubt(mibs):
    b = _bundle(
        [_ssh("Version")],
        [_script("Version", "show version | include Software\n")],
        vendor="cisco",
        category="switch",
    )
    assert _regeln(b, mibs) == []


def test_einstellbare_werte_machen_jedes_cli_script_kaputt(mibs):
    s = _script(
        "Conn", "show conn count\n", parameters=[{"name": "X", "type": "text", "default": "a"}]
    )
    b = _bundle([_ssh("Conn")], [s], vendor="cisco", category="firewall")
    assert _regeln(b, mibs) == [("fehler", "ziel_shell")]


def test_routeros_script_auf_mikrotik(mibs):
    ros = "/ip ipsec installed-sa print count-only\n"
    b = _bundle(
        [_ssh("SAs")],
        [_script("SAs", ros)],
        vendor="mikrotik",
        category="router",
        os_family="linux",
    )
    assert _regeln(b, mibs) == [("nicht_pruefbar", "routeros_script")]
    mit_wert = _script("SAs", '# @vesana-param PEER text "Peer" default=x\n' + ros)
    b2 = _bundle([_ssh("SAs")], [mit_wert], vendor="mikrotik", category="router")
    assert _regeln(b2, mibs) == [("fehler", "ziel_shell")]


# --- Robustheit -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bundle",
    [
        None,
        [],
        {},
        {"checks": "x"},
        {"checks": [None, 1, {"check_config": "x"}]},
        {"scripts": [{"script_body": None}], "checks": []},
    ],
)
def test_kaputte_bundles_werfen_nie(mibs, bundle):
    lint.pruefe_bundle(bundle, mibs, OHNE_SHELLCHECK)


def test_kurztext_deckelt():
    funde = [lint.Fund("fehler", "r", f"C{i}", "t") for i in range(12)]
    text = lint.kurztext(funde, grenze=3)
    assert text.startswith("C0: t; C1: t; C2: t") and text.endswith("… und 9 weitere")
