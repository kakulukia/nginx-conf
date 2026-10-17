from pathlib import Path

import pytest

from nginx_conf.auth import (
    LOGIN_STATE_KEY,
    AuthError,
    complete_setup,
    create_session,
    ensure_setup_token,
    issue_login_token,
    login_with_token,
    logout,
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


def test_login_token_is_hashed_single_use_and_logout_revokes_session(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.create_user("andy")
    token = issue_login_token(db, "andy")
    assert token not in str(db.get_ui_state(LOGIN_STATE_KEY))
    result = login_with_token(db, token)
    assert result.user.username == "andy"
    assert user_for_session(db, result.session_token) == result.user
    with pytest.raises(AuthError):
        login_with_token(db, token)
    logout(db, result.session_token)
    assert user_for_session(db, result.session_token) is None


def test_login_rejects_wrong_replaced_and_expired_tokens(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.create_user("andy")
    old_token = issue_login_token(db, "andy")
    token = issue_login_token(db, "andy")
    for invalid in ("", "wrong", old_token):
        with pytest.raises(AuthError):
            login_with_token(db, invalid)
    state = db.get_ui_state(LOGIN_STATE_KEY)
    state["expires_at"] = "2000-01-01T00:00:00+00:00"
    db.set_ui_state(LOGIN_STATE_KEY, state)
    with pytest.raises(AuthError):
        login_with_token(db, token)
    with pytest.raises(AuthError):
        issue_login_token(db, "unknown")


def test_only_one_concurrent_request_can_consume_a_login_token(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    db = make_db(tmp_path)
    db.create_user("andy")
    token = issue_login_token(db, "andy")

    def attempt():
        try:
            return login_with_token(db, token).user.username
        except AuthError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: attempt(), range(2))).count("andy") == 1
