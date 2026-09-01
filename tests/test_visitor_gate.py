"""Besucher-Tor: HTML nur mit Vesana-Sitzung, Maschinen-API bleibt frei.

Lukas (09/2026): „aktuell kommt jeder einfach so auf community.vesana.org,
allerdings sollten nur jene drauf kommen, die eine Vesana-Session offen haben."
Die Instanzen holen Profile/Bundles/Icons weiter über ``/api/v1/*`` — auch alte
Versionen ohne Token — deshalb darf das Tor die API NIE erfassen.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import requires_db

HTML_PAGES = ("/", "/browse", "/questions", "/questions/ask", "/upload", "/account", "/icons")


def test_anonymous_html_pages_show_the_gate(client: TestClient) -> None:
    for path in HTML_PAGES:
        resp = client.get(path)
        assert resp.status_code == 403, (path, resp.status_code)
        assert "Community Hub" in resp.text and 'class="gate"' in resp.text, path
        assert resp.headers.get("x-robots-tag") == "noindex, nofollow", path
        # Kein Navigations-Menü für Anonyme — nichts, was zum Klicken einlädt.
        assert 'class="nav"' not in resp.text, path


def test_gate_is_translated(client: TestClient) -> None:
    de = client.get("/", cookies={"lang": "de"})
    en = client.get("/", cookies={"lang": "en"})
    assert "Nur mit Vesana-Sitzung." in de.text
    assert "Vesana session required." in en.text


def test_machine_api_and_infrastructure_stay_open(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/static/css/community.css").status_code == 200
    assert client.get("/robots.txt").text.startswith("User-agent: *")
    assert client.get("/admin/login").status_code == 200
    # Sprachwechsel darf auch VOR der Anmeldung gehen (das Tor selbst ist zweisprachig).
    assert client.get("/lang/en", follow_redirects=False).status_code == 303
    # /auth ohne Token ist ein Validierungsfehler, aber kein Tor.
    assert client.get("/auth").status_code == 422


@requires_db
def test_api_is_reachable_without_session(db_app_client: TestClient) -> None:
    for path in ("/api/v1/profiles", "/api/v1/profiles/match-rules", "/api/v1/icon-library"):
        resp = db_app_client.get(path)
        assert resp.status_code == 200, (path, resp.status_code)


@requires_db
def test_session_opens_the_gate(db_app_client: TestClient, make_login_jwt) -> None:
    assert db_app_client.get("/").status_code == 403
    token = make_login_jwt(
        sub="66666666-6666-6666-6666-666666666666", display_name="GateInstance", jti="gate-1"
    )
    db_app_client.get("/auth", params={"token": token}, follow_redirects=False)
    home = db_app_client.get("/")
    assert home.status_code == 200
    assert "GateInstance" in home.text and 'class="nav"' in home.text
    # Abmelden schließt das Tor wieder.
    db_app_client.get("/logout", follow_redirects=False)
    assert db_app_client.get("/").status_code == 403
