"""Notification seam (C6).

C4 fires events at the points where C6 will eventually deliver in-app/email
notifications (new comment, new reply, moderation report). Until C6 lands this
is a no-op and there is deliberately NO notifications table.
"""

from __future__ import annotations

from typing import Any


def enqueue(event: str, **payload: Any) -> None:
    """No-op until C6 implements real notification delivery.

    TODO(C6): persist + dispatch notifications for ``event`` with ``payload``.
    """
    return None
