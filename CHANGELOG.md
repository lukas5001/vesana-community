# Changelog

All notable changes to vesana-community are documented here. This project uses
its own version scheme, independent of the main Vesana product.

## 0.1.0 — Foundation

- Scaffold of the standalone FastAPI app (sibling to Vesana + the licence portal).
- Dedicated Postgres schema `community`; `instances` and `used_login_tokens`
  tables with an initial Alembic migration.
- Portal-JWT SSO auth:
  - `GET /auth?token=` browser single sign-on (session cookie).
  - `POST /api/v1/auth/exchange` (login JWT → API token).
  - `POST /api/v1/auth/refresh` (Bearer API token → fresh API token).
  - Login JWTs verified with the portal's Ed25519 public key (EdDSA, no
    roundtrip); single-use enforcement via `used_login_tokens(jti)`.
  - Stateless HS256 API tokens signed with the app's own `SECRET_KEY`; blocked
    instances rejected per request.
- `GET /health` liveness endpoint.
- Dark-theme HTML shell (Jinja2 templates + Vesana-style CSS tokens).
- CI (ruff check / ruff format --check / pytest on Python 3.12), Dockerfile
  (non-root, `python:3.12-slim`), and docker-compose for local dev.
