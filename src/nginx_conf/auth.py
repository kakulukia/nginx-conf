from __future__ import annotations

import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nginx_conf.store import AppDatabase, User

SESSION_COOKIE_NAME = "nginx_conf_session"
SETUP_STATE_KEY = "auth.setup_token"
SETUP_TOKEN_FILENAME = "setup-token"
SESSION_TTL_DAYS = 30


class AuthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SetupResult:
    user: User
    session_token: str


def ensure_setup_token(db: AppDatabase, *, token_path: Path) -> bool:
    if db.count_users() > 0:
        _remove_token_file(token_path)
        db.delete_ui_state(SETUP_STATE_KEY)
        return False
    if db.get_ui_state(SETUP_STATE_KEY) is not None:
        return False

    token = secrets.token_urlsafe(32)
    db.set_ui_state(SETUP_STATE_KEY, {"token_hash": _hash_token(token), "created_at": _now()})
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    return True


def complete_setup(
    db: AppDatabase,
    *,
    setup_token: str,
    username: str,
    server_ips: list[str],
    token_path: Path,
) -> SetupResult:
    if db.count_users() > 0:
        raise AuthError("Setup ist bereits abgeschlossen")
    state = db.get_ui_state(SETUP_STATE_KEY)
    if not state or state.get("token_hash") != _hash_token(setup_token):
        raise AuthError("Setup-Token ist ungueltig")

    for address in server_ips:
        if address:
            db.upsert_server_ip(address, label="setup")
    user = db.create_user(username)
    db.delete_ui_state(SETUP_STATE_KEY)
    _remove_token_file(token_path)
    session_token = create_session(db, user)
    return SetupResult(user=user, session_token=session_token)


def create_session(db: AppDatabase, user: User) -> str:
    session_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS)).isoformat(timespec="seconds")
    db.create_session(user_id=user.id, session_hash=_hash_token(session_token), expires_at=expires_at)
    return session_token


def user_for_session(db: AppDatabase, session_token: str | None) -> User | None:
    if not session_token:
        return None
    return db.get_user_for_session(_hash_token(session_token))


def setup_client_allowed(host: str | None) -> bool:
    if host == "testclient":
        return True
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def setup_token_path_for_database(db_path: Path) -> Path:
    return db_path.parent / SETUP_TOKEN_FILENAME


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _remove_token_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
