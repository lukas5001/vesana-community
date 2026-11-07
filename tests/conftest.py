"""Shared pytest fixtures.

Key ideas:
* DB-dependent tests are skipped unless ``DATABASE_URL_TEST`` is set to a
  reachable Postgres; pure logic tests always run.
* Testdaten entstehen direkt in der DB (``insert_profile``/``insert_instance``)
  — Uploads und Instanz-Anmeldung gibt es seit der Vesana-Bibliothek nicht mehr.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

TEST_SECRET_KEY = "test-secret-key-do-not-use-in-prod"

os.environ["SECRET_KEY"] = TEST_SECRET_KEY
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


# --- Datenhelfer (DB-Tests) -------------------------------------------------
# Uploads und Instanz-Anmeldung gibt es seit dem Umbau zur Vesana-Bibliothek
# (2026-11-07) nicht mehr — Testdaten kommen direkt in die DB, so wie sie das
# Signier-Werkzeug bzw. der Altbestand hinterlassen.


def insert_instance(uuid_: str, display_name: str = "Test Instance") -> str:
    from app.db import SessionLocal
    from app.models.instance import Instance

    with SessionLocal() as db:
        db.add(Instance(uuid=uuid_, display_name=display_name, is_blocked=False))
        db.commit()
    return uuid_


def insert_profile(
    name: str,
    *,
    tier: str = "community",
    review_status: str = "pending",
    vendor: str = "ACME",
    uploader: str | None = None,
    bundle: dict | None = None,
    signed: bool = False,
) -> str:
    """Profil + eine (``is_current``) Version; ``signed`` ⇒ für alle freigegeben."""
    from app.db import SessionLocal
    from app.models.community_profile import CommunityProfile
    from app.models.community_profile_version import CommunityProfileVersion

    bundle = bundle or {
        "schema_version": 1,
        "profile": {"name": name, "vendor": vendor},
        "checks": [{"check_type": "ping", "check_config": {"host": "1.1.1.1"}}],
    }
    with SessionLocal() as db:
        profile = CommunityProfile(
            name=name,
            vendor=vendor,
            tier=tier,
            review_status=review_status,
            approved=review_status == "approved",
            uploader_instance_uuid=uploader,
        )
        db.add(profile)
        db.flush()
        version = CommunityProfileVersion(
            profile_id=profile.id,
            version_tag="1.0.0",
            bundle_json=bundle,
            is_current=True,
            signed_envelope=(
                {"format": "vesana-library-sig/1", "key_id": "k", "sig": "s", "payload": "{}"}
                if signed
                else None
            ),
            released_all_at=datetime.now(UTC) - timedelta(minutes=1) if signed else None,
        )
        db.add(version)
        db.flush()
        profile.latest_version_id = version.id
        db.commit()
        return profile.id
