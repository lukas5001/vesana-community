# vesana-community

**community.vesana.org** — the Vesana Community Hub.

A standalone FastAPI application that is a *sibling* to the main Vesana product
and the licence portal. It has its **own repository** and its **own version
scheme** (`0.1.0`, `0.2.0`, …), independent of Vesana (which lives on `1.9.x`).

## What it is

The Community Hub is where Vesana instances meet. There is **no password to
type** here: users arrive via single sign-on from their own Vesana instance.
The **licence portal is the only trust anchor** — it signs a short-lived login
token that vesana-community verifies cryptographically, with no roundtrip back
to the portal.

## Architecture

```
Licence portal (Ed25519 PRIVATE key)
        │  signs short-lived login JWT (EdDSA), TTL 5 min, single-use
        ▼
vesana-community  ──verifies with portal Ed25519 PUBLIC key (no roundtrip)
        │
        ├─ upserts the Instance row (display_name, avatar)
        ├─ enforces single-use via community.used_login_tokens(jti)
        └─ issues its OWN long-lived API token (HS256, signed with SECRET_KEY)
                 │  30 days, refreshable, stateless, checked per request
                 ▼  (Vesana backend stores & presents this token)
```

* **Login JWT** — issued by the portal, algorithm `EdDSA`. Payload:
  `{ iss:"vesana-licence-portal", sub: instance_uuid, display_name,
  avatar_b64?, jti, iat, exp }`. TTL 5 min, single-use (replay → 401).
* **API token** — issued by vesana-community, algorithm `HS256`, signed with
  `SECRET_KEY`. Payload `{ sub: instance_uuid, typ:"api", iat, exp }`. Verified
  on every request; blocked instances are rejected.
* **Storage** — all tables live in a dedicated Postgres schema, `community`, so
  the app can safely share the prod Postgres instance with the rest of Vesana.

### Auth endpoints

| Method | Path                      | Purpose                                                        |
|--------|---------------------------|---------------------------------------------------------------|
| GET    | `/auth?token=<loginJWT>`  | Browser SSO: verify, upsert, set session cookie, redirect `/` |
| POST   | `/api/v1/auth/exchange`   | Machine-to-machine: login JWT → `{api_token, expires_at}`     |
| POST   | `/api/v1/auth/refresh`    | Bearer API token → fresh `{api_token, expires_at}`            |
| GET    | `/health`                 | Liveness probe → `{status, service, version}`                 |

## Dev quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then edit SECRET_KEY + PORTAL_PUBLIC_KEY(_PATH)

# Bring up a local Postgres (the compose `postgres` service is dev-only):
docker compose up -d postgres

alembic upgrade head
uvicorn app.main:app --reload
```

App is then on http://localhost:8000 (or 8080 in Docker).

## Running tests

```bash
pytest -q
```

* Pure crypto and health tests **always run**.
* DB-dependent SSO tests are **skipped** unless `DATABASE_URL_TEST` points at a
  reachable Postgres, e.g.:

```bash
export DATABASE_URL_TEST="postgresql+psycopg://community:community@localhost:5432/community"
pytest -q
```

## Lint

CI runs, in order: `ruff check .`, `ruff format --check .`, `pytest -q`
(Python 3.12). Run the same locally before opening a PR:

```bash
ruff check .
ruff format --check .
```

## Environment variables

| Variable                    | Default                                   | Description                                                       |
|-----------------------------|-------------------------------------------|-------------------------------------------------------------------|
| `DATABASE_URL`              | local Postgres URL                        | SQLAlchemy URL; prod = shared Postgres, schema `community`        |
| `SECRET_KEY`                | `dev-insecure-change-me`                   | Signs the session cookie **and** HS256 API tokens                 |
| `PORTAL_PUBLIC_KEY`         | (unset)                                   | Portal Ed25519 public key, inline PEM                             |
| `PORTAL_PUBLIC_KEY_PATH`    | (unset)                                   | Path to a PEM file (used if `PORTAL_PUBLIC_KEY` is unset)         |
| `API_TOKEN_TTL_DAYS`        | `30`                                      | API token lifetime in days                                        |
| `LOGIN_TOKEN_LEEWAY_SECONDS`| `30`                                      | Clock-skew leeway when verifying login JWT expiry                 |
| `COMMUNITY_ADMIN_USER`      | `admin`                                   | HTTP Basic admin username                                         |
| `COMMUNITY_ADMIN_PASSWORD`  | `change-me`                               | HTTP Basic admin password (compared timing-safe)                  |
| `COMMUNITY_BASE_URL`        | `http://localhost:8080`                   | Public base URL of this app                                       |

## Deployment

* **Docker**: `python:3.12-slim`, runs as non-root `app` (UID 1000), serves on
  `:8080` via uvicorn.
* **Compose**: the `community` service plus a **dev-only** `postgres` service.
  On prod, point `DATABASE_URL` at the existing shared Postgres (schema
  `community`) and do **not** run the bundled `postgres` service.
* **CI**: GitHub Actions on the self-hosted `vesana-vps` runner.
