"""Application settings for vesana-community.

Loaded from environment variables (and an optional ``.env`` file) via
pydantic-settings. The portal's Ed25519 PUBLIC key is the trust anchor for
login JWTs and can be supplied either inline as a PEM string
(``PORTAL_PUBLIC_KEY``) or as a path to a PEM file (``PORTAL_PUBLIC_KEY_PATH``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database -----------------------------------------------------------
    # On prod this points at the existing shared Postgres; vesana-community
    # keeps all of its tables in a dedicated ``community`` schema.
    DATABASE_URL: str = "postgresql+psycopg://community:community@localhost:5432/community"

    # --- Secrets ------------------------------------------------------------
    # Signs the session cookie AND the long-lived HS256 API tokens.
    SECRET_KEY: str = "dev-insecure-change-me"

    # --- Portal trust anchor (Ed25519 public key, PEM) ----------------------
    PORTAL_PUBLIC_KEY: str | None = None
    PORTAL_PUBLIC_KEY_PATH: str | None = None

    # --- Token lifetimes ----------------------------------------------------
    API_TOKEN_TTL_DAYS: int = 30
    LOGIN_TOKEN_LEEWAY_SECONDS: int = 30

    # --- Admin basic auth ---------------------------------------------------
    COMMUNITY_ADMIN_USER: str = "admin"
    COMMUNITY_ADMIN_PASSWORD: str = "change-me"

    # --- Misc ---------------------------------------------------------------
    COMMUNITY_BASE_URL: str = "http://localhost:8080"

    # Set the ``Secure`` flag on the session cookie (cookie only sent over
    # HTTPS). MUST be true in any TLS-terminated deployment; stays false for
    # local HTTP dev. Driven by env so no code change is needed per environment.
    SESSION_COOKIE_SECURE: bool = False

    @model_validator(mode="after")
    def _resolve_portal_public_key(self) -> Settings:
        """Materialise PORTAL_PUBLIC_KEY from a PEM file if a path was given.

        An inline ``PORTAL_PUBLIC_KEY`` always wins; otherwise we read the file
        referenced by ``PORTAL_PUBLIC_KEY_PATH`` and cache its contents.
        """
        if not self.PORTAL_PUBLIC_KEY and self.PORTAL_PUBLIC_KEY_PATH:
            path = Path(self.PORTAL_PUBLIC_KEY_PATH)
            self.PORTAL_PUBLIC_KEY = path.read_text(encoding="utf-8")
        return self

    @property
    def portal_public_key_pem(self) -> str:
        """Return the portal public key PEM or raise if it was never configured."""
        if not self.PORTAL_PUBLIC_KEY:
            raise RuntimeError(
                "PORTAL_PUBLIC_KEY (or PORTAL_PUBLIC_KEY_PATH) is not configured; "
                "cannot verify login JWTs."
            )
        return self.PORTAL_PUBLIC_KEY


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Tests that need a different environment should clear this cache via
    ``get_settings.cache_clear()`` after monkeypatching ``os.environ``.
    """
    return Settings()
