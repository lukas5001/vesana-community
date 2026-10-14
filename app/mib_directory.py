"""Das Vesana-Objekt-Verzeichnis eines MIB-Moduls (Format 1) — Hub-Kopie.

Wortgleiche Kopie von ``shared/mib/directory.py`` im Vesana-Repo (der Hub kann
das Paket nicht importieren). Der Hub prüft beim Veröffentlichen gegen dieses
Modell; jede Instanz prüft beim Import ERNEUT gegen ihre eigene Fassung — ein
Hub-Eintrag ist für die Instanz fremder Inhalt. Wer das Format ändert, ändert
beide Dateien (neues ``format`` statt stiller Erweiterung).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORMAT_VERSION = 1

#: Obergrenzen — ein Verzeichnis ist Daten, die jeder Mandant zu sehen bekommt.
MAX_OBJECTS = 60_000
MAX_DESCRIPTION = 4_000

OID_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,127}$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")

ObjectKind = Literal["scalar", "table", "entry", "column", "node", "notification"]

#: Kanonische Basistypen (SMIv2-Schreibweise).
BASE_TYPES = (
    "Integer32",
    "Unsigned32",
    "Gauge32",
    "Counter32",
    "Counter64",
    "TimeTicks",
    "IpAddress",
    "OCTET STRING",
    "OBJECT IDENTIFIER",
    "BITS",
    "Opaque",
)


class DirectoryObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oid: str = Field(max_length=600)
    name: str = Field(max_length=128)
    kind: ObjectKind
    syntax: str | None = Field(default=None, max_length=32)
    tc_name: str | None = Field(default=None, max_length=128)
    enum: list[tuple[int, str]] | None = Field(default=None, max_length=2000)
    bits: list[tuple[int, str]] | None = Field(default=None, max_length=1024)
    units: str | None = Field(default=None, max_length=128)
    display_hint: str | None = Field(default=None, max_length=128)
    access: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    table_oid: str | None = Field(default=None, max_length=600)
    index_columns: list[str] | None = Field(default=None, max_length=32)
    augments: str | None = Field(default=None, max_length=128)
    name_column: str | None = Field(default=None, max_length=600)

    @field_validator("oid", "table_oid", "name_column")
    @classmethod
    def _oid(cls, v: str | None) -> str | None:
        if v is not None and not OID_RE.match(v):
            raise ValueError("keine numerische OID")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("ungültiger Objektname")
        return v

    @field_validator("syntax")
    @classmethod
    def _syntax(cls, v: str | None) -> str | None:
        if v is not None and v not in BASE_TYPES:
            raise ValueError("unbekannter Basistyp")
        return v

    @field_validator("enum", "bits")
    @classmethod
    def _labels(cls, v: list[tuple[int, str]] | None) -> list[tuple[int, str]] | None:
        if v is None:
            return v
        for _num, label in v:
            if not label or len(label) > 128:
                raise ValueError("Bezeichnung leer oder zu lang")
        return v


class MibDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal[1] = FORMAT_VERSION
    module: str = Field(max_length=128)
    revision: str | None = Field(default=None, max_length=32)
    organization: str | None = Field(default=None, max_length=512)
    identity_oid: str | None = Field(default=None, max_length=600)
    imports: list[str] = Field(default_factory=list, max_length=256)
    descriptions_included: bool = True
    objects: list[DirectoryObject] = Field(default_factory=list, max_length=MAX_OBJECTS)

    @field_validator("module")
    @classmethod
    def _module(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("ungültiger Modulname")
        return v

    @field_validator("identity_oid")
    @classmethod
    def _identity(cls, v: str | None) -> str | None:
        if v is not None and not OID_RE.match(v):
            raise ValueError("keine numerische OID")
        return v

    @field_validator("imports")
    @classmethod
    def _imports(cls, v: list[str]) -> list[str]:
        for name in v:
            if not NAME_RE.match(name):
                raise ValueError("ungültiger Import-Modulname")
        return v


def enterprise_of(directory: MibDirectory) -> str | None:
    """Hersteller-Zweig ``1.3.6.1.4.1.<n>`` des Moduls, sonst None.

    Zuerst die MODULE-IDENTITY, sonst die häufigste Hersteller-Nummer unter den
    Objekten (SMIv1-Module haben keine Identität)."""
    prefix = "1.3.6.1.4.1."
    if directory.identity_oid and directory.identity_oid.startswith(prefix):
        parts = directory.identity_oid.split(".")
        if len(parts) > 6:
            return ".".join(parts[:7])
    counts: dict[str, int] = {}
    for obj in directory.objects:
        if obj.oid.startswith(prefix):
            parts = obj.oid.split(".")
            if len(parts) > 6:
                key = ".".join(parts[:7])
                counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda k: counts[k])
