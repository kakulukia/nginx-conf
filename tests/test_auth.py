from pathlib import Path

import pytest

from nginx_conf.auth import (
    AuthError,
    complete_setup,
    create_session,
    ensure_setup_token,
    setup_client_allowed,
    user_for_session,
)
from nginx_conf.store import AppDatabase


def make_db(tmp_path: Path) -> AppDatabase:
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    return db


def test_setup_token_is_written_once_and_invalidated_after_setup(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    token_path = tmp_path / "setup-token"

    assert ensure_setup_token(db, token_path=token_path) is True
    token = token_path.read_text(encoding="utf-8").strip()
    assert token
    assert ensure_setup_token(db, token_path=token_path) is False

    result = complete_setup(
        db,
        setup_token=token,
        username="admin",
        server_ips=["203.0.113.10", "2001:db8::10"],
        token_path=token_path,
    )

    assert result.user.username == "admin"
    assert db.count_users() == 1
    assert db.get_ui_state("auth.setup_token") is None
    assert not token_path.exists()
    assert [(entry.address, entry.family) for entry in db.list_server_ips()] == [
        ("2001:db8::10", "ipv6"),
        ("203.0.113.10", "ipv4"),
    ]
    assert user_for_session(db, result.session_token).username == "admin"


def test_setup_rejects_invalid_token(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    token_path = tmp_path / "setup-token"
    ensure_setup_token(db, token_path=token_path)

    with pytest.raises(AuthError):
        complete_setup(db, setup_token="wrong", username="admin", server_ips=[], token_path=token_path)


def test_session_lookup_rejects_unknown_tokens(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    user = db.create_user("admin")
    token = create_session(db, user)

    assert user_for_session(db, token).username == "admin"
    assert user_for_session(db, "wrong") is None


def test_setup_client_allows_only_non_global_addresses() -> None:
    assert setup_client_allowed("127.0.0.1") is True
    assert setup_client_allowed("100.64.0.1") is True
    assert setup_client_allowed("10.0.0.5") is True
    assert setup_client_allowed("8.8.8.8") is False
    assert setup_client_allowed("example.com") is False
