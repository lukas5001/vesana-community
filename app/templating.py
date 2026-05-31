"""Shared Jinja2 templating configuration.

Lives in its own module so routers can render templates without importing
``app.main`` (which would create an import cycle). A small ``markdown_safe``
filter renders user-supplied markdown-ish text as escaped paragraphs — it
never injects raw HTML.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.requests import Request

from app.version import VERSION

BASE_DIR = Path(__file__).resolve().parent


def _global_context(request: Request) -> dict:
    """Inject values every template (esp. base.html nav + footer) needs.

    ``current_instance`` is read from the signed session cookie only (no DB
    hit): it is the logged-in instance's {uuid, name} or ``None`` for anonymous
    visitors. ``version`` powers the footer. Per-route context still wins on
    key collisions.
    """
    current_instance = None
    try:
        sess = request.session
    except (AssertionError, KeyError, AttributeError):
        sess = {}
    uuid = sess.get("instance_uuid")
    if uuid:
        # Short, stable suffix for disambiguation when two users pick the same
        # name (think Discord's #1234). Derived from the instance uuid.
        suffix = uuid.replace("-", "")[:4]
        current_instance = {
            "uuid": uuid,
            "name": sess.get("display_name") or uuid,
            "suffix": suffix,
        }
    return {"version": VERSION, "current_instance": current_instance}


templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates"),
    context_processors=[_global_context],
)


def markdown_safe(text: str | None) -> Markup:
    """Render text as escaped HTML paragraphs.

    This is NOT a real markdown engine: it escapes everything first (no raw
    HTML injection) and only turns blank-line-separated blocks into <p> and
    single newlines into <br>. Safe for untrusted uploader content.
    """
    if not text:
        return Markup("")
    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n")]
    html_blocks = []
    for block in blocks:
        if not block:
            continue
        escaped = escape(block).replace("\n", "<br>")
        html_blocks.append(f"<p>{escaped}</p>")
    return Markup("\n".join(html_blocks))


templates.env.filters["markdown_safe"] = markdown_safe
