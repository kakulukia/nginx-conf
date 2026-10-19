from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from nginx_conf.store import AppDatabase

SESSION_COOKIE_NAME = "nginx_conf_session"
SESSION_TTL_DAYS = 30
ENROLLMENT_COOKIE_NAME = "nginx_conf_enrollment"
ENROLLMENT_TTL_SECONDS = 600
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_LOCK_SECONDS = 60
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
LOGIN_ERROR = "Die Anmeldedaten sind ungültig."


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class EnrollmentRequired:
    token: str
    password_change_required: bool = False


def configure_password(db: AppDatabase, *, password: str, replace: bool = False) -> None:
    if not 12 <= len(password) <= 1024:
        raise AuthError("Das Passwort muss zwischen 12 und 1024 Zeichen lang sein.")
    db.save_auth_credentials(
        password_hash=PASSWORD_HASHER.hash(password),
        totp_secret="",
        last_totp_step=-1,
        replace=replace,
        password_change_required=True,
    )


def matching_totp_step(secret: str, code: str, *, now: int | None = None) -> int | None:
    code = code.strip().replace(" ", "")
    if re.fullmatch(r"[0-9]{6}", code) is None:
        return None
    step = int(time.time() if now is None else now) // 30
    totp = pyotp.TOTP(secret)
    for offset in (0, -1, 1):
        candidate = step + offset
        if candidate >= 0 and secrets.compare_digest(totp.at(candidate * 30), code):
            return candidate
    return None


def login(db: AppDatabase, *, password: str, code: str, now: int | None = None) -> str | EnrollmentRequired:
    now = int(time.time() if now is None else now)
    credentials = db.reserve_login_attempt(now=now, limit=LOGIN_ATTEMPT_LIMIT, lock_seconds=LOGIN_LOCK_SECONDS)
    if credentials is None or not password or len(password) > 1024:
        raise AuthError(LOGIN_ERROR)
    try:
        PASSWORD_HASHER.verify(credentials.password_hash, password)
    except (VerificationError, InvalidHashError) as error:
        raise AuthError(LOGIN_ERROR) from error
    if not credentials.totp_secret:
        token = secrets.token_urlsafe(32)
        if not db.create_enrollment(
            token_hash=_hash_token(token),
            password_hash=credentials.password_hash,
            secret=pyotp.random_base32(),
            expires_at=now + ENROLLMENT_TTL_SECONDS,
            now=now,
        ):
            raise AuthError(LOGIN_ERROR)
        return EnrollmentRequired(token, credentials.password_change_required)
    step = matching_totp_step(credentials.totp_secret, code, now=now)
    if step is None:
        raise AuthError(LOGIN_ERROR)
    token = secrets.token_urlsafe(32)
    expires_at = _session_expiry(now)
    if not db.complete_login(credentials, totp_step=step, session_hash=_hash_token(token), expires_at=expires_at):
        raise AuthError(LOGIN_ERROR)
    return token


def enrollment_secret(db: AppDatabase, token: str | None, *, now: int | None = None) -> str | None:
    if not token:
        return None
    return db.get_enrollment_secret(_hash_token(token), now=int(time.time() if now is None else now))


def password_change_required(db: AppDatabase, token: str | None, *, now: int | None = None) -> bool:
    return bool(token and db.get_password_change_hash(_hash_token(token), now=int(time.time() if now is None else now)))


def set_initial_password(
    db: AppDatabase, *, token: str | None, password: str, confirmation: str, now: int | None = None
) -> str:
    now = int(time.time() if now is None else now)
    old_hash = db.get_password_change_hash(_hash_token(token), now=now) if token else None
    if old_hash is None:
        raise AuthError("Die Einrichtung ist abgelaufen. Bitte erneut mit deinem Passwort anmelden.")
    if not 12 <= len(password) <= 1024:
        raise AuthError("Das Passwort muss zwischen 12 und 1024 Zeichen lang sein.")
    if password != confirmation:
        raise AuthError("Die Passwörter stimmen nicht überein.")
    try:
        PASSWORD_HASHER.verify(old_hash, password)
    except VerificationError, InvalidHashError:
        pass
    else:
        raise AuthError("Bitte ein anderes Passwort als das Installationspasswort wählen.")
    next_token = secrets.token_urlsafe(32)
    if not db.complete_password_change(
        token_hash=_hash_token(token),
        password_hash=old_hash,
        new_password_hash=PASSWORD_HASHER.hash(password),
        new_token_hash=_hash_token(next_token),
        now=now,
    ):
        raise AuthError("Die Einrichtung ist abgelaufen. Bitte erneut mit deinem Passwort anmelden.")
    return next_token


def confirm_enrollment(db: AppDatabase, *, token: str | None, code: str, now: int | None = None) -> str:
    now = int(time.time() if now is None else now)
    secret = enrollment_secret(db, token, now=now)
    if secret is None:
        raise AuthError("Die Einrichtung ist abgelaufen. Bitte erneut mit deinem Passwort anmelden.")
    db.reserve_login_attempt(now=now, limit=LOGIN_ATTEMPT_LIMIT, lock_seconds=LOGIN_LOCK_SECONDS)
    step = matching_totp_step(secret, code, now=now)
    if step is None:
        raise AuthError("Der Code ist ungültig. Bitte den aktuellen Code aus deiner Authenticator-App eingeben.")
    session = secrets.token_urlsafe(32)
    if not db.complete_enrollment(
        token_hash=_hash_token(token),
        now=now,
        totp_step=step,
        session_hash=_hash_token(session),
        expires_at=_session_expiry(now),
    ):
        raise AuthError("Die Einrichtung ist abgelaufen. Bitte erneut mit deinem Passwort anmelden.")
    return session


def _session_expiry(now: int) -> str:
    return (datetime.fromtimestamp(now, UTC) + timedelta(days=SESSION_TTL_DAYS)).isoformat(timespec="seconds")


def session_valid(db: AppDatabase, token: str | None) -> bool:
    return bool(token and db.session_valid(_hash_token(token)))


def logout(db: AppDatabase, token: str | None) -> None:
    if token:
        db.delete_session(_hash_token(token))


def configure_access_cli() -> int:
    import argparse
    import getpass
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Vorläufiges Installationspasswort für nginx-conf festlegen")
    parser.add_argument("--database", type=Path, default=AppDatabase().path)
    parser.add_argument("--replace", action="store_true", help="Zugang ersetzen und alle Sessions abmelden")
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "Die Einrichtung benötigt ein interaktives Terminal.\n")
    db = AppDatabase(args.database)
    db.initialize()
    if db.get_auth_credentials() and not args.replace:
        parser.exit(1, "Zugang bereits eingerichtet. Zum Ersetzen --replace verwenden.\n")
    password = getpass.getpass("Installationspasswort: ")
    if not 12 <= len(password) <= 1024:
        parser.exit(1, "Das Passwort muss zwischen 12 und 1024 Zeichen lang sein.\n")
    if password != getpass.getpass("Passwort wiederholen: "):
        parser.exit(1, "Die Passwörter stimmen nicht überein.\n")
    try:
        configure_password(db, password=password, replace=args.replace)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print("Passwort gespeichert. Beim ersten Login ein eigenes Passwort setzen und den Authenticator einrichten.")
    return 0


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
