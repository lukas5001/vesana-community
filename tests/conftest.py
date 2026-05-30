"""Shared pytest fixtures.

Key ideas:
* A freshly generated Ed25519 keypair stands in for the licence portal. Its
  PUBLIC key PEM is injected into the environment BEFORE the app/config is
  imported, so vesana-community verifies login JWTs we mint with the matching
  PRIVATE key.
* ``make_login_jwt`` mints valid portal login JWTs for tests.
* DB-dependent tests are skipped unless ``DATABASE_URL_TEST`` is set to a
  reachable Postgres; pure crypto/logic tests always run.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# --- Generate a portal keypair and configure the environment up-front -------
# This MUST happen before app.config / app.db / app.main are imported anywhere,
# because app.db builds its engine from DATABASE_URL at first import.
_PORTAL_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PORTAL_PRIVATE_PEM = _PORTAL_PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")
_PORTAL_PUBLIC_PEM = (
    _PORTAL_PRIVATE_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)

TEST_SECRET_KEY = "test-secret-key-do-not-use-in-prod"

os.environ["SECRET_KEY"] = TEST_SECRET_KEY
os.environ["PORTAL_PUBLIC_KEY"] = _PORTAL_PUBLIC_PEM
os.environ["COMMUNITY_ADMIN_USER"] = "admin"
os.environ["COMMUNITY_ADMIN_PASSWORD"] = "test-admin-pass"
os.environ["API_TOKEN_TTL_DAYS"] = "30"
os.environ["LOGIN_TOKEN_LEEWAY_SECONDS"] = "30"

# If a real test database is configured, point DATABASE_URL at it BEFORE any app
# module is imported, so the module-level engine binds to the right place and we
# never need a fragile importlib.reload() dance.
_DB_TEST_URL = os.environ.get("DATABASE_URL_TEST")
if _DB_TEST_URL:
    os.environ["DATABASE_URL"] = _DB_TEST_URL
else:
    # A harmless default so importing app.db never fails when no DB is present.
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://community:community@localhost:5432/community",
    )

LOGIN_ISSUER = "vesana-licence-portal"


@pytest.fixture(scope="session")
def portal_private_pem() -> str:
    return _PORTAL_PRIVATE_PEM


@pytest.fixture(scope="session")
def portal_public_pem() -> str:
    return _PORTAL_PUBLIC_PEM


@pytest.fixture(scope="session")
def make_login_jwt():
    """Return a factory that mints portal-signed login JWTs.

    Usage::

        token = make_login_jwt()                      # all defaults
        token = make_login_jwt(sub="...", exp_delta=timedelta(minutes=-1))
    """

    def _make(
        *,
        sub: str | None = None,
        display_name: str = "Test Instance",
        avatar_b64: str | None = None,
        jti: str | None = None,
        iss: str = LOGIN_ISSUER,
        iat_delta: timedelta = timedelta(0),
        exp_delta: timedelta = timedelta(minutes=5),
        algorithm: str = "EdDSA",
        signing_key: str | None = None,
        extra_claims: dict | None = None,
    ) -> str:
        now = datetime.now(UTC)
        payload: dict = {
            "iss": iss,
            "sub": sub or str(uuid.uuid4()),
            "display_name": display_name,
            "jti": jti or str(uuid.uuid4()),
            "iat": int((now + iat_delta).timestamp()),
            "exp": int((now + exp_delta).timestamp()),
        }
        if avatar_b64 is not None:
            payload["avatar_b64"] = avatar_b64
        if extra_claims:
            payload.update(extra_claims)
        key = signing_key if signing_key is not None else _PORTAL_PRIVATE_PEM
        return jwt.encode(payload, key, algorithm=algorithm)

    return _make


# --- DB availability gate ---------------------------------------------------
def _db_is_available() -> bool:
    """True only if DATABASE_URL_TEST is set AND the database is reachable."""
    if not _DB_TEST_URL:
        return False
    try:
        from sqlalchemy import create_engine, text

        eng = create_engine(_DB_TEST_URL, connect_args={"connect_timeout": 5})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


_DB_AVAILABLE = _db_is_available()

requires_db = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason="no reachable test database (set DATABASE_URL_TEST to run DB tests)",
)


@pytest.fixture
def settings():
    """A fresh Settings instance built from the test environment."""
    from app.config import Settings

    return Settings()


@pytest.fixture
def client():
    """A TestClient over the real app.

    Crypto/health endpoints work without a DB.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def _db_schema():
    """Create the ``community`` schema + tables ONCE for the whole session.

    DATABASE_URL was pointed at DATABASE_URL_TEST at import time, so the
    module-level engine in ``app.db`` is already bound to the test database.
    There is NO importlib.reload() here: the model classes the app imports and
    the metadata we ``create_all`` are one and the same object.
    """
    from sqlalchemy import text

    import app.db as db_mod
    import app.models  # noqa: F401  ensure all models are registered on Base

    with db_mod.engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{db_mod.SCHEMA}"'))

    db_mod.Base.metadata.drop_all(db_mod.engine)
    db_mod.Base.metadata.create_all(db_mod.engine)
    try:
        yield db_mod
    finally:
        db_mod.Base.metadata.drop_all(db_mod.engine)


@pytest.fixture
def db_app_client(_db_schema):
    """A TestClient wired to the real test database (DB tests only).

    Truncates all tables before each test so every case starts clean.
    """
    db_mod = _db_schema

    from sqlalchemy import text

    table_names = [
        f'"{db_mod.SCHEMA}"."{t.name}"' for t in reversed(db_mod.Base.metadata.sorted_tables)
    ]
    if table_names:
        with db_mod.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(table_names)} RESTART IDENTITY CASCADE"))

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
