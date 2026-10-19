import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyotp
import pytest

from nginx_conf import auth
from nginx_conf.store import AppDatabase, LoginThrottledError

PASSWORD = "local-test-password"
SECRET = "JBSWY3DPEHPK3PXP"
NOW = 1_700_000_000


@pytest.fixture
def db(tmp_path: Path) -> AppDatabase:
    database = AppDatabase(tmp_path / "auth.sqlite3")
    database.initialize()
    database.save_auth_credentials(
        password_hash=auth.PASSWORD_HASHER.hash(PASSWORD), totp_secret=SECRET, last_totp_step=NOW // 30
    )
    return database


def test_installation_sets_only_password_and_rejects_accidental_replacement(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "auth.sqlite3")
    db.initialize()
    with pytest.raises(auth.AuthError):
        auth.configure_password(db, password="short")
    assert db.get_auth_credentials() is None
    auth.configure_password(db, password=PASSWORD)
    credentials = db.get_auth_credentials()
    assert credentials.password_hash.startswith("$argon2id$")
    assert PASSWORD not in credentials.password_hash
    assert auth.PASSWORD_HASHER.verify(credentials.password_hash, PASSWORD)
    assert credentials.totp_secret == ""
    assert db.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="bereits eingerichtet"):
        auth.configure_password(db, password=PASSWORD)


def test_login_requires_both_factors_and_rejects_replay(db: AppDatabase) -> None:
    code = pyotp.TOTP(SECRET).at(NOW + 30)
    for password, otp in (("wrong", code), (PASSWORD, "wrong"), (PASSWORD, pyotp.TOTP(SECRET).at(NOW))):
        with pytest.raises(auth.AuthError):
            auth.login(db, password=password, code=otp, now=NOW + 30)
    token = auth.login(db, password=PASSWORD, code=code, now=NOW + 30)
    assert db.session_valid(auth._hash_token(token), now="2023-11-14T22:14:00+00:00")
    with pytest.raises(auth.AuthError):
        auth.login(db, password=PASSWORD, code=code, now=NOW + 30)
    auth.logout(db, token)
    assert not db.session_valid(auth._hash_token(token), now="2023-11-14T22:14:00+00:00")


def test_same_totp_can_only_create_one_session_under_concurrency(db: AppDatabase) -> None:
    def attempt() -> bool:
        try:
            auth.login(db, password=PASSWORD, code=pyotp.TOTP(SECRET).at(NOW + 30), now=NOW + 30)
            return True
        except auth.AuthError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [False, True]


def test_rate_limit_persists_and_expires(db: AppDatabase) -> None:
    for _ in range(auth.LOGIN_ATTEMPT_LIMIT):
        with pytest.raises(auth.AuthError):
            auth.login(db, password="wrong", code="123456", now=NOW + 30)
    with pytest.raises(LoginThrottledError) as caught:
        auth.login(AppDatabase(db.path), password=PASSWORD, code=pyotp.TOTP(SECRET).at(NOW + 30), now=NOW + 31)
    assert caught.value.retry_after == 59
    token = auth.login(db, password=PASSWORD, code=pyotp.TOTP(SECRET).at(NOW + 91), now=NOW + 91)
    assert db.session_valid(auth._hash_token(token), now="2023-11-14T22:15:00+00:00")


@pytest.mark.parametrize("offset, accepted", [(-60, False), (-30, True), (0, True), (30, True), (60, False)])
def test_totp_accepts_only_adjacent_time_steps(offset: int, accepted: bool) -> None:
    code = pyotp.TOTP(SECRET).at(NOW + offset)
    assert (auth.matching_totp_step(SECRET, code, now=NOW) is not None) is accepted


def test_rotating_credentials_revokes_sessions_and_inflight_login(db: AppDatabase) -> None:
    old = db.get_auth_credentials()
    token = auth.login(db, password=PASSWORD, code=pyotp.TOTP(SECRET).at(NOW + 30), now=NOW + 30)
    assert db.session_valid(auth._hash_token(token), now="2023-11-14T22:14:00+00:00")
    auth.configure_password(db, password=PASSWORD, replace=True)
    assert not db.session_valid(auth._hash_token(token), now="2023-11-14T22:14:00+00:00")
    assert not db.complete_login(
        old, totp_step=NOW // 30 + 3, session_hash="old-inflight", expires_at="9999-01-01T00:00:00+00:00"
    )


def test_legacy_auth_is_removed_without_losing_project_data(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "legacy.sqlite3")
    db.initialize()
    db.upsert_server_ip("203.0.113.10")
    db.save_project_selection("demo", nginx_config_path=Path("/opt/www/demo/app.nginx"), pm2_start_script_path=None)
    db.set_ui_state("auth.setup_token", {"token_hash": "legacy"})
    (tmp_path / "setup-token").write_text("legacy")
    with sqlite3.connect(db.path) as connection:
        connection.executescript(
            "create table users(id integer primary key); create table sessions(session_hash text, user_id integer); insert into users values (1); insert into sessions values ('legacy-session', 1);"
        )
    db.initialize()
    with sqlite3.connect(db.path) as connection:
        names = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")}
    assert "users" not in names and "sessions" not in names
    assert db.get_ui_state("auth.setup_token") is None
    assert not (tmp_path / "setup-token").exists()
    assert db.get_auth_credentials() is None
    assert not db.session_valid("legacy-session")
    assert db.list_server_ips()[0].address == "203.0.113.10"
    assert db.get_project_selection("demo").nginx_config_path == "/opt/www/demo/app.nginx"


def test_installation_cli_only_asks_for_password(monkeypatch, tmp_path: Path, capsys) -> None:
    import getpass
    import sys

    monkeypatch.setattr(sys, "argv", ["nginx-conf-auth", "--database", str(tmp_path / "cli.sqlite3")])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    inputs = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(inputs))
    assert auth.configure_access_cli() == 0
    output = capsys.readouterr().out
    assert "Passwort gespeichert" in output
    assert "Authenticator" in output
    assert SECRET not in output
    assert PASSWORD not in output
    assert AppDatabase(tmp_path / "cli.sqlite3").get_auth_credentials().totp_secret == ""


@pytest.fixture
def pending_db(tmp_path: Path) -> AppDatabase:
    database = AppDatabase(tmp_path / "pending.sqlite3")
    database.initialize()
    auth.configure_password(database, password=PASSWORD)
    return database


def test_password_only_grants_enrollment_not_app_access(pending_db: AppDatabase) -> None:
    with pytest.raises(auth.AuthError):
        auth.login(pending_db, password="wrong", code="", now=NOW)
    pending = auth.login(pending_db, password=PASSWORD, code="", now=NOW)
    assert isinstance(pending, auth.EnrollmentRequired)
    assert not auth.session_valid(pending_db, pending.token)
    secret = auth.enrollment_secret(pending_db, pending.token, now=NOW)
    assert secret and len(secret) == 32
    with sqlite3.connect(pending_db.path) as connection:
        stored_hash = connection.execute("select token_hash from auth_enrollments").fetchone()[0]
    assert stored_hash == auth._hash_token(pending.token) and stored_hash != pending.token
    assert pending_db.get_auth_credentials().totp_secret == ""
    with pytest.raises(auth.AuthError):
        auth.confirm_enrollment(pending_db, token=pending.token, code="invalid", now=NOW)
    assert auth.enrollment_secret(pending_db, pending.token, now=NOW) == secret
    session = auth.confirm_enrollment(pending_db, token=pending.token, code=pyotp.TOTP(secret).at(NOW), now=NOW)
    assert pending_db.session_valid(auth._hash_token(session), now="2023-11-14T22:13:21+00:00")
    assert pending_db.get_auth_credentials().totp_secret == secret
    assert auth.enrollment_secret(pending_db, pending.token, now=NOW) is None
    with pytest.raises(auth.AuthError):
        auth.login(pending_db, password=PASSWORD, code=pyotp.TOTP(secret).at(NOW), now=NOW)
    with pytest.raises(auth.AuthError):
        auth.login(pending_db, password=PASSWORD, code="", now=NOW + 30)
    assert isinstance(auth.login(pending_db, password=PASSWORD, code=pyotp.TOTP(secret).at(NOW + 30), now=NOW + 30), str)


@pytest.mark.parametrize("token", [None, "unknown-token"])
def test_enrollment_requires_valid_password_proof(pending_db: AppDatabase, token: str | None) -> None:
    assert auth.enrollment_secret(pending_db, token, now=NOW) is None
    with pytest.raises(auth.AuthError):
        auth.confirm_enrollment(pending_db, token=token, code="123456", now=NOW)
    assert pending_db.get_auth_credentials().totp_secret == ""


def test_enrollment_expiry_and_password_replacement_invalidate_proof(pending_db: AppDatabase) -> None:
    pending = auth.login(pending_db, password=PASSWORD, code="", now=NOW)
    secret = auth.enrollment_secret(pending_db, pending.token, now=NOW)
    with pytest.raises(auth.AuthError):
        auth.confirm_enrollment(pending_db, token=pending.token, code=pyotp.TOTP(secret).at(NOW + 600), now=NOW + 600)
    assert auth.enrollment_secret(pending_db, pending.token, now=NOW + 600) is None
    pending = auth.login(pending_db, password=PASSWORD, code="", now=NOW + 600)
    auth.configure_password(pending_db, password=PASSWORD, replace=True)
    assert auth.enrollment_secret(pending_db, pending.token, now=NOW + 600) is None
    with pytest.raises(auth.AuthError):
        auth.confirm_enrollment(pending_db, token=pending.token, code="123456", now=NOW + 600)


def test_concurrent_enrollments_cannot_replace_confirmed_authenticator(pending_db: AppDatabase) -> None:
    first = auth.login(pending_db, password=PASSWORD, code="", now=NOW)
    second = auth.login(pending_db, password=PASSWORD, code="", now=NOW)
    challenges = [(p, auth.enrollment_secret(pending_db, p.token, now=NOW)) for p in (first, second)]

    def confirm(challenge) -> bool:
        pending, secret = challenge
        try:
            auth.confirm_enrollment(pending_db, token=pending.token, code=pyotp.TOTP(secret).at(NOW), now=NOW)
            return True
        except auth.AuthError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, challenges)) == [False, True]
    for pending, _ in challenges:
        assert auth.enrollment_secret(pending_db, pending.token, now=NOW) is None


def test_enrollment_confirmation_is_rate_limited(pending_db: AppDatabase) -> None:
    pending = auth.login(pending_db, password=PASSWORD, code="", now=NOW)
    for _ in range(auth.LOGIN_ATTEMPT_LIMIT - 1):
        with pytest.raises(auth.AuthError):
            auth.confirm_enrollment(pending_db, token=pending.token, code="wrong", now=NOW)
    with pytest.raises(LoginThrottledError):
        auth.confirm_enrollment(pending_db, token=pending.token, code="wrong", now=NOW + 1)
