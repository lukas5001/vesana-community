# Changelog

## 0.7.0 — Admin panel

Added the community admin panel (C8): a server-rendered HTML console plus a
matching JSON API for review, moderation, instances, approvals and stats.

### Highlights

- HTML admin at `/admin` (`/admin/review`, `/admin/moderation`,
  `/admin/instances`, `/admin/profiles`) — gated by `require_admin` HTTP Basic
  (the app's OWN `.env` creds `COMMUNITY_ADMIN_USER` / `COMMUNITY_ADMIN_PASSWORD`,
  NOT instance SSO), so the browser shows a Basic-auth prompt. Each section is
  themed via CSS tokens + `color-mix` (no bare `color:#fff`) and fully escaped;
  attacker-influenced content (report reasons, script findings, names) is never
  marked safe.
- Dashboard with overview stats: instances (total / blocked), profiles (total,
  by tier, pending), downloads/imports totals, votes, questions (total / open),
  open reports and events.
- Review-Queue page reuses the C3 review service (`list_for_review` / `approve` /
  `reject`) — script findings + `has_scripts` shown prominently; HTML forms post
  to `require_admin` handlers that 303 back.
- Moderation: open reports with a SAFE target preview (a removed comment shows
  `[entfernt]`; no downvote reasons or secrets are ever leaked). Resolve via
  `dismiss` (status → `dismissed`) or `remove` (acts on the target — comment
  `is_removed`, question `is_closed` + `closed_reason='removed by moderator'`,
  answer deleted, profile `is_removed` — and status → `resolved`).
- Instances: list active instances with their uploaded-profile count;
  block/unblock toggles `is_blocked` (the auth layer already rejects blocked
  instances on every request).
- Profiles: promote a beta/community profile to `official` (also sets `approved`
  + `review_status='approved'` + `approved_by='admin'`).
- AdminFlag-gated JSON API (`X-Admin-Authorization` Basic, same seam as C3/C4):
  `GET /api/v1/admin/reports`, `POST /api/v1/admin/reports/{id}/resolve`,
  `GET /api/v1/admin/instances`, `POST /api/v1/admin/instances/{uuid}/block`,
  `POST /api/v1/admin/profiles/{id}/promote`, `GET /api/v1/admin/stats`. The
  existing C3 review-queue endpoints are reused, not redefined. Every endpoint
  returns 401 without the admin header.
- NO new migration: the moderation `status` column (`open` → `resolved` /
  `dismissed`) is reused as the resolution state, and every stat reuses existing
  columns. Migrations `0001`–`0006` are untouched.

## 0.6.0 — Community notifications (events + poll)

- New `community_events` table (migration `0006_community_events`) and
  `CommunityEvent` model: a per-instance notification feed. `instance_uuid` is
  the recipient; `payload_json` (JSONB) holds only small render data.
- `app.services.notifications.enqueue` now inserts a real event in the SAME
  transaction as the triggering action; it skips self-notifications
  (`recipient == actor`) and missing recipients (e.g. official/beta profiles
  with no uploader).
- Emit events for: a comment on a profile (`profile_comment`), a reply to a
  comment (`comment_reply`), a new answer (`qa_answer`), an accepted answer
  (`answer_accepted`), and upload approve/reject (`profile_approved` /
  `profile_rejected`).
- New endpoints: `GET /api/v1/notifications` (`?unread_only`, `?limit`, plus an
  `unread_count`) and `POST /api/v1/notifications/mark-read` (`{ids: [...]}` or
  `{all: true}`). Every query is scoped to the caller's own instance — a caller
  can never read or mark another instance's events.
- Payloads carry only non-sensitive render data (ids + display strings); never
  secrets, tokens, or downvote reasons.

## 0.5.0 — Community upload + review queue

Added self-hoster profile uploads with a heuristic script-gate, immediate
"waiting for review" visibility, owner-scoped versioning and an admin review
queue.

### Highlights

- `POST /api/v1/profiles/upload` (Bearer auth): an instance uploads a Vesana
  profile bundle (`schema_version` 1). Creates a `tier='community'` profile with
  `review_status='pending'` (immediately visible, badged) plus a current version
  row, or — when the SAME uploader re-uploads a profile with the same
  (name, vendor) — adds a new immutable version, flips `is_current`, updates
  `latest_version_id`/`updated_at` and resets `review_status` to `pending`.
- MODERATE-BEFORE-SHOW = NO: community uploads are visible right away with a
  "🔄 Warte auf Review" badge until approved; only `rejected` + removed profiles
  are hidden. `review_status` ('pending' | 'approved' | 'rejected') is the source
  of truth; the legacy `approved` bool is mirrored from it.
- Upload constraints: `schema_version` must be 1 (400), `profile.name` required
  (400), serialized bundle ≤ 500KB (413); re-using a `version_tag` on a profile
  you already own is a 409 (versions are immutable).
- Heuristic script-gate (NOT a sandbox): flags `has_scripts` when a check
  references a script via `check_config.script_id`, and records `script_findings`
  for dangerous markers (`rm -rf`, `Invoke-Expression`, `curl | bash`, `eval(`,
  `os.system`, …) found in any check_config string or a top-level `scripts` list.
- Admin review queue (`X-Admin-Authorization` Basic): `GET /api/v1/admin/
  review-queue` (pending by default, `?status=all|approved|rejected`),
  `POST /api/v1/admin/review/{id}/approve`,
  `POST /api/v1/admin/review/{id}/reject` (stores `rejection_reason`).
- Browse + detail surface `review_status` + `has_scripts`: a pending badge, a
  persistent community warning ("Scripts laufen auf deinen Agents/Collectoren;
  nicht von Vesana geprüft") and an extra script-findings note. Themed via
  CSS tokens + `color-mix` (no bare `color: #fff`), escaped.
- Notification seam fires `profile.approved` / `profile.rejected` (no-op until
  C6); no notifications table.
- Alembic migration `0005_upload_review` (adds `review_status`,
  `rejection_reason`, `has_scripts`, `script_findings`); bumped to 0.5.0.

## 0.4.0 — Q&A portal

Added a community Q&A portal: questions and answers with voting, accepted
answers, similar-question search and profile linking.

### Highlights

- `questions` + `answers` tables (Alembic `0004_qa_portal`), both with cached
  `vote_score` recomputed via the unified `votes` table (`target_type`
  `question` / `answer`).
- Questions carry a cached `answer_count` (recomputed on answer create/delete),
  optional `tags`, an optional `profile_id` link (SET NULL on profile delete),
  and a self-referential `duplicate_of_id`.
- Voting on questions and answers reuses the unified votes service (one vote per
  instance per target, re-voting upserts, `±1` only).
- Exactly one answer per question may be accepted: only the question author may
  accept; accepting flips any previously accepted answer false in one
  transaction, also guarded by a Postgres partial unique index
  (`uq_answers_one_accepted`).
- Answers sort accepted-first, then by `vote_score`, then oldest first.
- Similar-question search (`GET /api/v1/questions/similar?title=`, ILIKE top 5).
- Filters (open / answered / unanswered / accepted / tag) + search over
  title/body/tags + sort (newest / votes / active).
- Admin-only duplicate close: closed questions stay visible but accept no new
  answers (409). Reporting questions/answers reuses `moderation_reports`.
- "Vesana Team" badge stamped on a question/answer only when the author posts
  with valid admin credentials (`X-Admin-Authorization`); never self-settable.
- Server-rendered, read-only pages: `/questions` (list + filters + search +
  sort, closed + Vesana-Team badges) and `/questions/{id}` (escaped markdown,
  accepted-first answers with ✅ badge, closed/duplicate notice). The profile
  detail page gains a "Zugehörige Fragen" section.
- Notification seam fires `answer.created` / `answer.accepted` (no-op until C6).
- Alembic migration `0004_qa_portal`; bumped to 0.4.0.

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
