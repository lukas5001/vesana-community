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

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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
