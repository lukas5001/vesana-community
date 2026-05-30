"""Health endpoint: body values, not just the status code."""

from __future__ import annotations

from app.version import VERSION


def test_health_returns_ok_body(client):
    resp = client.get("/health")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "vesana-community"
    assert body["version"] == VERSION
