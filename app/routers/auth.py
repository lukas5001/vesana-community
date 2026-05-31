"""Authentication endpoints.

Three entry points share one core flow (verify portal login JWT -> enforce
single-use -> upsert Instance):

* ``GET  /auth?token=...``            — browser SSO; sets a signed session cookie.
* ``POST /api/v1/auth/exchange``      — machine-to-machine; returns an API token.
* ``POST /api/v1/auth/refresh``       — exchange a still-valid API token for a
                                        fresh one (Bearer auth).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_instance
from app.auth.single_use import consume_jti
from app.auth.tokens import TokenError, issue_api_token, verify_login_jwt
from app.config import Settings, get_settings
from app.db import get_db
from app.models.instance import Instance

router = APIRouter(tags=["auth"])


def _verify_and_upsert(token: str, db: Session, settings: Settings) -> Instance:
    """Verify a login JWT, enforce single-use, and upsert the Instance row.

    Returns the (created or updated) Instance. Raises HTTPException(401) on any
    verification or replay failure.
    """
    try:
        claims = verify_login_jwt(token, settings=settings)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    # Single-use: this raises 401 on replay BEFORE we mutate any instance state.
    consume_jti(db, claims["jti"])

    instance_uuid = claims["sub"]
    display_name = claims.get("display_name") or instance_uuid
    avatar_b64 = claims.get("avatar_b64")
    now = datetime.now(UTC)

    instance = db.get(Instance, instance_uuid)
    if instance is None:
        instance = Instance(
            uuid=instance_uuid,
            display_name=display_name,
            avatar_data=avatar_b64,
            joined_at=now,
            last_seen_at=now,
            is_blocked=False,
        )
        db.add(instance)
    else:
        instance.display_name = display_name
        if avatar_b64 is not None:
            instance.avatar_data = avatar_b64
        instance.last_seen_at = now

    db.commit()
    db.refresh(instance)
    return instance


@router.get("/auth")
def auth_sso(
    token: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    """Browser SSO: verify the login JWT, set the session cookie, redirect to /."""
    instance = _verify_and_upsert(token, db, settings)
    request.session["instance_uuid"] = instance.uuid
    # Cache the EFFECTIVE display name (user-chosen, else SSO) in the session so
    # the HTML nav can show who is logged in without a DB hit on every render.
    request.session["display_name"] = instance.effective_name
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout(request: Request) -> RedirectResponse:
    """Clear the session cookie and return to the browse page."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/v1/auth/exchange")
def auth_exchange(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str, Body(embed=True)],
) -> dict:
    """Machine-to-machine: trade a login JWT for a long-lived API token."""
    instance = _verify_and_upsert(token, db, settings)
    api_token, expires_at = issue_api_token(instance.uuid, settings=settings)
    return {"api_token": api_token, "expires_at": expires_at.isoformat()}


@router.post("/api/v1/auth/refresh")
def auth_refresh(
    instance: Annotated[Instance, Depends(get_current_instance)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Exchange a still-valid API token for a fresh one.

    ``get_current_instance`` already verified the Bearer token and rejected
    blocked instances, so issuing a new token here is safe.
    """
    api_token, expires_at = issue_api_token(instance.uuid, settings=settings)
    return {"api_token": api_token, "expires_at": expires_at.isoformat()}
