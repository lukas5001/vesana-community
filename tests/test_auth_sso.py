"""DB-dependent SSO flow tests.

Skipped unless DATABASE_URL_TEST points at a reachable Postgres. Covers:
* exchange a login JWT for an API token,
* replay of the same jti -> 401,
* GET /auth?token sets the session cookie + redirects,
* refresh with a Bearer API token returns a fresh token.
"""

from __future__ import annotations

from tests.conftest import requires_db


@requires_db
def test_exchange_returns_api_token(db_app_client, make_login_jwt):
    token = make_login_jwt(
        sub="33333333-3333-3333-3333-333333333333",
        display_name="ExchangeInstance",
        jti="sso-exchange-1",
    )
    resp = db_app_client.post("/api/v1/auth/exchange", json={"token": token})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["api_token"]
    assert body["expires_at"]


@requires_db
def test_replay_same_jti_is_rejected(db_app_client, make_login_jwt):
    token = make_login_jwt(
        sub="44444444-4444-4444-4444-444444444444",
        jti="sso-replay-1",
    )
    first = db_app_client.post("/api/v1/auth/exchange", json={"token": token})
    assert first.status_code == 200, first.text

    second = db_app_client.post("/api/v1/auth/exchange", json={"token": token})
    assert second.status_code == 401, second.text


@requires_db
def test_auth_sso_sets_session_and_redirects(db_app_client, make_login_jwt):
    token = make_login_jwt(
        sub="55555555-5555-5555-5555-555555555555",
        display_name="SessionInstance",
        jti="sso-session-1",
    )
    resp = db_app_client.get("/auth", params={"token": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # A session cookie must have been set.
    assert "vesana_community_session" in resp.cookies

    # The index page now shows the logged-in instance display name.
    home = db_app_client.get("/")
    assert home.status_code == 200
    assert "SessionInstance" in home.text


@requires_db
def test_refresh_returns_new_token(db_app_client, make_login_jwt):
    token = make_login_jwt(
        sub="66666666-6666-6666-6666-666666666666",
        jti="sso-refresh-1",
    )
    exchanged = db_app_client.post("/api/v1/auth/exchange", json={"token": token})
    assert exchanged.status_code == 200, exchanged.text
    api_token = exchanged.json()["api_token"]

    refreshed = db_app_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["api_token"]


@requires_db
def test_refresh_without_token_is_rejected(db_app_client):
    resp = db_app_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401, resp.text
