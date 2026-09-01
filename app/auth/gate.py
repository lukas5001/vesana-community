"""Besucher-Tor: die HTML-Seiten gibt es nur mit einer Vesana-Sitzung.

community.vesana.org gehört zu den Vesana-Instanzen — wer keine offene
Instanz-Sitzung hat (SSO-Deep-Link ``/auth?token=`` aus der App), sieht statt
der Bibliothek eine Tor-Seite, die den Weg beschreibt. Die Maschinen-API
(``/api/v1/*``) ist davon NICHT betroffen: die Instanzen holen Profile,
Bundles und Icons serverseitig, teils ohne Token und auch in alten Versionen.
Ebenso frei bleiben ``/auth``, ``/logout``, ``/lang``, ``/health``,
``/static`` und der Admin-Bereich (eigene Anmeldung).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.auth.deps import get_session_instance
from app.models.instance import Instance

# Pfade des Seiten-Routers, die auch ohne Sitzung erreichbar bleiben.
GATE_EXEMPT_PREFIXES = ("/lang/",)


class VisitorGateRequired(Exception):
    """Ausgelöst, wenn eine HTML-Seite ohne Instanz-Sitzung aufgerufen wird."""


def require_visitor(
    request: Request,
    instance: Annotated[Instance | None, Depends(get_session_instance)],
) -> None:
    if instance is not None:
        return
    if request.url.path.startswith(GATE_EXEMPT_PREFIXES):
        return
    raise VisitorGateRequired()
