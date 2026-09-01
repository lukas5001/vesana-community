"""Zweiter Faktor des Hub-Admins — Zustand in ``admin_accounts``.

Der Ablauf beim Einrichten: ``begin_setup`` erzeugt ein Geheimnis, das NUR in
der Session liegt; erst ``confirm_setup`` (erster gültiger Code) schreibt es
verschlüsselt in die Datenbank und schaltet 2FA scharf. So kann eine
abgebrochene Einrichtung den Admin nie aussperren.

``verify_second_factor`` akzeptiert einen TOTP-Code (mit Replay-Schutz über
``totp_last_counter``) ODER einen Backup-Code (wird verbraucht).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.auth import admin_security as sec
from app.config import Settings
from app.models.admin_account import AdminAccount


def get_account(db: Session, username: str) -> AdminAccount | None:
    return db.get(AdminAccount, username)


def get_or_create(db: Session, username: str) -> AdminAccount:
    account = db.get(AdminAccount, username)
    if account is None:
        account = AdminAccount(username=username)
        db.add(account)
        db.flush()
    return account


def two_fa_enabled(db: Session, username: str) -> bool:
    account = db.get(AdminAccount, username)
    return bool(account is not None and account.totp_enabled and account.totp_secret_enc)


def begin_setup(username: str, settings: Settings) -> dict[str, str]:
    """Neues Geheimnis + otpauth-URI — noch NICHT gespeichert (nur Session)."""
    secret = sec.generate_totp_secret()
    uri = sec.otpauth_uri(secret, account=username, issuer=_issuer(settings))
    return {"secret": secret, "uri": uri}


def _issuer(settings: Settings) -> str:
    host = settings.COMMUNITY_BASE_URL.split("//", 1)[-1].split("/", 1)[0]
    return f"Vesana Community Hub ({host})" if host else "Vesana Community Hub"


def confirm_setup(
    db: Session, username: str, secret: str, code: str, settings: Settings
) -> list[str] | None:
    """Erster Code stimmt ⇒ 2FA aktiv, Backup-Codes zurück (einmalig im Klartext)."""
    counter = sec.verify_totp(secret, code, last_counter=None)
    if counter is None:
        return None
    account = get_or_create(db, username)
    account.totp_secret_enc = sec.encrypt_secret(secret, settings.SECRET_KEY)
    account.totp_enabled = True
    account.totp_confirmed_at = datetime.now(UTC)
    account.totp_last_counter = counter
    codes = sec.generate_backup_codes()
    account.backup_codes = [sec.hash_backup_code(c, settings.SECRET_KEY) for c in codes]
    account.backup_codes_generated_at = datetime.now(UTC)
    db.flush()
    return codes


def regenerate_backup_codes(db: Session, username: str, settings: Settings) -> list[str]:
    account = get_or_create(db, username)
    codes = sec.generate_backup_codes()
    account.backup_codes = [sec.hash_backup_code(c, settings.SECRET_KEY) for c in codes]
    account.backup_codes_generated_at = datetime.now(UTC)
    db.flush()
    return codes


def disable(db: Session, username: str) -> None:
    account = db.get(AdminAccount, username)
    if account is None:
        return
    account.totp_secret_enc = None
    account.totp_enabled = False
    account.totp_confirmed_at = None
    account.totp_last_counter = None
    account.backup_codes = None
    account.backup_codes_generated_at = None
    db.flush()


@dataclass
class FactorResult:
    ok: bool
    method: str | None = None  # "totp" | "backup"
    backup_codes_left: int | None = None


def verify_second_factor(db: Session, username: str, code: str, settings: Settings) -> FactorResult:
    """TOTP-Code ODER Backup-Code. Bei Erfolg wird der Zähler bzw. der Code verbraucht."""
    account = db.get(AdminAccount, username)
    if account is None or not account.totp_enabled or not account.totp_secret_enc:
        return FactorResult(ok=False)
    raw = (code or "").strip()

    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == sec.TOTP_DIGITS and len(raw.replace(" ", "")) == sec.TOTP_DIGITS:
        secret = sec.decrypt_secret(account.totp_secret_enc, settings.SECRET_KEY)
        counter = sec.verify_totp(secret, digits, last_counter=account.totp_last_counter)
        if counter is not None:
            account.totp_last_counter = counter
            db.flush()
            return FactorResult(ok=True, method="totp")
        return FactorResult(ok=False)

    remaining = sec.consume_backup_code(raw, list(account.backup_codes or []), settings.SECRET_KEY)
    if remaining is None:
        return FactorResult(ok=False)
    account.backup_codes = remaining
    db.flush()
    return FactorResult(ok=True, method="backup", backup_codes_left=len(remaining))


def status(db: Session, username: str) -> dict:
    account = db.get(AdminAccount, username)
    if account is None:
        return {"enabled": False, "since": None, "backup_left": 0, "backup_generated": None}
    return {
        "enabled": bool(account.totp_enabled and account.totp_secret_enc),
        "since": account.totp_confirmed_at,
        "backup_left": account.backup_codes_left,
        "backup_generated": account.backup_codes_generated_at,
    }
