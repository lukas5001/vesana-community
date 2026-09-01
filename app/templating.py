"""Shared Jinja2 templating configuration.

Lives in its own module so routers can render templates without importing
``app.main`` (which would create an import cycle). A small ``markdown_safe``
filter renders user-supplied markdown-ish text as escaped paragraphs — it
never injects raw HTML.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.requests import Request

from app.auth.csrf import csrf_token
from app.i18n import DEFAULT_LANG, LANGUAGES, label, normalize_lang, translate
from app.identity import is_real_name, public_name
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
        raw = sess.get("display_name")
        current_instance = {
            "uuid": uuid,
            "name": public_name(raw, uuid),
            "suffix": suffix,
            "is_real": is_real_name(raw),
        }

    lang = DEFAULT_LANG
    try:
        lang = normalize_lang(request.cookies.get("lang"))
    except (AttributeError, KeyError):
        pass

    def t(key: str, **kwargs) -> str:
        return translate(lang, key, **kwargs)

    def lbl(prefix: str, value: str | None) -> str:
        """Label for an enum-ish value (category, sort, filter) — falls back to the value."""
        return label(lang, prefix, value)

    def href(**overrides) -> str:
        """Current URL with some query params replaced (None removes the param)."""
        params = dict(request.query_params)
        for key, value in overrides.items():
            if value is None or value == "":
                params.pop(key, None)
            else:
                params[key] = str(value)
        query = urlencode(params)
        return request.url.path + (f"?{query}" if query else "")

    # Einmal-Meldungen (Admin): beim Rendern konsumiert, Text erst hier übersetzt.
    flashes = []
    try:
        raw_flashes = sess.pop("flash", None) or []
        for item in raw_flashes:
            if isinstance(item, dict) and item.get("key"):
                flashes.append(
                    {
                        "kind": item.get("kind", "ok"),
                        "text": t(item["key"], **(item.get("kw") or {})),
                    }
                )
    except (AssertionError, KeyError, AttributeError, TypeError):
        flashes = []

    return {
        "version": VERSION,
        "current_instance": current_instance,
        "lang": lang,
        "languages": LANGUAGES,
        "t": t,
        "lbl": lbl,
        "href": href,
        "csrf": csrf_token(request),
        "flashes": flashes,
    }


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


def fmt_dt(value) -> str:
    """``01.09.2026 08:15`` (UTC) — für Protokoll und Listen."""
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def fmt_date(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y")


def rel_time(value, lang: str = DEFAULT_LANG) -> str:
    """„vor 3 Std." / „3 h ago" — grob, für den Aktivitätsstrom."""
    from datetime import UTC, datetime

    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - value).total_seconds()))
    if seconds < 60:
        return translate(lang, "time.now")
    minutes = seconds // 60
    if minutes < 60:
        return translate(lang, "time.min", n=minutes)
    hours = minutes // 60
    if hours < 48:
        return translate(lang, "time.h", n=hours)
    days = hours // 24
    if days < 60:
        return translate(lang, "time.d", n=days)
    return fmt_date(value)


templates.env.filters["dt"] = fmt_dt
templates.env.filters["d"] = fmt_date
templates.env.filters["ago"] = rel_time
