"""Hub-Anbindung der Profil-Prüfung (``app/profile_lint``).

Die Regeln selbst stehen in ``app/profile_lint/lint.py`` (wortgleich im
Vesana-Repo). Hier nur, was der Hub dazugibt: die MIB-Typen aus der eigenen
Bibliothek (``mib_modules``, neueste Revision je Modul) plus die mitgelieferten
Standard-MIBs, und die Entscheidung beim Hochladen.

🔒 Ein FEHLER weist den Upload ab (422) — nur sichere Defekte (Syntaxfehler,
Wert garantiert 0/immer gleich, Prozent auf Byte-/Zählergrößen). Alles andere
geht mit der Antwort zurück und steht in der Admin-Prüfung, blockiert aber nie.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mib_module import MibModule
from app.profile_lint import lint
from app.services import mib_library

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "aufloeser": None}


def mib_aufloeser(db: Session) -> lint.MibAufloeser:
    """Standard-MIBs + Hub-Bibliothek. Neu gebaut nur, wenn sich die Bibliothek
    geändert hat (Schlüssel = Modul + sha256 der neuesten Revisionen)."""
    zeilen = mib_library.search(db)
    key = tuple(sorted((r.module, r.sha256) for r in zeilen))
    with _cache_lock:
        if _cache["key"] == key and _cache["aufloeser"] is not None:
            return _cache["aufloeser"]
    ids = [r.id for r in zeilen]
    verzeichnisse: list[dict[str, Any]] = []
    if ids:
        for directory in db.execute(
            select(MibModule.directory).where(MibModule.id.in_(ids))
        ).scalars():
            if isinstance(directory, dict):
                verzeichnisse.append(directory)
    aufloeser = lint.MibAufloeser.mit_standard(verzeichnisse)
    with _cache_lock:
        _cache["key"] = key
        _cache["aufloeser"] = aufloeser
    return aufloeser


def pruefe(db: Session, bundle: dict[str, Any]) -> lint.Ergebnis:
    return lint.pruefe_bundle(bundle, mib_aufloeser(db))


def pruefe_fuer_upload(db: Session, bundle: dict[str, Any]) -> lint.Ergebnis:
    """Prüft ein hochgeladenes Bundle; ein Fehler ⇒ 422 mit Klartext je Fund."""
    ergebnis = pruefe(db, bundle)
    fehler = ergebnis.fehler
    if fehler:
        raise HTTPException(
            status_code=422,
            detail=f"Profil-Prüfung: {len(fehler)} Fehler — {lint.kurztext(fehler)}",
        )
    return ergebnis


def info() -> dict[str, Any]:
    return {
        "lint_version": lint.LINT_VERSION,
        "fingerprint": lint.FINGERPRINT,
        "werkzeuge": lint.Werkzeuge.finden().als_dict(),
    }
