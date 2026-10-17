from __future__ import annotations

import ipaddress
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path("/opt/nginx-config")
DATABASE_FILENAME = "nginx-conf.sqlite3"
MAX_EXCERPT_CHARS = 4_000
ACTIVE_ACTION_STATUSES = ("started", "running")
FINISHED_ACTION_STATUSES = ("succeeded", "failed")
FORBIDDEN_JSON_KEYS = {
    "after_hash",
    "before_hash",
    "config_content",
    "diff",
    "file_content",
    "full_diff",
    "git_diff",
    "new_config_content",
    "new_content",
    "old_config_content",
    "old_content",
}


class ActionLockError(RuntimeError):
    pass


class PersistencePolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    created_at: str
    action: str
    project: str | None
    subject_type: str | None
    subject: str | None
    result: str
    git_commit_hash: str | None
    exit_code: int | None
    message_excerpt: str | None


@dataclass(frozen=True, slots=True)
class ActionRecord:
    action_id: str
    status: str
    action_type: str
    target: str
    project: str | None
    config_path: str | None
    subcommand: str | None
    expected_file: str | None
    parameters: dict[str, Any]
    started_at: str
    updated_at: str
    finished_at: str | None
    exit_code: int | None
    stdout_excerpt: str | None
    stderr_excerpt: str | None
    error_excerpt: str | None
    banner_acknowledged: bool
    result_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProjectSelection:
    project_name: str
    nginx_config_path: str | None
    pm2_start_script_path: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class ServerIP:
    address: str
    family: str
    label: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ManagedPM2Process:
    id: int
    project_name: str
    pm2_name: str
    pm2_id: str | None
    cwd: str
    start_script: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str
    created_at: str


def default_database_path(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / DATABASE_FILENAME


class AppDatabase:
    def __init__(self, path: Path | None = None, *, read_only: bool = False) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        self.read_only = read_only

    def initialize(self) -> None:
        if self.read_only:
            raise RuntimeError("Cannot initialize a read-only database connection")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def record_audit(
        self,
        *,
        action: str,
        result: str,
        project: str | None = None,
        subject_type: str | None = None,
        subject: str | None = None,
        git_commit_hash: str | None = None,
        exit_code: int | None = None,
        message_excerpt: str | None = None,
    ) -> int:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                insert into audit_log (
                    created_at, action, project, subject_type, subject, result,
                    git_commit_hash, exit_code, message_excerpt
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    action,
                    project,
                    subject_type,
                    subject,
                    result,
                    git_commit_hash,
                    exit_code,
                    _limit_excerpt(message_excerpt),
                ),
            )
            return int(cursor.lastrowid)

    def list_audit_entries(self) -> list[AuditEntry]:
        with self._connect() as connection:
            rows = connection.execute("select * from audit_log order by id").fetchall()
        return [_audit_entry_from_row(row) for row in rows]

    def create_action(
        self,
        *,
        action_type: str,
        target: str,
        project: str | None = None,
        config_path: str | Path | None = None,
        subcommand: str | None = None,
        expected_file: str | Path | None = None,
        parameters: dict[str, Any] | None = None,
        action_id: str | None = None,
    ) -> str:
        _reject_forbidden_payload(parameters or {})
        now = _now()
        action_id = action_id or str(uuid.uuid4())
        connection = self._connect()
        try:
            connection.execute("begin immediate")
            active = connection.execute(
                "select action_id from actions where status in (?, ?) order by started_at limit 1",
                ACTIVE_ACTION_STATUSES,
            ).fetchone()
            if active is not None:
                connection.rollback()
                raise ActionLockError(f"Aktion laeuft bereits: {active['action_id']}")

            connection.execute(
                """
                insert into actions (
                    action_id, status, action_type, target, project, config_path,
                    subcommand, expected_file, parameters_json, started_at, updated_at,
                    result_metadata_json
                )
                values (?, 'started', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    action_type,
                    target,
                    project,
                    _path_to_str(config_path),
                    subcommand,
                    _path_to_str(expected_file),
                    _json_dump(parameters or {}),
                    now,
                    now,
                    _json_dump({}),
                ),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

        return action_id

    def mark_action_running(self, action_id: str) -> None:
        self._update_action_status(action_id, "running")

    def finish_action(
        self,
        action_id: str,
        *,
        status: str,
        exit_code: int | None = None,
        stdout_excerpt: str | None = None,
        stderr_excerpt: str | None = None,
        error_excerpt: str | None = None,
        result_metadata: dict[str, Any] | None = None,
    ) -> None:
        if status not in FINISHED_ACTION_STATUSES:
            raise ValueError(f"Unsupported finished action status: {status}")
        _reject_forbidden_payload(result_metadata or {})
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                update actions
                set status = ?,
                    updated_at = ?,
                    finished_at = ?,
                    exit_code = ?,
                    stdout_excerpt = ?,
                    stderr_excerpt = ?,
                    error_excerpt = ?,
                    result_metadata_json = ?
                where action_id = ?
                """,
                (
                    status,
                    now,
                    now,
                    exit_code,
                    _limit_excerpt(stdout_excerpt),
                    _limit_excerpt(stderr_excerpt),
                    _limit_excerpt(error_excerpt),
                    _json_dump(result_metadata or {}),
                    action_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(action_id)

    def acknowledge_action_result(self, action_id: str) -> None:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "update actions set banner_acknowledged = 1, updated_at = ? where action_id = ?",
                (now, action_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(action_id)

    def get_action(self, action_id: str) -> ActionRecord | None:
        with self._connect() as connection:
            row = connection.execute("select * from actions where action_id = ?", (action_id,)).fetchone()
        return _action_from_row(row) if row is not None else None

    def get_active_action(self) -> ActionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "select * from actions where status in (?, ?) order by started_at limit 1",
                ACTIVE_ACTION_STATUSES,
            ).fetchone()
        return _action_from_row(row) if row is not None else None

    def get_last_unacknowledged_result(self) -> ActionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                select * from actions
                where status in (?, ?) and banner_acknowledged = 0
                order by finished_at desc, updated_at desc
                limit 1
                """,
                FINISHED_ACTION_STATUSES,
            ).fetchone()
        return _action_from_row(row) if row is not None else None

    def get_last_action_result_by_type(self, action_type: str) -> ActionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                select * from actions
                where action_type = ? and status in (?, ?)
                order by finished_at desc, updated_at desc
                limit 1
                """,
                (action_type, *FINISHED_ACTION_STATUSES),
            ).fetchone()
        return _action_from_row(row) if row is not None else None

    def validate_helper_action(
        self,
        *,
        action_id: str,
        subcommand: str,
        project: str,
        config_path: str | Path,
        expected_file: str | Path,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                select action_id from actions
                where action_id = ?
                  and status in (?, ?)
                  and subcommand = ?
                  and project = ?
                  and config_path = ?
                  and expected_file = ?
                """,
                (
                    action_id,
                    *ACTIVE_ACTION_STATUSES,
                    subcommand,
                    project,
                    _path_to_str(config_path),
                    _path_to_str(expected_file),
                ),
            ).fetchone()
        return row is not None

    def set_ui_state(self, key: str, value: dict[str, Any]) -> None:
        _reject_forbidden_payload(value)
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                insert into ui_state (key, value_json, updated_at)
                values (?, ?, ?)
                on conflict(key) do update set
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, _json_dump(value), now),
            )

    def get_ui_state(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("select value_json from ui_state where key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row is not None else None

    def delete_ui_state(self, key: str) -> None:
        with self._connect() as connection:
            connection.execute("delete from ui_state where key = ?", (key,))

    def consume_ui_state(self, key: str, expected: dict[str, Any]) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "delete from ui_state where key = ? and value_json = ?", (key, _json_dump(expected))
            )
        return result.rowcount == 1

    def count_users(self) -> int:
        with self._connect() as connection:
            row = connection.execute("select count(*) as count from users").fetchone()
        return int(row["count"])

    def create_user(self, username: str) -> User:
        username = username.strip()
        if not username:
            raise ValueError("Username is required")
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "insert into users (username, created_at) values (?, ?)",
                (username, now),
            )
            user_id = int(cursor.lastrowid)
        return User(id=user_id, username=username, created_at=now)

    def get_user(self, user_id: int) -> User | None:
        with self._connect() as connection:
            row = connection.execute("select * from users where id = ?", (user_id,)).fetchone()
        return _user_from_row(row) if row is not None else None

    def get_user_by_username(self, username: str) -> User | None:
        with self._connect() as connection:
            row = connection.execute("select * from users where username = ?", (username,)).fetchone()
        return _user_from_row(row) if row is not None else None

    def create_session(self, *, user_id: int, session_hash: str, expires_at: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                insert into sessions (session_hash, user_id, created_at, last_seen_at, expires_at)
                values (?, ?, ?, ?, ?)
                """,
                (session_hash, user_id, now, now, expires_at),
            )

    def get_user_for_session(self, session_hash: str, *, now: str | None = None) -> User | None:
        now = now or _now()
        with self._connect() as connection:
            row = connection.execute(
                """
                select users.*
                from sessions
                join users on users.id = sessions.user_id
                where sessions.session_hash = ?
                  and sessions.expires_at > ?
                """,
                (session_hash, now),
            ).fetchone()
        return _user_from_row(row) if row is not None else None

    def delete_session(self, session_hash: str) -> None:
        with self._connect() as connection:
            connection.execute("delete from sessions where session_hash = ?", (session_hash,))

    def upsert_server_ip(self, address: str, *, label: str | None = None) -> None:
        parsed = ipaddress.ip_address(address)
        family = "ipv4" if parsed.version == 4 else "ipv6"
        normalized = str(parsed)
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                insert into server_ips (address, family, label, created_at, updated_at)
                values (?, ?, ?, ?, ?)
                on conflict(address) do update set
                    family = excluded.family,
                    label = excluded.label,
                    updated_at = excluded.updated_at
                """,
                (normalized, family, label, now, now),
            )

    def list_server_ips(self) -> list[ServerIP]:
        with self._connect() as connection:
            rows = connection.execute("select * from server_ips order by address").fetchall()
        return [
            ServerIP(
                address=row["address"],
                family=row["family"],
                label=row["label"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def save_project_selection(
        self,
        project_name: str,
        *,
        nginx_config_path: str | Path | None,
        pm2_start_script_path: str | Path | None,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                insert into project_selections (
                    project_name, nginx_config_path, pm2_start_script_path, updated_at
                )
                values (?, ?, ?, ?)
                on conflict(project_name) do update set
                    nginx_config_path = excluded.nginx_config_path,
                    pm2_start_script_path = excluded.pm2_start_script_path,
                    updated_at = excluded.updated_at
                """,
                (
                    project_name,
                    _path_to_str(nginx_config_path),
                    _path_to_str(pm2_start_script_path),
                    now,
                ),
            )

    def get_project_selection(self, project_name: str) -> ProjectSelection | None:
        with self._connect() as connection:
            row = connection.execute(
                "select * from project_selections where project_name = ?",
                (project_name,),
            ).fetchone()
        if row is None:
            return None
        return ProjectSelection(
            project_name=row["project_name"],
            nginx_config_path=row["nginx_config_path"],
            pm2_start_script_path=row["pm2_start_script_path"],
            updated_at=row["updated_at"],
        )

    def register_pm2_process(
        self,
        *,
        project_name: str,
        pm2_name: str,
        cwd: str | Path,
        start_script: str | Path,
        pm2_id: str | int | None = None,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                insert into managed_pm2_processes (
                    project_name, pm2_name, pm2_id, cwd, start_script, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(project_name, pm2_name) do update set
                    pm2_id = excluded.pm2_id,
                    cwd = excluded.cwd,
                    start_script = excluded.start_script,
                    updated_at = excluded.updated_at
                """,
                (
                    project_name,
                    pm2_name,
                    None if pm2_id is None else str(pm2_id),
                    _path_to_str(cwd),
                    _path_to_str(start_script),
                    now,
                    now,
                ),
            )

    def list_managed_pm2_processes(self, project_name: str | None = None) -> list[ManagedPM2Process]:
        query = "select * from managed_pm2_processes"
        params: tuple[str, ...] = ()
        if project_name is not None:
            query += " where project_name = ?"
            params = (project_name,)
        query += " order by project_name, pm2_name"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [
            ManagedPM2Process(
                id=row["id"],
                project_name=row["project_name"],
                pm2_name=row["pm2_name"],
                pm2_id=row["pm2_id"],
                cwd=row["cwd"],
                start_script=row["start_script"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def unregister_pm2_process(self, *, project_name: str, pm2_name: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "delete from managed_pm2_processes where project_name = ? and pm2_name = ?",
                (project_name, pm2_name),
            )

    def _update_action_status(self, action_id: str, status: str) -> None:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "update actions set status = ?, updated_at = ? where action_id = ?",
                (status, now, action_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(action_id)

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return connection


def _audit_entry_from_row(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        created_at=row["created_at"],
        action=row["action"],
        project=row["project"],
        subject_type=row["subject_type"],
        subject=row["subject"],
        result=row["result"],
        git_commit_hash=row["git_commit_hash"],
        exit_code=row["exit_code"],
        message_excerpt=row["message_excerpt"],
    )


def _action_from_row(row: sqlite3.Row) -> ActionRecord:
    return ActionRecord(
        action_id=row["action_id"],
        status=row["status"],
        action_type=row["action_type"],
        target=row["target"],
        project=row["project"],
        config_path=row["config_path"],
        subcommand=row["subcommand"],
        expected_file=row["expected_file"],
        parameters=json.loads(row["parameters_json"]),
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
        exit_code=row["exit_code"],
        stdout_excerpt=row["stdout_excerpt"],
        stderr_excerpt=row["stderr_excerpt"],
        error_excerpt=row["error_excerpt"],
        banner_acknowledged=bool(row["banner_acknowledged"]),
        result_metadata=json.loads(row["result_metadata_json"]),
    )


def _user_from_row(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        created_at=row["created_at"],
    )


def _json_dump(value: dict[str, Any]) -> str:
    _reject_forbidden_payload(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in FORBIDDEN_JSON_KEYS:
                raise PersistencePolicyError(f"Darf nicht in SQLite persistiert werden: {key}")
            _reject_forbidden_payload(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _reject_forbidden_payload(nested)


def _path_to_str(path: str | Path | None) -> str | None:
    return None if path is None else str(path)


def _limit_excerpt(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:MAX_EXCERPT_CHARS]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


SCHEMA = """
create table if not exists audit_log (
    id integer primary key autoincrement,
    created_at text not null,
    action text not null,
    project text,
    subject_type text,
    subject text,
    result text not null,
    git_commit_hash text,
    exit_code integer,
    message_excerpt text
);

create table if not exists actions (
    action_id text primary key,
    status text not null check (status in ('started', 'running', 'succeeded', 'failed')),
    action_type text not null,
    target text not null,
    project text,
    config_path text,
    subcommand text,
    expected_file text,
    parameters_json text not null,
    started_at text not null,
    updated_at text not null,
    finished_at text,
    exit_code integer,
    stdout_excerpt text,
    stderr_excerpt text,
    error_excerpt text,
    banner_acknowledged integer not null default 0 check (banner_acknowledged in (0, 1)),
    result_metadata_json text not null
);

create unique index if not exists one_running_action
on actions((1))
where status in ('started', 'running');

create table if not exists ui_state (
    key text primary key,
    value_json text not null,
    updated_at text not null
);

create table if not exists users (
    id integer primary key autoincrement,
    username text not null unique,
    created_at text not null
);

create table if not exists sessions (
    session_hash text primary key,
    user_id integer not null references users(id) on delete cascade,
    created_at text not null,
    last_seen_at text not null,
    expires_at text not null
);

create table if not exists server_ips (
    address text primary key,
    family text not null check (family in ('ipv4', 'ipv6')),
    label text,
    created_at text not null,
    updated_at text not null
);

create table if not exists project_selections (
    project_name text primary key,
    nginx_config_path text,
    pm2_start_script_path text,
    updated_at text not null
);

create table if not exists managed_pm2_processes (
    id integer primary key autoincrement,
    project_name text not null,
    pm2_name text not null,
    pm2_id text,
    cwd text not null,
    start_script text not null,
    created_at text not null,
    updated_at text not null,
    unique(project_name, pm2_name)
);
"""
