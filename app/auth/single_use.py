"""Single-use enforcement for login JWTs.

The first presentation of a given ``jti`` inserts a row into
``community.used_login_tokens``. Any subsequent presentation collides on the
primary key and is treated as a replay attack.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.used_login_token import UsedLoginToken


def consume_jti(db: Session, jti: str) -> None:
    """Atomically mark ``jti`` as used.

    Raises:
        HTTPException(401): if the jti has already been consumed (replay).
    """
    db.add(UsedLoginToken(jti=jti))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="login token already used",
        ) from exc
