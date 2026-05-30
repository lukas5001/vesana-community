# Portal endpoint: `POST /api/v1/community/issue-login-token`

> **Where this lives:** the **`vesana-license-portal`** repo, **not** here and
> **not** in Vesana. This file is the drop-in reference so it can be added with
> the portal's own conventions. The community app already verifies what this
> endpoint signs (see `app/auth/tokens.py::verify_login_jwt`).

## Why the portal signs (not the Vesana instance)

community.vesana.org must trust exactly **one** signer. If each Vesana instance
signed its own login token, the community would have to trust every instance's
key — any leaked instance key forges any identity. The **licence portal is the
single trust anchor**: it already knows every legitimate instance (phone-home),
so it signs, and the community verifies with the portal's **public** key only.

## Contract (must match the community verifier)

`app/auth/tokens.py::verify_login_jwt` in this repo requires:

- **alg**: `EdDSA` (Ed25519). Pinned — no alg confusion possible.
- **iss**: `vesana-licence-portal`
- required claims: `exp`, `iat`, `sub`, `jti`, `iss`
- `sub` = `instance_uuid`
- optional: `display_name`, `avatar_b64`
- **TTL**: 5 minutes. **Single-use**: the community records each `jti` and
  rejects replays, so a fresh `jti` per call is mandatory.

## Endpoint

```python
# vesana-license-portal: app/routers/community.py  (or wherever portal routers live)
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/community", tags=["community"])

LOGIN_TOKEN_TTL = timedelta(minutes=5)
LOGIN_ISSUER = "vesana-licence-portal"

# Load ONCE at startup. The PEM produced by scripts/gen_portal_keypair.py.
# Keep the path in the portal's settings/.env, never in git.
_PORTAL_PRIVATE_PEM = Path(
    "/opt/vesana-license-portal/secrets/portal_ed25519_private.pem"
).read_text(encoding="utf-8")


class IssueLoginTokenBody(BaseModel):
    instance_uuid: str


class IssueLoginTokenResponse(BaseModel):
    token: str
    expires_in: int  # seconds


@router.post("/issue-login-token", response_model=IssueLoginTokenResponse)
def issue_login_token(
    body: IssueLoginTokenBody,
    # >>> INTEGRATION POINT (portal-specific): authenticate the CALLER as the
    # instance it claims to be, using the SAME mechanism phone-home already uses
    # (license key / shared instance secret / mTLS). Do NOT issue a token for an
    # instance_uuid the caller hasn't proven it owns.
    caller=Depends(...),  # replace with the portal's instance-auth dependency
) -> IssueLoginTokenResponse:
    instance = lookup_instance(body.instance_uuid)  # portal's existing model
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown instance")
    if instance.is_blocked or instance.is_suspended:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "instance is blocked")
    # Defence in depth: the authenticated caller must match the requested uuid.
    if caller.instance_uuid != body.instance_uuid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "instance mismatch")

    now = datetime.now(UTC)
    payload = {
        "iss": LOGIN_ISSUER,
        "sub": instance.uuid,
        "display_name": instance.community_display_name or f"instanz-{instance.uuid[:8]}",
        "jti": str(uuid.uuid4()),  # fresh per call — community enforces single-use
        "iat": int(now.timestamp()),
        "exp": int((now + LOGIN_TOKEN_TTL).timestamp()),
    }
    # avatar_b64 is optional; include the (<=~20KB) base64 WebP if the portal has it.
    if getattr(instance, "avatar_b64", None):
        payload["avatar_b64"] = instance.avatar_b64

    token = jwt.encode(payload, _PORTAL_PRIVATE_PEM, algorithm="EdDSA")
    return IssueLoginTokenResponse(token=token, expires_in=int(LOGIN_TOKEN_TTL.total_seconds()))
```

## Vesana-instance side (small addition to the Vesana repo)

When the user clicks **Community Hub** in the avatar menu (already wired to open
`community.vesana.org`), the Vesana **backend** should, just-in-time:

1. `POST {LICENCE_PORTAL_URL}/api/v1/community/issue-login-token {instance_uuid}`
   (authenticated the same way phone-home authenticates).
2. Receive `{token}` and either:
   - **Browser SSO**: redirect the new tab to
     `https://community.vesana.org/auth?token=<token>` (community sets the
     session cookie and redirects to `/`), **or**
   - **Background/API**: `POST https://community.vesana.org/api/v1/auth/exchange
     {token}` → store the returned long-lived `api_token` in
     `system_settings.community_api_token` for Notifications-poll / import / upload.

   Do both: exchange for the api_token in the background AND hand the browser a
   token for the seamless tab open. (Two separate `issue-login-token` calls, each
   with its own single-use `jti`.)

3. **Graceful degradation**: if the portal or community is unreachable, fail
   silently — the avatar menu still opens the URL; the user just isn't pre-logged-in.

`system_settings` keys to add on the Vesana side (all with safe defaults, no new
mandatory env): `community_api_token`, `community_api_token_expires_at`,
`community_display_name` (default `instanz-{uuid[:8]}`), `community_url`
(default `https://community.vesana.org`).

## Token lifetimes recap

| Token | Signer | Alg | TTL | Single-use | Stored where |
|---|---|---|---|---|---|
| Login JWT | portal (private) | EdDSA | 5 min | yes (`jti`) | nowhere — transient |
| API token | community (`SECRET_KEY`) | HS256 | 30 d (refreshable) | no | Vesana `system_settings.community_api_token` |
