"""Zweiter Faktor für den Hub-Admin: TOTP (RFC 6238) + Backup-Codes.

Bewusst ohne Fremdbibliothek für den Algorithmus — HOTP/TOTP sind ein paar
Zeilen ``hmac`` + ``struct``, und so bleibt jede Zeile lesbar, die über den
Zugang zum Admin entscheidet. Nur der QR-Code kommt aus ``segno`` (reines
Python, kein PIL).

Bausteine:

* ``generate_totp_secret`` / ``otpauth_uri`` / ``qr_svg`` — Einrichtung.
* ``verify_totp`` — prüft einen Code im Fenster ±1 Schritt und liefert den
  getroffenen ZÄHLER zurück. Der Aufrufer speichert ihn als ``last_counter``:
  ein Code gilt genau einmal (Replay-Schutz — derselbe Fund wie in Vesanas
  ``two_fa_last_counter``).
* ``generate_backup_codes`` / ``hash_backup_code`` — zehn Einmal-Codes, nur
  als HMAC-Hash gespeichert.
* ``encrypt_secret`` / ``decrypt_secret`` — das TOTP-Geheimnis liegt AES-GCM-
  verschlüsselt in der Datenbank; der Schlüssel ist per HKDF aus dem
  ``SECRET_KEY`` der App abgeleitet. Ein Datenbank-Dump allein reicht damit
  nicht, um Codes zu erzeugen.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from markupsafe import Markup

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
# Wie viele Zeitschritte Uhren-Drift wir in jede Richtung tolerieren.
TOTP_WINDOW = 1

BACKUP_CODE_COUNT = 10
_BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # ohne i/l/o/0/1 — nichts zum Verwechseln


# ---- TOTP ---------------------------------------------------------------------


def generate_totp_secret() -> str:
    """Base32-Geheimnis (160 Bit), wie es Authenticator-Apps erwarten."""
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _secret_bytes(secret_b32: str) -> bytes:
    cleaned = secret_b32.strip().replace(" ", "").upper()
    padding = "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned + padding, casefold=True)


def hotp(secret_b32: str, counter: int, digits: int = TOTP_DIGITS) -> str:
    """RFC 4226."""
    digest = hmac.new(_secret_bytes(secret_b32), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def totp_counter(at: float | None = None, step: int = TOTP_STEP_SECONDS) -> int:
    return int((time.time() if at is None else at) // step)


def totp_at(secret_b32: str, at: float | None = None) -> str:
    """Der Code, der zum Zeitpunkt ``at`` gültig ist (Tests + Einrichtungs-Vorschau)."""
    return hotp(secret_b32, totp_counter(at))


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    last_counter: int | None,
    at: float | None = None,
) -> int | None:
    """Prüft ``code`` gegen das Fenster ±``TOTP_WINDOW`` Schritte.

    Liefert den getroffenen Zähler, wenn der Code passt UND dieser Zähler noch
    nicht verbraucht wurde (``> last_counter``) — sonst ``None``. Ein Code, der
    einmal angenommen wurde, ist damit auch innerhalb seiner 30 Sekunden tot.
    """
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) != TOTP_DIGITS:
        return None
    now_counter = totp_counter(at)
    for delta in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        candidate = now_counter + delta
        if last_counter is not None and candidate <= last_counter:
            continue
        if hmac.compare_digest(hotp(secret_b32, candidate), digits):
            return candidate
    return None


def otpauth_uri(secret_b32: str, account: str, issuer: str = "Vesana Community Hub") -> str:
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}"
    )


def qr_svg(data: str) -> Markup:
    """Inline-SVG des QR-Codes (kein externes Bild, CSP-konform)."""
    import segno

    qr = segno.make(data, error="m")
    # Feste Ink-Farbe auf der cremefarbenen Kachel — ein QR-Code muss in JEDEM
    # Theme dunkel auf hell sein, sonst liest ihn keine Kamera.
    # ``omitsize``: KEINE festen width/height, sondern eine viewBox. Mit festen
    # Maßen skaliert CSS nur den Rahmen, nicht den Pfad — der Code wurde
    # abgeschnitten und war für keine App lesbar (Lukas, 09/2026).
    svg = qr.svg_inline(scale=4, border=3, dark="#1F1419", light=None, svgclass="qr", omitsize=True)
    return Markup(svg)


def pretty_secret(secret_b32: str) -> str:
    """``ABCD EFGH …`` zum Abtippen."""
    return " ".join(secret_b32[i : i + 4] for i in range(0, len(secret_b32), 4))


# ---- Backup-Codes ---------------------------------------------------------------


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    out: list[str] = []
    for _ in range(count):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def normalize_backup_code(code: str) -> str:
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def hash_backup_code(code: str, pepper: str) -> str:
    """HMAC-SHA256 über den normalisierten Code; ``pepper`` = App-SECRET_KEY."""
    normalized = normalize_backup_code(code).encode()
    return hmac.new(pepper.encode("utf-8"), normalized, hashlib.sha256).hexdigest()


def consume_backup_code(code: str, hashes: list[str], pepper: str) -> list[str] | None:
    """Liefert die Liste OHNE den verbrauchten Code — oder ``None``, wenn keiner passt."""
    if not normalize_backup_code(code):
        return None
    wanted = hash_backup_code(code, pepper)
    for idx, stored in enumerate(hashes):
        if hmac.compare_digest(stored, wanted):
            return hashes[:idx] + hashes[idx + 1 :]
    return None


# ---- Verschlüsselung des Geheimnisses -------------------------------------------

_HKDF_INFO = b"vesana-community/admin-totp-secret/v1"


def _derive_key(secret_key: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        secret_key.encode("utf-8")
    )


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_derive_key(secret_key)).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt_secret(token: str, secret_key: str) -> str:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, sealed = raw[:12], raw[12:]
    return AESGCM(_derive_key(secret_key)).decrypt(nonce, sealed, None).decode("utf-8")
