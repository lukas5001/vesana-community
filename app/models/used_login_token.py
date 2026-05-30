"""The ``used_login_tokens`` table — single-use enforcement for login JWTs.

Each login JWT carries a unique ``jti``. The first time it is presented we
INSERT its jti here; any later attempt collides on the primary key and is
rejected as a replay.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UsedLoginToken(Base):
    __tablename__ = "used_login_tokens"

    jti: Mapped[str] = mapped_column(String(255), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<UsedLoginToken jti={self.jti!r}>"
