# Deploying community.vesana.org (prod VPS)

> Outward-facing infra. These steps touch DNS, nginx, the shared Postgres and a
> new secret keypair — they need an explicit go-ahead and are listed here so
> they can be done in one sitting. Code is already CI-gated in this repo.

Prod VPS: `82.165.251.29` · path `/opt/vesana-community/` · shared Postgres 17
(native, user `vesana`). SSH via the Bitwarden agent:
`SSH_AUTH_SOCK=/home/lukas/.bitwarden-ssh-agent.sock ssh root@82.165.251.29`.

## 1. Secrets — Ed25519 keypair (one-time)

```bash
# locally, in this repo
python scripts/gen_portal_keypair.py --out-dir ./secrets
# -> secrets/portal_ed25519_private.pem  (licence portal ONLY)
# -> secrets/portal_ed25519_public.pem   (community app)
```

- Install the **private** PEM on the licence portal
  (`/opt/vesana-license-portal/secrets/`, chmod 600) and add the
  `issue-login-token` endpoint (`docs/portal-issue-login-token.md`).
- Install the **public** PEM on the community app and point
  `PORTAL_PUBLIC_KEY_PATH` at it.
- Generate the community `SECRET_KEY`: `python -c "import secrets;print(secrets.token_urlsafe(48))"`.

## 2. Postgres schema (shared instance, dedicated `community` schema)

The app keeps everything in schema `community`; Alembic creates it. Give the
`vesana` DB user rights, then run migrations from the container:

```bash
sudo -u postgres psql -d vesana -c 'CREATE SCHEMA IF NOT EXISTS community AUTHORIZATION vesana;'
# migrations run via the container entrypoint, or manually:
docker compose -f /opt/vesana-community/docker-compose.yml run --rm community alembic upgrade head
```

(Use the same DB `vesana`; `DATABASE_URL=postgresql+psycopg://vesana:vesana_prod_2026@<pg-host>:5432/vesana`.
On the prod box Postgres is native, so the community container reaches it via the
host gateway / host network — mirror how the licence portal connects.)

## 3. `.env` (in `/opt/vesana-community/`)

```ini
DATABASE_URL=postgresql+psycopg://vesana:vesana_prod_2026@<pg-host>:5432/vesana
SECRET_KEY=<token_urlsafe(48)>
PORTAL_PUBLIC_KEY_PATH=/app/secrets/portal_ed25519_public.pem
API_TOKEN_TTL_DAYS=30
LOGIN_TOKEN_LEEWAY_SECONDS=30
COMMUNITY_ADMIN_USER=lukas
COMMUNITY_ADMIN_PASSWORD=<strong-distinct-from-portal>
COMMUNITY_BASE_URL=https://community.vesana.org
```

Mount the public PEM into the container at `/app/secrets/portal_ed25519_public.pem`
(compose volume) — never bake secrets into the image.

## 4. Deploy

```bash
mkdir -p /opt/vesana-community && cd /opt/vesana-community
git clone git@github.com:lukas5001/vesana-community.git .
# place .env and secrets/portal_ed25519_public.pem
docker compose up -d --build
docker compose run --rm community alembic upgrade head
docker compose run --rm community python -m app.seed   # official/beta starter profiles
```

## 5. nginx vhost + TLS (`community.vesana.org`)

Add a server block proxying to the container (`:8080`), then Let's Encrypt:

```nginx
server {
    server_name community.vesana.org;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
certbot --nginx -d community.vesana.org
```

When TLS is on, set the session cookie to secure: flip `https_only=True` in
`app/main.py`'s `SessionMiddleware` (or make it env-driven) before/at this step.

## 6. DNS

`community.vesana.org` → A record → `82.165.251.29` (same as vesana.org).

## 7. gh token for PRs/releases

This repo has its own fine-grained PAT (Bitwarden entry **"Vesana-Community"**).
`git push` works over SSH already; only the `gh` CLI needs the token:

```bash
printf '%s\n' "<TOKEN>" | gh auth login --with-token --hostname github.com
```

The general Vesana-Tasks token has **no** access to this repo.

## Checklist (outward-facing — needs go-ahead)

- [ ] Ed25519 keypair generated; private → portal, public → community
- [ ] `issue-login-token` endpoint added to vesana-license-portal (+ Vesana-side call)
- [ ] `community` schema created + `alembic upgrade head`
- [ ] `.env` + public-PEM volume in place
- [ ] `docker compose up -d` healthy (`/health` returns ok)
- [ ] seed run (official/beta profiles visible)
- [ ] nginx vhost + Let's Encrypt cert; `https_only=True`
- [ ] DNS A record
- [ ] community gh PAT activated for PRs/releases
