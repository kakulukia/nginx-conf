import sqlite3
from pathlib import Path

import pytest

from nginx_conf.store import (
    MAX_EXCERPT_CHARS,
    ActionLockError,
    AppDatabase,
    PersistencePolicyError,
)


def make_db(tmp_path: Path) -> AppDatabase:
    db = AppDatabase(tmp_path / "opt/nginx-config/nginx-conf.sqlite3")
    db.initialize()
    return db


def test_basic_crud_for_audit_ui_ips_selection_and_pm2(tmp_path: Path) -> None:
    db = make_db(tmp_path)

    audit_id = db.record_audit(
        action="write-config",
        project="demo-app",
        subject_type="config",
        subject="/opt/www/demo-app/app.nginx",
        result="failed",
        exit_code=1,
        message_excerpt="nginx -t failed",
    )
    entries = db.list_audit_entries()
    assert entries[0].id == audit_id
    assert entries[0].project == "demo-app"
    assert entries[0].message_excerpt == "nginx -t failed"

    db.set_ui_state("dashboard", {"collapsed": ["demo-app"]})
    assert db.get_ui_state("dashboard") == {"collapsed": ["demo-app"]}
    db.delete_ui_state("dashboard")
    assert db.get_ui_state("dashboard") is None

    db.create_session(session_hash="session-hash", expires_at="9999-01-01T00:00:00+00:00")
    assert db.session_valid("session-hash")
    db.delete_session("session-hash")
    assert not db.session_valid("session-hash")

    db.upsert_server_ip("203.0.113.10", label="public-v4")
    db.upsert_server_ip("2001:db8::10", label="public-v6")
    assert [(entry.address, entry.family) for entry in db.list_server_ips()] == [
        ("2001:db8::10", "ipv6"),
        ("203.0.113.10", "ipv4"),
    ]

    db.save_project_selection(
        "demo-app",
        nginx_config_path="/opt/www/demo-app/app.nginx",
        pm2_start_script_path="/opt/www/demo-app/start.sh",
    )
    selection = db.get_project_selection("demo-app")
    assert selection is not None
    assert selection.nginx_config_path == "/opt/www/demo-app/app.nginx"
    assert selection.pm2_start_script_path == "/opt/www/demo-app/start.sh"

    db.register_pm2_process(
        project_name="demo-app",
        pm2_name="demo-app",
        pm2_id=7,
        cwd="/opt/www/demo-app",
        start_script="/opt/www/demo-app/start.sh",
    )
    process = db.list_managed_pm2_processes("demo-app")[0]
    assert process.pm2_id == "7"
    assert process.cwd == "/opt/www/demo-app"
    assert process.start_script == "/opt/www/demo-app/start.sh"
    db.unregister_pm2_process(project_name="demo-app", pm2_name="demo-app")
    assert db.list_managed_pm2_processes("demo-app") == []


def test_action_lock_rejects_parallel_actions(tmp_path: Path) -> None:
    db = make_db(tmp_path)

    first_action_id = db.create_action(action_type="certbot", target="demo.local")

    with pytest.raises(ActionLockError):
        db.create_action(action_type="reload-nginx", target="nginx")

    active = db.get_active_action()
    assert active is not None
    assert active.action_id == first_action_id

    db.finish_action(first_action_id, status="succeeded", exit_code=0)
    assert db.get_active_action() is None

    second_action_id = db.create_action(action_type="reload-nginx", target="nginx")
    assert second_action_id != first_action_id


def test_schema_enforces_only_one_active_action(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    now = "2026-06-11T12:00:00+00:00"

    with db._connect() as connection:
        connection.execute(
            """
            insert into actions (
                action_id, status, action_type, target, parameters_json,
                started_at, updated_at, result_metadata_json
            )
            values ('one', 'started', 'test', 'one', '{}', ?, ?, '{}')
            """,
            (now, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into actions (
                    action_id, status, action_type, target, parameters_json,
                    started_at, updated_at, result_metadata_json
                )
                values ('two', 'running', 'test', 'two', '{}', ?, ?, '{}')
                """,
                (now, now),
            )


def test_action_status_banner_and_helper_validation(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    action_id = db.create_action(
        action_type="write-config",
        target="demo-app/app.nginx",
        project="demo-app",
        config_path="/opt/www/demo-app/app.nginx",
        subcommand="write-config",
        expected_file="/opt/www/demo-app/app.nginx",
        parameters={"diff_stat": {"files": 1, "insertions": 2}},
    )

    assert db.validate_helper_action(
        action_id=action_id,
        subcommand="write-config",
        project="demo-app",
        config_path="/opt/www/demo-app/app.nginx",
        expected_file="/opt/www/demo-app/app.nginx",
    )
    assert not db.validate_helper_action(
        action_id=action_id,
        subcommand="commit-config",
        project="demo-app",
        config_path="/opt/www/demo-app/app.nginx",
        expected_file="/opt/www/demo-app/app.nginx",
    )

    db.mark_action_running(action_id)
    db.finish_action(
        action_id,
        status="failed",
        exit_code=1,
        stdout_excerpt="ok",
        stderr_excerpt="x" * (MAX_EXCERPT_CHARS + 20),
        error_excerpt="nginx -t failed",
        result_metadata={"git_commit_hash": None, "diff_stat": {"files": 1}},
    )
    action = db.get_action(action_id)
    assert action is not None
    assert action.status == "failed"
    assert action.stderr_excerpt == "x" * MAX_EXCERPT_CHARS
    assert action.result_metadata == {"git_commit_hash": None, "diff_stat": {"files": 1}}

    assert db.get_last_unacknowledged_result() == action
    assert db.get_last_action_result_by_type("write-config") == action
    assert db.get_last_action_result_by_type("certbot-renew-dry-run") is None
    db.acknowledge_action_result(action_id)
    assert db.get_last_unacknowledged_result() is None
    last_by_type = db.get_last_action_result_by_type("write-config")
    assert last_by_type is not None
    assert last_by_type.action_id == action.action_id
    assert last_by_type.banner_acknowledged is True


def test_forbidden_config_content_diffs_and_hashes_are_rejected(tmp_path: Path) -> None:
    db = make_db(tmp_path)

    with pytest.raises(PersistencePolicyError):
        db.create_action(action_type="write-config", target="demo", parameters={"new_content": "server { }"})

    action_id = db.create_action(action_type="write-config", target="demo")
    with pytest.raises(PersistencePolicyError):
        db.finish_action(action_id, status="failed", result_metadata={"full_diff": "@@ ..."})

    db.finish_action(action_id, status="failed")
    with pytest.raises(PersistencePolicyError):
        db.set_ui_state("last-edit", {"before_hash": "abc"})


def test_schema_has_no_full_config_diff_or_file_hash_columns(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    forbidden_columns = {
        "after_hash",
        "before_hash",
        "config_content",
        "diff",
        "full_diff",
        "new_content",
        "old_content",
    }

    with db._connect() as connection:
        table_names = [
            row["name"]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'",
            )
        ]
        column_names = {
            column["name"]
            for table_name in table_names
            for column in connection.execute(f"pragma table_info({table_name})")
        }

    assert forbidden_columns.isdisjoint(column_names)


def test_project_selection_cannot_change_during_another_action(tmp_path):
    db = make_db(tmp_path)
    action_id = db.create_action(action_type="helper-activate-config", target="config")
    with pytest.raises(ActionLockError):
        db.save_project_selection("demo", nginx_config_path="/opt/www/demo/app.nginx", pm2_start_script_path=None)
    db.save_project_selection(
        "demo", nginx_config_path="/opt/www/demo/app.nginx", pm2_start_script_path=None, action_id=action_id
    )
    assert db.get_project_selection("demo").nginx_config_path == "/opt/www/demo/app.nginx"
