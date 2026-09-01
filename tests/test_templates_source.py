"""Quell-Anker für den Register-Neubau (09/2026).

Prüft die Dinge, die beim Ist-Zustand schiefgingen und die kein Funktionstest
sieht: fehlende Übersetzungen (fielen still auf den Schlüssel zurück), ein
Icon-Slug als Text, hart kodiertes Englisch, fremde Farben, Schriften ohne
Datei.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.i18n import TRANSLATIONS

ROOT = Path(__file__).resolve().parents[1] / "app"
TEMPLATES = sorted((ROOT / "templates").rglob("*.html"))
CSS_RAW = (ROOT / "static" / "css" / "community.css").read_text(encoding="utf-8")
CSS = re.sub(r"/\*.*?\*/", "", CSS_RAW, flags=re.S)  # Kommentare zählen nicht

_T_CALL = re.compile(r"""\bt\(\s*['"]([a-z0-9_.]+)['"]\s*[,)]""")


def _template_keys() -> set[str]:
    keys: set[str] = set()
    for path in TEMPLATES:
        keys.update(_T_CALL.findall(path.read_text(encoding="utf-8")))
    return keys


def test_every_template_key_exists_in_both_languages() -> None:
    """Ein fehlender Schlüssel würde als ``detail.foo`` auf der Seite stehen."""
    keys = _template_keys()
    assert keys, "keine t()-Aufrufe gefunden — Regex kaputt?"
    for lang in ("de", "en"):
        missing = sorted(k for k in keys if k not in TRANSLATIONS[lang])
        assert not missing, f"{lang}: fehlende Übersetzungen: {missing}"


def test_dynamic_label_keys_exist_for_known_values() -> None:
    """lbl('cat'|'sort'|'qsort'|'qfilter', wert) hat für jeden bekannten Wert einen Text."""
    from app.services.profiles import SORT_OPTIONS
    from app.services.qa import FILTER_OPTIONS
    from app.services.qa import SORT_OPTIONS as QA_SORT

    expected = {f"sort.{o}" for o in SORT_OPTIONS}
    expected |= {f"qsort.{o}" for o in QA_SORT}
    expected |= {f"qfilter.{o}" for o in FILTER_OPTIONS}
    for lang in ("de", "en"):
        missing = sorted(k for k in expected if k not in TRANSLATIONS[lang])
        assert not missing, f"{lang}: {missing}"


def test_translation_tables_are_symmetric() -> None:
    de, en = set(TRANSLATIONS["de"]), set(TRANSLATIONS["en"])
    assert de == en, f"nur de: {sorted(de - en)} · nur en: {sorted(en - de)}"


def test_no_hardcoded_theme_on_html_root() -> None:
    """Hell ist Standard, Dunkel kommt vom System oder vom Umschalter — nie fest."""
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'data-theme="dark"' not in base
    assert 'data-theme="light"' not in base


def test_icon_slug_is_rendered_as_symbol_not_text() -> None:
    """Der alte Fehler: ``{{ p.icon }}`` stand als Überschrift auf jeder Karte."""
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        assert "{{ p.icon }}" not in text and "profile.icon }}" not in text, path.name
    tile = (ROOT / "templates" / "_tile.html").read_text(encoding="utf-8")
    assert 'href="#i-{{ p.icon_slug }}"' in tile


def test_question_page_has_no_hardcoded_english() -> None:
    """question.html war komplett englisch hart kodiert."""
    text = (ROOT / "templates" / "question.html").read_text(encoding="utf-8")
    for phrase in (
        "Back to questions",
        "Your answer",
        "Post answer",
        "No answers yet",
        "Related profile",
    ):
        assert phrase not in text, phrase


def test_css_uses_brand_tokens_only() -> None:
    """Kein fremdes Blau, kein hartes Weiß auf Marken-Flächen."""
    assert "#5b8cff" not in CSS.lower()
    assert re.search(r"color\s*:\s*(#fff\b|#ffffff\b|white\b)", CSS, re.I) is None
    for token in ("--brand:#6B1226", "--on-brand:#F5EDE0", "'Geist'", "'Inter'", "'Geist Mono'"):
        assert token in CSS, token


def test_every_font_face_file_exists() -> None:
    """Eine fehlende woff2 fällt still auf die Systemschrift zurück."""
    files = re.findall(r"url\('/static/fonts/([^']+)'\)", CSS)
    assert files, "keine @font-face-Quellen gefunden"
    for name in files:
        assert (ROOT / "static" / "fonts" / name).is_file(), name


def test_no_inline_scripts_or_styles_in_templates() -> None:
    """Die CSP erlaubt nur externe Dateien — ein Inline-Handler wäre still tot."""
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        assert re.search(r"<script(?![^>]*\bsrc=)", text) is None, path.name
        assert re.search(r"\son(click|submit|change|input)=", text, re.I) is None, path.name
        # CSP style-src 'self': ein Inline-style-Attribut wird im Browser still
        # verworfen (Abstände weg, Balken ohne Breite) — live gesehen 09/2026.
        assert 'style="' not in text, f"{path.name}: Inline-Style"
