# Changelog

## 0.3.0 — Voting & comments

Added voting and one-level threaded comments for community profiles.

### Highlights

- Unified `votes` table for profiles and comments: one vote per instance per target, re-voting upserts (no duplicates), `±1` only.
- Cached `vote_score` on profiles and comments, recomputed in the same transaction as every vote change.
- Threaded comments (`profile_comments`, exactly one reply level): create, edit/delete your own within 24h, soft delete keeps thread structure.
- "Helpful" tag set by the profile uploader or an admin; helpful comments sort to the top.
- Optional, private downvote reasons (stored, never exposed in public views).
- `moderation_reports` table + report endpoint (consumed by C8).
- Comment thread server-rendered on the profile detail page.
- Notification seam (`app/services/notifications.py`, no-op until C6).
- Alembic migration `0003_voting_comments`; bumped to 0.3.0.

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
