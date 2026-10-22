"""Profil-Prüfung (Lint) für Hub-Bundles — EINE Regel-Bibliothek für Upload und Nacht.

Hier steht jede Fehlerart, die das Mess-Audit vom 22.10.2026 in Hub-Profilen
gefunden hat, als Regel. Dieselbe Datei läuft an zwei Stellen:

* **Community-Hub** (``vesana-community``, ``app/profile_lint/``): beim
  Hochladen. Ein FEHLER weist den Upload ab, eine WARNUNG wird angezeigt.
* **Vesana** (``tools/checkwahrheit/wahrheit/profile_lint/``, wortgleiche
  Kopie): nachts über alle Hub-Profile (Check-Wahrheit, Hub-Lint) und vor
  jedem Kurator-Push (``tools/hub_rollout/rollout.py``).

🔒 Die Kopie ist BYTE-GLEICH. ``FINGERPRINT`` (sha256 über diese Datei und
``standard_mib_types.json``) steht in beiden Repos als Anker im Test und wird
nachts gegen ``GET /api/v1/profile-lint`` des Hubs verglichen. Wer eine Regel
ändert, ändert sie im Hub-Repo und kopiert beide Dateien nach Vesana.

Schweregrade:

* ``fehler`` — sicherer Defekt: Syntaxfehler, Wert garantiert 0 bzw. immer
  gleich, Prozent auf einer Byte- oder Zählergröße. Blockiert den Upload.
* ``warnung`` — sehr wahrscheinlich falsch, aber nicht in jedem Fall.
* ``hinweis`` — wirkungslos oder Vorgabe greift statt Absicht.
* ``nicht_pruefbar`` — z. B. OID ohne auflösbare MIB. Nie stillschweigend „ok".

Reine Bibliothek: nur Standardbibliothek, keine Datenbank, kein Netz. Die
MIB-Verzeichnisse reicht der Aufrufer herein (Hub: Tabelle ``mib_modules``;
Vesana: Hub-API). Externe Prüfer (``bash -n``, ``sh -n``, awk, shellcheck)
übersetzen nur, sie führen nichts aus.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LINT_VERSION = "2026.10.22"

FEHLER = "fehler"
WARNUNG = "warnung"
HINWEIS = "hinweis"
NICHT_PRUEFBAR = "nicht_pruefbar"
SCHWEREGRADE = (FEHLER, WARNUNG, HINWEIS, NICHT_PRUEFBAR)

_HIER = Path(__file__).resolve().parent
STANDARD_DATEI = _HIER / "standard_mib_types.json"

#: Zeitgrenze je externem Prüfer-Aufruf (Sekunden).
PRUEFER_TIMEOUT = 10


def _fingerprint() -> str:
    h = hashlib.sha256()
    for datei in (Path(__file__).resolve(), STANDARD_DATEI):
        try:
            h.update(datei.read_bytes())
        except OSError:
            h.update(b"-")
    return h.hexdigest()


FINGERPRINT = _fingerprint()


@dataclass(frozen=True)
class Fund:
    schwere: str
    regel: str
    check: str
    text: str

    def as_dict(self) -> dict[str, str]:
        return {
            "schwere": self.schwere,
            "regel": self.regel,
            "check": self.check,
            "text": self.text,
        }


@dataclass
class Ergebnis:
    funde: list[Fund] = field(default_factory=list)
    werkzeuge: dict[str, bool] = field(default_factory=dict)

    def von(self, schwere: str) -> list[Fund]:
        return [f for f in self.funde if f.schwere == schwere]

    @property
    def fehler(self) -> list[Fund]:
        return self.von(FEHLER)

    @property
    def warnungen(self) -> list[Fund]:
        return self.von(WARNUNG)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lint_version": LINT_VERSION,
            "fingerprint": FINGERPRINT,
            "werkzeuge": dict(self.werkzeuge),
            "funde": [f.as_dict() for f in self.funde],
        }


# --- MIB-Typen -----------------------------------------------------------------


@dataclass(frozen=True)
class MibObjekt:
    oid: str
    name: str
    modul: str
    kind: str
    syntax: str | None
    tc_name: str | None
    units: str | None
    enum: tuple[tuple[int, str], ...] | None

    @property
    def ist_text(self) -> bool:
        return self.syntax == "OCTET STRING" and not self.enum

    @property
    def ist_zaehler(self) -> bool:
        """Echter Zähler seit Boot. Counter64 mit Gauge-Bedeutung zählt nicht:
        UCD ``CounterBasedGauge64`` (memSysAvail) und Hersteller, die Größen als
        Counter64 führen (Synology raidFreeSize)."""
        if self.syntax not in ("Counter32", "Counter64") or self.tc_name in _GAUGE_TCS:
            return False
        return not _GROESSEN_NAME.search(self.name)


#: Einheiten, die eine MIB nur im DESCRIPTION-Text nennt (der Hub liefert
#: Verzeichnisse ohne Beschreibung aus). Je Eintrag die Fundstelle.
EINHEITEN_NACHTRAG: dict[str, str] = {
    # FORTINET-FORTIGATE-MIB fgSysDiskUsage: "Current hard disk usage (MB)"
    "1.3.6.1.4.1.12356.101.4.1.6": "MB",
    # FORTINET-FORTIGATE-MIB fgSysDiskCapacity: "Total hard disk capacity (MB)"
    "1.3.6.1.4.1.12356.101.4.1.7": "MB",
    # FORTINET-FORTIGATE-MIB fgSysMemCapacity: "Total physical memory (RAM) installed (KB)"
    "1.3.6.1.4.1.12356.101.4.1.5": "KB",
    # HOST-RESOURCES-MIB hrStorageSize/hrStorageUsed: "in units of hrStorageAllocationUnits"
    "1.3.6.1.2.1.25.2.3.1.5": "allocation units",
    "1.3.6.1.2.1.25.2.3.1.6": "allocation units",
}

_BYTE_EINHEIT = re.compile(
    r"^(bytes?|octets?|[kmgt]i?b(ytes)?|kilo(bytes)?|mega(bytes)?|allocation units)$", re.I
)
_BYTE_NAME = re.compile(r"(Bytes|Octets|KBytes|MBytes|Kbytes|Mbytes)(?![a-z])")
_TIMETICKS_TCS = ("TimeTicks", "TimeStamp", "TimeInterval")
_GAUGE_TCS = ("CounterBasedGauge64", "Gauge64")
_GROESSEN_NAME = re.compile(
    r"(Size|Free|Total|Avail|Used|Capacity|Space|Usage|Level|Percent)", re.I
)


class MibAufloeser:
    """OID (auch mit Instanz-Index) → MIB-Objekt, über die längste bekannte Präfix-OID."""

    def __init__(self) -> None:
        self._objekte: dict[str, MibObjekt] = {}

    def __len__(self) -> int:
        return len(self._objekte)

    def objekt_hinzu(self, o: dict[str, Any], modul: str) -> None:
        if o.get("kind") not in ("scalar", "column"):
            return
        oid = _norm_oid(o.get("oid"))
        if not oid or oid in self._objekte:
            return
        enum = o.get("enum")
        self._objekte[oid] = MibObjekt(
            oid=oid,
            name=str(o.get("name") or ""),
            modul=modul,
            kind=str(o.get("kind")),
            syntax=o.get("syntax"),
            tc_name=o.get("tc_name"),
            units=EINHEITEN_NACHTRAG.get(oid) or o.get("units"),
            enum=tuple((int(k), str(v)) for k, v in enum) if enum else None,
        )

    def verzeichnis_hinzu(self, verzeichnis: dict[str, Any]) -> None:
        """Ein Objekt-Verzeichnis (Format 1, wie Hub-Tabelle ``mib_modules.directory``)."""
        modul = str(verzeichnis.get("module") or "")
        for o in verzeichnis.get("objects") or []:
            if isinstance(o, dict):
                self.objekt_hinzu(o, modul)

    def standard_laden(self, datei: Path | None = None) -> None:
        """Die mitgelieferten Standard-MIBs (HOST-RESOURCES, IF, UCD, BRIDGE, UPS, Printer …)."""
        daten = json.loads((datei or STANDARD_DATEI).read_text(encoding="utf-8"))
        for modul, objekte in daten["module"].items():
            for oid, name, kind, syntax, tc_name, units, enum in objekte:
                self.objekt_hinzu(
                    {
                        "kind": kind,
                        "oid": oid,
                        "name": name,
                        "syntax": syntax,
                        "tc_name": tc_name,
                        "units": units,
                        "enum": enum,
                    },
                    modul,
                )

    @classmethod
    def mit_standard(cls, verzeichnisse: Iterable[dict[str, Any]] = ()) -> MibAufloeser:
        a = cls()
        for v in verzeichnisse:
            a.verzeichnis_hinzu(v)
        a.standard_laden()
        return a

    def finde(self, oid: Any) -> MibObjekt | None:
        """Spalte: mit beliebigem Index. Skalar: nur genau oder mit ``.0`` —
        eine OID UNTER einem Skalar ist kein Objekt dieser MIB."""
        voll = _norm_oid(oid)
        o = voll
        while o:
            treffer = self._objekte.get(o)
            if treffer is not None:
                if treffer.kind == "scalar" and voll not in (o, o + ".0"):
                    return None
                return treffer
            if "." not in o:
                return None
            o = o.rsplit(".", 1)[0]
        return None


def _norm_oid(oid: Any) -> str:
    s = str(oid or "").strip().strip(".")
    return s if re.fullmatch(r"[0-9]+(\.[0-9]+)*", s) else ""


# --- Externe Prüfer ------------------------------------------------------------


@dataclass
class Werkzeuge:
    bash: str | None = None
    sh: str | None = None
    awk: list[str] | None = None
    shellcheck: str | None = None

    @classmethod
    def finden(cls) -> Werkzeuge:
        awk: list[str] | None = None
        if shutil.which("mawk"):
            awk = ["mawk", "-W", "dump"]
        elif shutil.which("gawk"):
            awk = ["gawk", "--pretty-print=/dev/null"]
        return cls(
            bash=shutil.which("bash"),
            sh=shutil.which("dash") or shutil.which("sh"),
            awk=awk,
            shellcheck=shutil.which("shellcheck"),
        )

    def als_dict(self) -> dict[str, bool]:
        return {
            "bash": bool(self.bash),
            "sh": bool(self.sh),
            "awk": bool(self.awk),
            "shellcheck": bool(self.shellcheck),
        }


def _lauf(aufruf: list[str], eingabe: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            aufruf,
            input=eingabe,
            stdin=None if eingabe is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=PRUEFER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return -1, "Zeitgrenze"
    except OSError as e:
        return -2, str(e)
    return p.returncode, (p.stderr or p.stdout or "").strip()


def _erste_zeile(text: str, laenge: int = 200) -> str:
    zeile = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return re.sub(r"^(bash|sh|dash|-):\s*(line\s*)?", "Zeile ", zeile)[:laenge]


# --- Shell-Text zerlegen -------------------------------------------------------


@dataclass
class Abschnitt:
    start: int
    ende: int
    text: str


_TRENNER = re.compile(r"[;|&\n(`]|\$\(")
_GEQUOTET = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def einfach_gequotete_abschnitte(befehl: str) -> list[Abschnitt]:
    """Alle '…'-Abschnitte, die die Shell als einfach gequotet liest (nicht in "…",
    nicht hinter \\ oder #). ``$(…)`` öffnet auch in "…" einen neuen Kontext.
    (Gleiche Zerlegung wie ``tools/hub_rollout/awk_quoting.py`` im Vesana-Repo.)"""
    raus: list[Abschnitt] = []
    i, n = 0, len(befehl)
    stapel: list[list[Any]] = [["n", 0]]
    while i < n:
        art = stapel[-1][0]
        c = befehl[i]
        if c == "\\":
            i += 2
            continue
        if befehl.startswith("$(", i):
            stapel.append(["c", 0])
            i += 2
            continue
        if art == "d":
            if c == '"':
                stapel.pop()
            i += 1
            continue
        if c == '"':
            stapel.append(["d", 0])
            i += 1
            continue
        if art == "c" and c == "(":
            stapel[-1][1] += 1
        elif art == "c" and c == ")":
            if stapel[-1][1] == 0:
                stapel.pop()
            else:
                stapel[-1][1] -= 1
        elif c == "#" and (i == 0 or befehl[i - 1] in " \t\n;"):
            nl = befehl.find("\n", i)
            i = n if nl < 0 else nl
            continue
        elif c == "'":
            ende = befehl.find("'", i + 1)
            if ende < 0:
                break
            raus.append(Abschnitt(i + 1, ende, befehl[i + 1 : ende]))
            i = ende + 1
            continue
        i += 1
    return raus


def _befehl_davor(befehl: str, abschnitt: Abschnitt) -> str:
    """Der Text des einfachen Befehls vor dem Abschnitt (gequotete Werte und
    abgeschlossene ``$(…)`` maskiert, damit ihre Trenner nicht zählen)."""
    davor = befehl[: abschnitt.start - 1]
    while True:
        maskiert = re.sub(r"\$\([^()]*\)", lambda m: "x" * len(m.group(0)), davor)
        if maskiert == davor:
            break
        davor = maskiert
    davor = _GEQUOTET.sub(lambda m: "x" * len(m.group(0)), davor)
    letzte = 0
    for m in _TRENNER.finditer(davor):
        letzte = m.end()
    return davor[letzte:]


_AWK_WORT = re.compile(r"(?:^|[\s;|&(`])(?:[\w./-]*/)?(?:busybox\s+)?[gm]?awk(?:\s|$)")


def ist_awk_programm(befehl: str, abschnitt: Abschnitt) -> bool:
    """Ist der Abschnitt das PROGRAMM eines awk-Aufrufs — nicht der Wert von -F/-v/-f?"""
    davor = _befehl_davor(befehl, abschnitt)
    alle = list(_AWK_WORT.finditer(davor))
    if not alle:
        return False
    treffer = alle[-1]
    worte = davor[treffer.end() :].split()
    if davor and not davor[-1].isspace() and worte and worte[-1].startswith("-"):
        return False
    if davor and not davor[-1].isspace() and len(worte) >= 2 and worte[-2] == "-v":
        return False
    return not (worte and worte[-1] in ("-F", "-v", "-f"))


_PY_C = re.compile(r"(?:^|\s)(?:[\w./-]*/)?python3?(?:\s+-[A-Za-z]+)*\s+-c\s*$")
_GREP = re.compile(r"(?:^|\s)(?:[\w./-]*/)?([ef]?grep)((?:\s+-[A-Za-z]+)*)(?:\s+-e)?\s*$")


def _python_c_programm(befehl: str, abschnitt: Abschnitt) -> bool:
    return bool(_PY_C.search(_befehl_davor(befehl, abschnitt)))


def _grep_bre_muster(befehl: str, abschnitt: Abschnitt) -> bool:
    """Muster eines grep OHNE -E/-P/-F (Basic Regular Expression)?"""
    m = _GREP.search(_befehl_davor(befehl, abschnitt))
    if not m or m.group(1) != "grep":
        return False
    flags = "".join(f.lstrip("-") for f in m.group(2).split())
    return not any(f in flags for f in "EPF")


_PY_HEREDOC = re.compile(
    r"python3?(?:\s+-[A-Za-z]*)*\s+(?:-\s+)?<<-?\s*'(\w+)'[^\n]*\n(.*?)\n\s*\1\s*(?:\n|$)", re.S
)


def _ohne_kommentarzeilen(text: str) -> str:
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


# --- Ziel der SSH-Scripts --------------------------------------------------------


def geraete_cli(profil: dict[str, Any]) -> str | None:
    """Gerät mit eigener CLI statt POSIX-Shell — dort gibt es kein bash/sh/awk.

    Erkannt am Profil (Hersteller + Kategorie), nicht am ``os_family``-Feld:
    MikroTik-Profile tragen dort teils „linux", RouterOS hat trotzdem keine Shell.
    """
    hersteller = str(profil.get("vendor") or "").lower()
    kategorie = str(profil.get("category") or "").lower()
    name = str(profil.get("name") or "").lower()
    if "mikrotik" in hersteller or "mikrotik" in name or "routeros" in name:
        return "RouterOS"
    if "fortinet" in hersteller or "fortigate" in name:
        return "FortiOS"
    if hersteller == "cisco" and kategorie in ("firewall", "switch", "router", "wlan"):
        return "Cisco ASA/IOS"
    if hersteller in ("hp", "hpe", "aruba") and kategorie == "switch":
        return "HP ProVision/ArubaOS"
    return None


#: Shell-Konstrukte, die keine Geräte-CLI ausführt (Pipes in Gerätebefehle wie
#: ``| include``/``| grep`` sind dort erlaubt und fehlen hier bewusst).
_SHELL_KONSTRUKTE = (
    (re.compile(r"\$\("), "$(…)"),
    (re.compile(r"`"), "Backticks"),
    (re.compile(r"\$\{?[A-Za-z_]"), "Shell-Variable"),
    (re.compile(r"(?m)^\s*[A-Za-z_][A-Za-z0-9_]*="), "Zuweisung"),
    (re.compile(r"\|\||&&"), "|| / &&"),
    (re.compile(r"\d?>\s*/dev/null|2>&1"), "Umleitung"),
    (
        re.compile(
            r"\|\s*(awk|sed|wc|head|tail|cut|tr|sort|uniq|xargs|python3?|perl|bash|sh|jq)\b"
        ),
        "Pipe in ein Unix-Werkzeug",
    ),
    (re.compile(r"(?m)^\s*(if|then|fi|for|while|do|done|case|esac)\b"), "Shell-Kontrollstruktur"),
    (re.compile(r"(?m)(^|\s)\[\s"), "[ … ]"),
)


def _routeros_script(text: str) -> bool:
    zeilen = [z.strip() for z in _ohne_kommentarzeilen(text).splitlines() if z.strip()]
    return bool(zeilen) and all(z[0] in "/:{}" for z in zeilen)


def _hat_werte(script: dict[str, Any], checks: list[dict[str, Any]]) -> bool:
    """Gehen beim Lauf ``VESANA_*``-Werte mit? Dann verpackt der Server den Rumpf
    in ``env … sh -c '…'`` (shared/script_wrap.py::build_ssh_command)."""
    params = script.get("parameters")
    if isinstance(params, list):
        for p in params:
            if isinstance(p, dict) and str(p.get("default") or "").strip():
                return True
    if not isinstance(params, list):
        for zeile in str(script.get("script_body") or "").splitlines():
            if "@vesana-param" in zeile and re.search(r"\bdefault=\S", zeile):
                return True
    for c in checks:
        werte = (c.get("check_config") or {}).get("script_params")
        if isinstance(werte, dict) and any(str(v).strip() for v in werte.values()):
            return True
    return False


# --- Script-Regeln -------------------------------------------------------------


def _shell_syntax(rumpf: str, interpreter: str, w: Werkzeuge) -> str | None | bool:
    """Fehlertext / None (ok) / False (kein Prüfer)."""
    if interpreter == "sh" and w.sh:
        aufruf = [w.sh, "-n"]
    elif w.bash:
        aufruf = [w.bash, "-n"]
    else:
        return False
    code, text = _lauf(aufruf, rumpf)
    return None if code == 0 else (_erste_zeile(text) or f"Exit-Code {code}")


def _shellcheck(rumpf: str, interpreter: str, w: Werkzeuge) -> list[str]:
    if not w.shellcheck:
        return []
    try:
        p = subprocess.run(
            [
                w.shellcheck,
                "-s",
                "sh" if interpreter == "sh" else "bash",
                "-f",
                "json1",
                "-S",
                "warning",
                "-",
            ],
            input=rumpf,
            capture_output=True,
            text=True,
            timeout=PRUEFER_TIMEOUT,
        )
        daten = json.loads(p.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    raus = []
    for k in (daten.get("comments") or [])[:5]:
        raus.append(f"SC{k.get('code')} Zeile {k.get('line')}: {str(k.get('message'))[:160]}")
    return raus


def _awk_regeln(rumpf: str, w: Werkzeuge) -> list[tuple[str, str, str]]:
    """(schwere, regel, text) je awk-Programm."""
    raus: list[tuple[str, str, str]] = []
    for a in einfach_gequotete_abschnitte(rumpf):
        if not ist_awk_programm(rumpf, a):
            continue
        prog = a.text
        kurz = prog.strip().replace("\n", " ")[:80]
        if w.awk:
            code, text = _lauf([*w.awk, prog])
            if code not in (0, -1, -2):
                raus.append(
                    (
                        FEHLER,
                        "awk_syntax",
                        f"awk-Programm übersetzt nicht ({_erste_zeile(text, 120)}): {kurz}",
                    )
                )
                continue
        for regex in re.findall(r"(?<![\w)\]])/((?:[^/\\\n]|\\.)+)/", prog):
            if re.search(r"\\\\[sSdDwW]", regex):
                raus.append(
                    (
                        FEHLER,
                        "awk_doppelter_backslash",
                        f"awk-Regex /{regex[:60]}/ enthält \\\\s — sucht einen wörtlichen "
                        "Backslash, trifft nie (gemeint: [[:space:]])",
                    )
                )
            elif re.search(r"(?<!\\)\\[sSdDwW]", regex):
                raus.append(
                    (
                        WARNUNG,
                        "awk_gawk_klasse",
                        f"awk-Regex /{regex[:60]}/ nutzt \\s/\\d/\\w — nur gawk kennt das, "
                        "mawk/busybox nicht ([[:space:]] verwenden)",
                    )
                )
    return raus


def _grep_regeln(rumpf: str) -> list[tuple[str, str, str]]:
    raus = []
    for a in einfach_gequotete_abschnitte(rumpf):
        if "\\\\|" in a.text and _grep_bre_muster(rumpf, a):
            raus.append(
                (
                    FEHLER,
                    "grep_doppelter_backslash",
                    f"grep '{a.text[:60]}' — \\\\| in einfachen Anführungszeichen sucht "
                    "den Text „\\|“, die Alternative greift nie "
                    "(gemeint: 'a\\|b' oder grep -E 'a|b')",
                )
            )
    return raus


def _python_kompiliert(code: str, wo: str) -> str | None:
    try:
        compile(code, wo, "exec", dont_inherit=True)
    except SyntaxError as e:
        return f"Zeile {e.lineno}: {e.msg}"
    except (ValueError, RecursionError, MemoryError) as e:
        return str(e)[:160]
    return None


def _python_in_shell(rumpf: str) -> list[tuple[str, str, str]]:
    raus = []
    for a in einfach_gequotete_abschnitte(rumpf):
        if _python_c_programm(rumpf, a):
            fehler = _python_kompiliert(a.text, "python -c")
            if fehler:
                raus.append(
                    (
                        FEHLER,
                        "python_syntax",
                        f"python3 -c '…' übersetzt nicht ({fehler}): {a.text[:80]}",
                    )
                )
    for m in _PY_HEREDOC.finditer(rumpf):
        fehler = _python_kompiliert(m.group(2), "python heredoc")
        if fehler:
            raus.append(
                (FEHLER, "python_syntax", f"Python im Here-Document übersetzt nicht ({fehler})")
            )
    return raus


def _ps_entkernen(text: str) -> str:
    """PowerShell-Code ohne Kommentare und String-Inhalte (Zeilen bleiben erhalten)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        zwei = text[i : i + 2]
        if zwei in ("@'", '@"') and (i + 2 < n and text[i + 2] in "\r\n"):
            ende = text.find("\n" + zwei[1] + "@", i + 2)
            stueck = text[i : (n if ende < 0 else ende + 3)]
            out.append("''" + "\n" * stueck.count("\n"))
            i = n if ende < 0 else ende + 3
            continue
        if zwei == "<#":
            ende = text.find("#>", i + 2)
            stueck = text[i : (n if ende < 0 else ende + 2)]
            out.append("\n" * stueck.count("\n"))
            i = n if ende < 0 else ende + 2
            continue
        if c == "#":
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j + 1 : j + 2] == "'":
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            out.append("''" + "\n" * text[i:j].count("\n"))
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "`" else 1
            out.append("''" + "\n" * text[i:j].count("\n"))
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


#: Nur ab PowerShell 6/7 vorhanden — der Windows-Agent startet powershell.exe 5.1.
_PS7_PARAMETER = (
    "SkipCertificateCheck",
    "SkipHttpErrorCheck",
    "StatusCodeVariable",
    "ResponseHeadersVariable",
    "AsHashtable",
    "NoProxy",
)
_PS7_SYNTAX = (
    (re.compile(r"\?\?"), "??-Operator (PowerShell 7)"),
    (re.compile(r"[\w)\]}]\?\."), "?.-Operator (PowerShell 7)"),
    (re.compile(r"&&|\|\|"), "&&/||-Verkettung (PowerShell 7)"),
)
_PS_TERNAER = re.compile(r"(?<=[\w)\]'$])\s+\?\s+(?!\{)[^\n]*?\s:\s")


def _powershell_regeln(rumpf: str, ziel_51: bool) -> list[tuple[str, str, str]]:
    code = _ps_entkernen(rumpf)
    schwere = FEHLER if ziel_51 else WARNUNG
    raus = []
    for regex, was in _PS7_SYNTAX:
        m = regex.search(code)
        if m:
            zeile = code.count("\n", 0, m.start()) + 1
            raus.append(
                (
                    schwere,
                    "powershell_51",
                    f"Zeile {zeile}: {was} — Windows PowerShell 5.1 bricht beim Parsen ab",
                )
            )
    m = _PS_TERNAER.search(code)
    if m:
        zeile = code.count("\n", 0, m.start()) + 1
        raus.append(
            (
                schwere,
                "powershell_51",
                f"Zeile {zeile}: ternärer Operator (PowerShell 7) — 5.1 bricht beim Parsen ab",
            )
        )
    versionsweiche = re.search(r"PSVersionTable|PSEdition", code, re.I)
    for p in _PS7_PARAMETER:
        # -Param, aber auch als Splat-Schlüssel ($params.Param = …, @{ Param = … });
        # eine Variable $Param ist kein Parameter.
        m = re.search(rf"(?i)(?<![\w$]){p}\b", code)
        if m:
            zeile = code.count("\n", 0, m.start()) + 1
            raus.append(
                (
                    WARNUNG if versionsweiche else schwere,
                    "powershell_51",
                    f"Zeile {zeile}: -{p} gibt es erst ab PowerShell 6/7 — "
                    "unter 5.1 Parameterfehler",
                )
            )
    return raus


def _script_regeln(
    script: dict[str, Any],
    checks: list[dict[str, Any]],
    profil: dict[str, Any],
    w: Werkzeuge,
) -> list[Fund]:
    funde: list[Fund] = []
    rumpf = str(script.get("script_body") or "")
    interpreter = str(script.get("interpreter") or "").strip().lower() or "bash"
    if interpreter == "shell":
        interpreter = "sh"
    name = str(script.get("name") or script.get("script_id") or "?")
    ort = f"Script {name}"
    if not rumpf.strip():
        return funde
    typen = {str(c.get("check_type") or "") for c in checks}
    cli = geraete_cli(profil) if "ssh_script" in typen else None

    def neu(schwere: str, regel: str, text: str) -> None:
        funde.append(Fund(schwere, regel, ort, text))

    if interpreter in ("python", "python3"):
        fehler = _python_kompiliert(rumpf, name)
        if fehler:
            neu(FEHLER, "python_syntax", f"Python übersetzt nicht ({fehler})")
        return funde

    if interpreter in ("powershell", "pwsh"):
        for schwere, regel, text in _powershell_regeln(rumpf, "agent_script" in typen):
            neu(schwere, regel, text)
        return funde

    if interpreter not in ("bash", "sh"):
        neu(
            NICHT_PRUEFBAR,
            "interpreter_unbekannt",
            f"Interpreter {interpreter!r} — Rumpf nicht prüfbar",
        )
        return funde

    if cli:
        ssh_checks = [c for c in checks if c.get("check_type") == "ssh_script"]
        if cli == "RouterOS" and _routeros_script(rumpf):
            if _hat_werte(script, ssh_checks):
                neu(
                    FEHLER,
                    "ziel_shell",
                    "RouterOS hat keine Shell — mit einstellbaren Werten geht der Rumpf als "
                    "`env … sh -c` ans Gerät und läuft nie",
                )
            else:
                neu(NICHT_PRUEFBAR, "routeros_script", "RouterOS-Script — Syntax nicht prüfbar")
            return funde
        gefunden = [
            was for regex, was in _SHELL_KONSTRUKTE if regex.search(_ohne_kommentarzeilen(rumpf))
        ]
        if gefunden or _hat_werte(script, ssh_checks):
            grund = ", ".join(gefunden[:4]) if gefunden else "einstellbare Werte (env … sh -c)"
            neu(
                FEHLER,
                "ziel_shell",
                f"ssh_script auf {cli}: die Geräte-CLI hat kein bash/sh — {grund} läuft dort nie",
            )
            return funde

    syntax = _shell_syntax(rumpf, interpreter, w)
    if syntax is False:
        neu(NICHT_PRUEFBAR, "werkzeug_fehlt", "kein bash/sh zum Prüfen vorhanden")
    elif syntax:
        neu(FEHLER, "shell_syntax", f"{interpreter} -n: {syntax}")
    for schwere, regel, text in [
        *_awk_regeln(rumpf, w),
        *_grep_regeln(rumpf),
        *_python_in_shell(rumpf),
    ]:
        neu(schwere, regel, text)
    for text in _shellcheck(rumpf, interpreter, w):
        neu(WARNUNG, "shellcheck", text)
    return funde


# --- SNMP-Regeln ---------------------------------------------------------------

_NUMERISCH = ("snmp", "snmp_oid")
_STATUS_SCHLUESSEL = ("ok_values", "warn_values", "crit_values")
_KRITISCH_KLINGT = re.compile(
    r"(fail|crit|error|down|nonrecoverable|bad|fault|alarm|degraded|lost|broken|dead)", re.I
)


def _einheit(c: dict[str, Any], cfg: dict[str, Any]) -> str:
    return str(cfg.get("unit") or c.get("unit") or "").strip()


def _hat_umrechnung(cfg: dict[str, Any]) -> bool:
    s = cfg.get("scale")
    if s not in (None, "", 0, "0", 1, "1", 1.0):
        return True
    return cfg.get("offset") not in (None, "", 0, "0")


def _status_werte(cfg: dict[str, Any]) -> set[str]:
    werte: set[str] = set()
    for k in _STATUS_SCHLUESSEL:
        v = cfg.get(k)
        if isinstance(v, list):
            werte.update(str(x).strip() for x in v)
    vm = cfg.get("value_map")
    if isinstance(vm, dict):
        werte.update(str(k).strip() for k in vm)
    return {w for w in werte if w}


def _ist_status_check(c: dict[str, Any], cfg: dict[str, Any]) -> bool:
    typ = c.get("check_type")
    if typ in ("snmp_table_state", "snmp_string"):
        return bool(_status_werte(cfg))
    return typ == "snmp_oid" and cfg.get("value_type") == "status" and bool(_status_werte(cfg))


def _byte_groesse(o: MibObjekt) -> str | None:
    if o.units and _BYTE_EINHEIT.match(o.units.strip()):
        return o.units
    if _BYTE_NAME.search(o.name):
        return "Bytes"
    return None


def _snmp_regeln(c: dict[str, Any], mibs: MibAufloeser | None) -> list[Fund]:
    funde: list[Fund] = []
    name = str(c.get("name") or "?")
    typ = str(c.get("check_type") or "")
    cfg = c.get("check_config") or {}
    if not isinstance(cfg, dict):
        return funde

    def neu(schwere: str, regel: str, text: str) -> None:
        funde.append(Fund(schwere, regel, name, text))

    oid = cfg.get("oid") or cfg.get("walk_oid")
    if typ not in (*_NUMERISCH, "snmp_counter", "snmp_string", "snmp_table_state") or not oid:
        return funde
    if mibs is None:
        neu(NICHT_PRUEFBAR, "mib_fehlt", "keine MIB-Typen geladen")
        return funde
    o = mibs.finde(oid)
    status = _ist_status_check(c, cfg)
    walk = bool(cfg.get("walk_oid")) and not cfg.get("oid")

    if _norm_oid(oid).startswith("1.3.6.1.2.1.25.3.3.1.2.") and not walk:
        neu(
            WARNUNG,
            "snmp_einzelkern",
            "hrProcessorLoad mit festem Index = EIN Kern; die Geräte-UI zeigt das Mittel "
            "(walk_oid 1.3.6.1.2.1.25.3.3.1.2 + aggregate avg)",
        )

    if o is None:
        neu(
            NICHT_PRUEFBAR,
            "mib_unbekannt",
            f"OID {_norm_oid(oid) or oid} in keiner verfügbaren MIB",
        )
        return funde
    wer = f"{o.modul}::{o.name}"
    einheit = _einheit(c, cfg)
    total = cfg.get("total_oid") or cfg.get("total_walk_oid")

    if typ in _NUMERISCH and not status:
        if einheit == "%" and not total and not _hat_umrechnung(cfg):
            groesse = _byte_groesse(o)
            if groesse:
                neu(
                    FEHLER,
                    "snmp_prozent_auf_bytes",
                    f"Einheit % auf {wer} ({groesse}) ohne total_oid — "
                    "Rohwert ist keine Prozentzahl",
                )
            elif o.ist_zaehler:
                neu(
                    FEHLER,
                    "snmp_prozent_auf_zaehler",
                    f"Einheit % auf {wer} ({o.syntax}) — "
                    "ein Zähler seit Boot ist keine Prozentzahl",
                )
            elif o.ist_text:
                neu(
                    WARNUNG,
                    "snmp_text_als_zahl",
                    f"Einheit % auf Text-OID {wer} — nur die erste Zahl im Text zählt",
                )
        if total:
            t = mibs.finde(total)
            if o.ist_text or (t is not None and t.ist_text):
                neu(
                    WARNUNG,
                    "snmp_text_als_zahl",
                    f"Anteil aus Text-OIDs ({wer}) — eine Einheit im Text (GB/TB) fällt weg, "
                    "ungleiche Einheiten ergeben Unsinn",
                )
        grenze = c.get("threshold_warn") is not None or c.get("threshold_crit") is not None
        if o.ist_zaehler and grenze and str(cfg.get("aggregate") or "") not in ("rate", "delta"):
            neu(
                WARNUNG,
                "snmp_zaehler_grenze",
                f"{wer} ist ein Zähler seit Boot ({o.syntax}) — absolute Grenze wird "
                "irgendwann dauerhaft überschritten (snmp_counter)",
            )
        if (o.syntax == "TimeTicks" or o.tc_name in _TIMETICKS_TCS) and not total:
            vf = str(cfg.get("value_format") or "")
            if einheit == "ticks" or vf == "duration_ticks":
                pass
            elif not _hat_umrechnung(cfg):
                text = (
                    f"{wer} liefert TimeTicks (1/100 s), duration_s ohne scale 0.01 "
                    "zeigt 100× zu viel"
                    if vf == "duration_s"
                    else f"{wer} liefert TimeTicks (1/100 s) ohne Dauer-Format "
                    "(value_format duration_ticks)"
                )
                neu(WARNUNG, "snmp_timeticks", text)

    funde.extend(_name_regeln(name, wer, o, oid))

    if typ == "snmp_counter" and not o.ist_zaehler:
        if o.enum:
            neu(
                FEHLER,
                "snmp_counter_konstante",
                f"snmp_counter auf {wer} (Aufzählung, kein Zähler) — die Rate ist immer 0",
            )
        else:
            neu(
                WARNUNG,
                "snmp_counter_kein_zaehler",
                f"snmp_counter auf {wer} ({o.syntax}) — kein Zähler, die Rate ist bedeutungslos",
            )

    if status:
        werte = _status_werte(cfg)
        zahlen = all(re.fullmatch(r"-?\d+", w) for w in werte)
        if o.ist_text and (typ == "snmp_table_state" or zahlen):
            neu(
                FEHLER,
                "snmp_textspalte_status",
                f"{wer} ist eine Textspalte — "
                + (
                    "snmp_table_state wertet nur Zahlen, der Check bleibt dauerhaft UNKNOWN"
                    if typ == "snmp_table_state"
                    else "die Zahlen-Zuordnung trifft nie, der Check meldet dauerhaft „unmapped“"
                ),
            )
        elif o.enum and zahlen:
            funde.extend(_enum_regeln(name, wer, o, cfg))
    return funde


_FENSTER_NAME = re.compile(r"(\d+)\s*-?\s*min", re.I)
_FENSTER_OBJ = re.compile(r"(\d+)\s*-?\s*min|Last(\d+)Minutes?|LastMinute|LastSecond", re.I)
#: Messgröße im Namen — Name des Checks gegen Name des MIB-Objekts.
_GROESSEN = (
    ("fan", re.compile(r"fan|lüfter|luefter", re.I)),
    ("temp", re.compile(r"temp", re.I)),
    ("volt", re.compile(r"volt", re.I)),
    ("cpu", re.compile(r"cpu|processor", re.I)),
    ("mem", re.compile(r"mem(?!ber)|\bram\b", re.I)),
)
#: ssCpuUser/ssCpuSystem: nur EIN Anteil der Last (UCD-SNMP-MIB, deprecated).
_CPU_TEILANTEIL = ("1.3.6.1.4.1.2021.11.9", "1.3.6.1.4.1.2021.11.10")


def _fenster_obj(name: str) -> int | None:
    m = _FENSTER_OBJ.search(name)
    if not m:
        return None
    if m.group(0).lower() == "lastminute":
        return 1
    if m.group(0).lower() == "lastsecond":
        return 0
    return int(m.group(1) or m.group(2))


def _name_regeln(name: str, wer: str, o: MibObjekt, oid: Any) -> list[Fund]:
    funde: list[Fund] = []
    n = _FENSTER_NAME.search(name)
    fo = _fenster_obj(o.name)
    if n and fo is not None and int(n.group(1)) != fo:
        funde.append(
            Fund(
                WARNUNG,
                "snmp_fenster",
                name,
                f"Name nennt {n.group(1)} min, {wer} misst "
                + ("je Sekunde" if fo == 0 else f"über {fo} min"),
            )
        )
    im_namen = {k for k, r in _GROESSEN if r.search(name)}
    im_objekt = {k for k, r in _GROESSEN if r.search(o.name)}
    if len(im_namen) == 1 and im_objekt and not (im_namen & im_objekt):
        funde.append(
            Fund(
                WARNUNG,
                "snmp_name_passt_nicht",
                name,
                f"Name sagt {next(iter(im_namen))}, die OID ist {wer}",
            )
        )
    o_oid = _norm_oid(oid)
    if any(o_oid == t or o_oid == t + ".0" for t in _CPU_TEILANTEIL) and re.search(
        r"cpu", name, re.I
    ):
        funde.append(
            Fund(
                WARNUNG,
                "snmp_cpu_teilanteil",
                name,
                f"{wer} ist nur ein Anteil der CPU-Last — "
                "Auslastung = 100 − ssCpuIdle (scale −1, offset 100)",
            )
        )
    return funde


def _enum_regeln(name: str, wer: str, o: MibObjekt, cfg: dict[str, Any]) -> list[Fund]:
    funde: list[Fund] = []
    enum = dict(o.enum or ())
    werte = {int(w) for w in _status_werte(cfg)}
    fremd = sorted(werte - set(enum))
    if fremd:
        funde.append(
            Fund(
                WARNUNG,
                "snmp_enum",
                name,
                f"Werte {fremd} kennt {wer} nicht (MIB: {_enum_text(enum)})",
            )
        )
    unmapped = str(cfg.get("unmapped_status") or "WARNING").upper()
    if unmapped != "CRITICAL":
        crit = {int(x) for x in cfg.get("crit_values") or [] if re.fullmatch(r"-?\d+", str(x))}
        vm = cfg.get("value_map") if isinstance(cfg.get("value_map"), dict) else {}
        crit |= {
            int(k)
            for k, v in vm.items()
            if re.fullmatch(r"-?\d+", str(k)) and str(v).upper() == "CRITICAL"
        }
        offen = [
            f"{k} {v}"
            for k, v in sorted(enum.items())
            if k not in werte and _KRITISCH_KLINGT.search(v)
        ]
        if offen:
            funde.append(
                Fund(
                    WARNUNG,
                    "snmp_enum",
                    name,
                    f"{', '.join(offen)} ({wer}) ist nicht zugeordnet "
                    f"und landet nur auf {unmapped}",
                )
            )
    labels = cfg.get("state_labels") or cfg.get("value_labels")
    if isinstance(labels, dict):
        namen = {re.sub(r"[^a-z0-9]", "", v.lower()): k for k, v in enum.items()}
        for roh, label in labels.items():
            if not re.fullmatch(r"-?\d+", str(roh)):
                continue
            k = int(roh)
            norm = re.sub(r"[^a-z0-9]", "", str(label).lower())
            if norm in namen and namen[norm] != k:
                funde.append(
                    Fund(
                        WARNUNG,
                        "snmp_enum",
                        name,
                        f"Beschriftung {k} = „{label}“, laut {wer} heißt {k} „{enum.get(k, '?')}“",
                    )
                )
                break
    return funde


def _enum_text(enum: dict[int, str]) -> str:
    teile = [f"{k} {v}" for k, v in sorted(enum.items())]
    return ", ".join(teile[:12]) + (" …" if len(teile) > 12 else "")


# --- Übrige Check-Regeln -------------------------------------------------------


def _ssl_regeln(c: dict[str, Any]) -> list[Fund]:
    if c.get("check_type") != "ssl_certificate":
        return []
    cfg = c.get("check_config") or {}
    if (
        isinstance(cfg, dict)
        and cfg.get("warn_days") not in (None, "")
        and cfg.get("crit_days") not in (None, "")
    ):
        return []
    return [
        Fund(
            HINWEIS,
            "ssl_tage",
            str(c.get("name") or "?"),
            "ssl_certificate ohne warn_days/crit_days in der Config — es gelten 30/14 Tage, "
            "Schwellwert-Spalten wirken hier nicht",
        )
    ]


# --- Einstieg ------------------------------------------------------------------


def pruefe_bundle(
    bundle: dict[str, Any],
    mibs: MibAufloeser | None = None,
    werkzeuge: Werkzeuge | None = None,
) -> Ergebnis:
    """Ein Bundle (``schema_version`` 1) → alle Funde. Wirft nie wegen des Inhalts."""
    w = werkzeuge or Werkzeuge.finden()
    erg = Ergebnis(werkzeuge=w.als_dict())
    if not isinstance(bundle, dict):
        return erg
    profil = bundle.get("profile") if isinstance(bundle.get("profile"), dict) else {}
    checks = [c for c in bundle.get("checks") or [] if isinstance(c, dict)]
    scripts = [s for s in bundle.get("scripts") or [] if isinstance(s, dict)]

    for c in checks:
        erg.funde.extend(_snmp_regeln(c, mibs))
        erg.funde.extend(_ssl_regeln(c))

    nutzer: dict[str, list[dict[str, Any]]] = {}
    for c in checks:
        sid = (
            (c.get("check_config") or {}).get("script_id")
            if isinstance(c.get("check_config"), dict)
            else None
        )
        if sid:
            nutzer.setdefault(str(sid), []).append(c)
    hat_shell = False
    for s in scripts:
        if str(s.get("interpreter") or "bash").strip().lower() in ("bash", "sh", "shell", ""):
            hat_shell = True
        try:
            erg.funde.extend(_script_regeln(s, nutzer.get(str(s.get("script_id")), []), profil, w))
        except Exception as e:  # noqa: BLE001 — ein Prüferfehler ist ein Fund, kein Absturz
            erg.funde.append(
                Fund(
                    NICHT_PRUEFBAR,
                    "pruefer_fehler",
                    f"Script {s.get('name')}",
                    f"Prüfung abgebrochen: {e!s:.120}",
                )
            )
    if hat_shell and not w.shellcheck:
        erg.funde.append(
            Fund(
                NICHT_PRUEFBAR,
                "werkzeug_fehlt",
                "(Bundle)",
                "shellcheck nicht installiert — Shell-Rümpfe nur mit bash -n geprüft",
            )
        )
    return erg


def kurztext(funde: Iterable[Fund], grenze: int = 8) -> str:
    """Eine Zeile je Fund, für Fehlermeldungen (Upload-Abweisung, Rollout-Sperre)."""
    liste = list(funde)
    zeilen = [f"{f.check}: {f.text}" for f in liste[:grenze]]
    if len(liste) > grenze:
        zeilen.append(f"… und {len(liste) - grenze} weitere")
    return "; ".join(zeilen)
