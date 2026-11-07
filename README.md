# vesana-community

**community.vesana.org** — die **Vesana-Bibliothek**.

A standalone FastAPI application, a *sibling* to the main Vesana product and the
licence portal, with its **own repository** and its **own version scheme**
(`0.x`), independent of Vesana.

## What it is (since 0.16.0, 2026-11-07)

A **read-only, vendor-curated, signed profile catalog** that Vesana instances
pull from — plus the icon library and the MIB library. There are **no uploads
from instances**, no public website, no Q&A, no votes/comments, no
notifications and no instance login any more. Questions and discussion live in
the public GitHub repo [vesana-org/community](https://github.com/vesana-org/community);
`GET /` redirects there and `robots.txt` disallows everything.

The only write path is the owner-only admin area (`/admin`, session + optional
TOTP, plus the `X-Admin-Authorization` JSON API). Existing tables (questions,
comments, votes, uploads, …) stay in the database untouched; only the code
paths are gone.

### Signing and channels

Every profile version can carry a `signed_envelope`
(`{"format": "vesana-library-sig/1", "key_id", "sig", "payload"}`), written by
an **external signing tool** — the hub never computes or checks signatures, it
stores and serves them. `released_all_at` says from when the stable channel
sees the version.

Instances send `X-Vesana-Channel: tester|stable` (missing/unknown ⇒ `stable`;
older instances send nothing). A version is **released for channel c** iff it
is signed AND (`c == tester` OR `released_all_at <= now()`). This single rule
(`app.services.profiles.is_released_for`) decides everything an instance sees;
an unsigned version is visible to nobody, a profile without a released version
is hidden for that channel. `is_current` is only a curator marker in the admin
view.

### API

| Method | Path                                                    | Purpose                                                               |
|--------|---------------------------------------------------------|-----------------------------------------------------------------------|
| GET    | `/api/v1/profiles`                                      | List; `latest_version_id`/`_tag` = newest version released for the channel |
| GET    | `/api/v1/profiles/match-rules`                          | Discovery rules of profiles with a released version                   |
| GET    | `/api/v1/profiles/{id}`                                 | Detail (check preview + changelog of the released version)            |
| GET    | `/api/v1/profiles/{id}/versions`                        | Released versions only, newest first                                  |
| GET    | `/api/v1/profiles/{id}/versions/{vid}/signed`           | The stored signed envelope (404 unless released + signed); counts a download |
| GET    | `/api/v1/profiles/{id}/versions/{vid}/bundle`           | Legacy raw bundle for older instances, same release rule              |
| GET    | `/api/v1/profile-lint`                                  | Version + fingerprint of the profile lint (Vesana compares nightly)   |
| GET    | `/api/v1/icon-library…`                                 | Icon library (read-only)                                              |
| GET    | `/api/v1/mibs…`                                         | MIB library (read-only)                                               |
| *      | `/api/v1/admin/…`, `/admin…`                            | Owner admin (profiles, instances, icons, MIBs, audit, 2FA)            |
| GET    | `/health`                                               | Liveness probe → `{status, service, version}`                         |

* **Storage** — all tables live in a dedicated Postgres schema, `community`, so
  the app can safely share the prod Postgres instance with the rest of Vesana.

## Dev quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then edit SECRET_KEY + admin credentials

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

* Pure logic tests **always run**.
* DB-dependent tests are **skipped** unless `DATABASE_URL_TEST` points at a
  reachable Postgres (CI runs a `postgres:16-alpine` service), e.g.:

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
| `SECRET_KEY`                | `dev-insecure-change-me`                   | Signs the admin session cookie                                    |
| `PORTAL_PUBLIC_KEY(_PATH)`, `API_TOKEN_TTL_DAYS`, `LOGIN_TOKEN_LEEWAY_SECONDS` | — | Unused since 0.16.0 (instance login removed); still accepted |
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
