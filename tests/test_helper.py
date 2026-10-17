import json
import sqlite3
import subprocess
from pathlib import Path

from nginx_conf import helper as helper_module
from nginx_conf.helper import handle_helper_request
from nginx_conf.store import AppDatabase


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=True,
    )


def create_repo_project(tmp_path: Path) -> tuple[Path, Path, Path, AppDatabase]:
    project_root = tmp_path / "opt/www"
    project = project_root / "demo-app"
    project.mkdir(parents=True)
    config = project / "app.nginx"
    config.write_text("server { listen 80; server_name old.example.com; }\n", encoding="utf-8")
    run_git(project, "init")
    run_git(project, "config", "user.email", "test@example.com")
    run_git(project, "config", "user.name", "Test User")
    run_git(project, "add", "app.nginx")
    run_git(project, "commit", "-m", "initial")

    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    db.save_project_selection("demo-app", nginx_config_path=str(config), pm2_start_script_path=None)
    return project_root, project, config, db


def create_action(db: AppDatabase, subcommand: str, config: Path, *, project: str = "demo-app") -> str:
    return db.create_action(
        action_type=f"helper-{subcommand}",
        target=str(config),
        project=project,
        config_path=str(config),
        subcommand=subcommand,
        expected_file=str(config),
    )


def request_payload(action_id: str, subcommand: str, config: Path, **extra) -> str:
    payload = {
        "action_id": action_id,
        "project": "demo-app",
        "config_path": str(config),
        "expected_file": str(config),
    }
    payload.update(extra)
    return json.dumps(payload)


def clone_request_payload(action_id: str, *, clone_url: str, target_project: str, target_path: Path) -> str:
    return json.dumps(
        {
            "action_id": action_id,
            "clone_url": clone_url,
            "target_project": target_project,
            "target_path": str(target_path),
        }
    )


def test_clone_project_helper_validates_action_and_runs_fixed_git_clone(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project_root.mkdir(parents=True)
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    action_id = db.create_action(
        action_type="helper-clone-project",
        target="demo-app",
        project="demo-app",
        subcommand="clone-project",
    )
    calls = []

    def fake_clone(clone_url: str, target_path: Path):
        calls.append((clone_url, target_path))
        target_path.mkdir()
        return subprocess.CompletedProcess(("git", "clone"), 0, "cloned", "")

    monkeypatch.setattr(helper_module, "_run_git_clone", fake_clone)

    exit_code, response = handle_helper_request(
        "clone-project",
        clone_request_payload(
            action_id,
            clone_url="https://github.com/freilandkiwis/demo-app.git",
            target_project="demo-app",
            target_path=project_root / "demo-app",
        ),
        db_path=db.path,
        project_root=project_root,
    )

    assert exit_code == 0
    assert response["ok"] is True
    assert response["target_project"] == "demo-app"
    assert calls == [("https://github.com/freilandkiwis/demo-app.git", project_root.resolve() / "demo-app")]


def test_clone_project_helper_rejects_mismatched_target_without_clone(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project_root.mkdir(parents=True)
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    action_id = db.create_action(
        action_type="helper-clone-project",
        target="demo-app",
        project="demo-app",
        subcommand="clone-project",
    )
    calls = []
    monkeypatch.setattr(helper_module, "_run_git_clone", lambda clone_url, target_path: calls.append(target_path))

    exit_code, response = handle_helper_request(
        "clone-project",
        clone_request_payload(
            action_id,
            clone_url="https://github.com/freilandkiwis/other.git",
            target_project="demo-app",
            target_path=project_root / "demo-app",
        ),
        db_path=db.path,
        project_root=project_root,
    )

    assert exit_code == 1
    assert "Clone target does not match" in response["error"]
    assert calls == []


def test_write_config_accepts_content_json_but_does_not_write_sqlite(tmp_path: Path) -> None:
    project_root, _project, config, db = create_repo_project(tmp_path)
    action_id = create_action(db, "write-config", config)
    new_content = "server { listen 80; server_name new.example.com; }\n"

    exit_code, response = handle_helper_request(
        "write-config",
        request_payload(action_id, "write-config", config, new_content=new_content),
        db_path=db.path,
        project_root=project_root,
    )

    assert exit_code == 0
    assert response["action_id"] == action_id
    assert response["ok"] is True
    assert response["changed_file"] == str(config.resolve())
    assert response["diff_stat"]["files"] == 1
    assert config.read_text(encoding="utf-8") == new_content
    assert db.get_action(action_id).status == "started"
    assert db.list_audit_entries() == []

    with sqlite3.connect(db.path) as connection:
        dump = "\n".join(connection.iterdump())
    assert "new.example.com" not in dump
    assert "before_hash" not in dump
    assert "after_hash" not in dump


def test_invalid_json_and_missing_action_id_have_no_write_effect(tmp_path: Path) -> None:
    project_root, _project, config, db = create_repo_project(tmp_path)
    original = config.read_text(encoding="utf-8")

    exit_code, response = handle_helper_request("write-config", "{", db_path=db.path, project_root=project_root)

    assert exit_code == 1
    assert response["ok"] is False
    assert response["action_id"] is None
    assert config.read_text(encoding="utf-8") == original

    exit_code, response = handle_helper_request(
        "write-config",
        request_payload("", "write-config", config, new_content="changed"),
        db_path=db.path,
        project_root=project_root,
    )

    assert exit_code == 1
    assert response["ok"] is False
    assert config.read_text(encoding="utf-8") == original


def test_unknown_or_mismatched_action_id_has_no_write_effect(tmp_path: Path) -> None:
    project_root, _project, config, db = create_repo_project(tmp_path)
    original = config.read_text(encoding="utf-8")
    action_id = create_action(db, "commit-config", config)

    unknown_exit, unknown = handle_helper_request(
        "write-config",
        request_payload("unknown", "write-config", config, new_content="changed"),
        db_path=db.path,
        project_root=project_root,
    )
    mismatch_exit, mismatch = handle_helper_request(
        "write-config",
        request_payload(action_id, "write-config", config, new_content="changed"),
        db_path=db.path,
        project_root=project_root,
    )

    assert unknown_exit == 1
    assert mismatch_exit == 1
    assert "Unknown action_id" in unknown["error"]
    assert "Subcommand does not match" in mismatch["error"]
    assert config.read_text(encoding="utf-8") == original


def test_wrong_project_or_config_path_has_no_write_effect(tmp_path: Path) -> None:
    project_root, _project, config, db = create_repo_project(tmp_path)
    original = config.read_text(encoding="utf-8")
    action_id = create_action(db, "write-config", config)
    outside = tmp_path / "outside.nginx"
    outside.write_text("server { listen 80; }\n", encoding="utf-8")

    wrong_project_payload = request_payload(
        action_id,
        "write-config",
        config,
        project="other",
        new_content="changed",
    )
    escape_payload = request_payload(
        action_id,
        "write-config",
        outside,
        new_content="changed",
    )

    assert (
        handle_helper_request("write-config", wrong_project_payload, db_path=db.path, project_root=project_root)[0] == 1
    )
    assert handle_helper_request("write-config", escape_payload, db_path=db.path, project_root=project_root)[0] == 1
    assert config.read_text(encoding="utf-8") == original
    assert outside.read_text(encoding="utf-8") == "server { listen 80; }\n"


def test_tempfile_hash_and_multifile_requests_are_rejected(tmp_path: Path) -> None:
    project_root, _project, config, db = create_repo_project(tmp_path)
    action_id = create_action(db, "write-config", config)
    original = config.read_text(encoding="utf-8")

    for extra in (
        {"tempfile_path": "/tmp/config"},
        {"before_hash": "abc"},
        {"after_hash": "def"},
        {"full_diff": "@@ ..."},
        {"files": [str(config)]},
    ):
        exit_code, response = handle_helper_request(
            "write-config",
            request_payload(action_id, "write-config", config, new_content="changed", **extra),
            db_path=db.path,
            project_root=project_root,
        )
        assert exit_code == 1
        assert response["ok"] is False
        assert config.read_text(encoding="utf-8") == original


def test_reset_config_to_head_resets_only_expected_file(tmp_path: Path) -> None:
    project_root, project, config, db = create_repo_project(tmp_path)
    other = project / "other.txt"
    other.write_text("dirty\n", encoding="utf-8")
    config.write_text("broken\n", encoding="utf-8")
    action_id = create_action(db, "reset-config-to-head", config)

    exit_code, response = handle_helper_request(
        "reset-config-to-head",
        request_payload(action_id, "reset-config-to-head", config),
        db_path=db.path,
        project_root=project_root,
    )

    assert exit_code == 0
    assert response["ok"] is True
    assert config.read_text(encoding="utf-8") == "server { listen 80; server_name old.example.com; }\n"
    assert other.read_text(encoding="utf-8") == "dirty\n"


def test_commit_config_commits_only_expected_file_with_dirty_foreign_changes(tmp_path: Path) -> None:
    project_root, project, config, db = create_repo_project(tmp_path)
    other = project / "other.txt"
    other.write_text("dirty\n", encoding="utf-8")
    config.write_text("server { listen 80; server_name committed.example.com; }\n", encoding="utf-8")
    action_id = create_action(db, "commit-config", config)

    exit_code, response = handle_helper_request(
        "commit-config",
        request_payload(action_id, "commit-config", config, message="Config aktualisiert"),
        db_path=db.path,
        project_root=project_root,
    )

    assert exit_code == 0
    assert response["ok"] is True
    assert response["git_commit_hash"]
    committed_files = run_git(project, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert committed_files == ["app.nginx"]
    assert run_git(project, "status", "--porcelain", "--", "other.txt").stdout.startswith("?? other.txt")


def test_activate_and_deactivate_config_manage_only_project_symlink(tmp_path: Path) -> None:
    project_root, _project, config, db = create_repo_project(tmp_path)
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    enabled_root.mkdir(parents=True)
    link_path = enabled_root / "demo-app.conf"
    activate_id = create_action(db, "activate-config", config)

    activate_exit, activate = handle_helper_request(
        "activate-config",
        request_payload(activate_id, "activate-config", config, link_path=str(link_path)),
        db_path=db.path,
        project_root=project_root,
        enabled_root=enabled_root,
    )

    assert activate_exit == 0
    assert activate["ok"] is True
    assert link_path.is_symlink()
    assert link_path.resolve(strict=True) == config.resolve()
    db.finish_action(activate_id, status="succeeded", exit_code=0)

    deactivate_id = create_action(db, "deactivate-config", config)
    deactivate_exit, deactivate = handle_helper_request(
        "deactivate-config",
        request_payload(deactivate_id, "deactivate-config", config, link_path=str(link_path)),
        db_path=db.path,
        project_root=project_root,
        enabled_root=enabled_root,
    )

    assert deactivate_exit == 0
    assert deactivate["ok"] is True
    assert not link_path.exists()
    assert not link_path.is_symlink()


def test_activate_config_rejects_foreign_enabled_state(tmp_path: Path) -> None:
    project_root, project, config, db = create_repo_project(tmp_path)
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    enabled_root.mkdir(parents=True)
    other_config = project / "other.nginx"
    other_config.write_text("server { listen 80; server_name other.example.com; }\n", encoding="utf-8")
    link_path = enabled_root / "demo-app.conf"
    link_path.symlink_to(other_config)
    action_id = create_action(db, "activate-config", config)

    exit_code, response = handle_helper_request(
        "activate-config",
        request_payload(action_id, "activate-config", config, link_path=str(link_path)),
        db_path=db.path,
        project_root=project_root,
        enabled_root=enabled_root,
    )

    assert exit_code == 1
    assert response["ok"] is False
    assert "different config" in response["error"]
    assert link_path.resolve(strict=True) == other_config.resolve()


def test_missing_git_repo_blocks_before_write(tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project = project_root / "demo-app"
    project.mkdir(parents=True)
    config = project / "app.nginx"
    config.write_text("server { listen 80; }\n", encoding="utf-8")
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    db.save_project_selection("demo-app", nginx_config_path=str(config), pm2_start_script_path=None)
    action_id = create_action(db, "write-config", config)

    exit_code, response = handle_helper_request(
        "write-config",
        request_payload(action_id, "write-config", config, new_content="changed"),
        db_path=db.path,
        project_root=project_root,
    )

    assert exit_code == 1
    assert response["ok"] is False
    assert "Git repository" in response["error"]
    assert config.read_text(encoding="utf-8") == "server { listen 80; }\n"


def test_certbot_helper_checks_active_selected_config_and_action_domains(monkeypatch, tmp_path: Path) -> None:
    from nginx_conf.system_actions import certbot_issue_command

    project_root, project, config, db = create_repo_project(tmp_path)
    enabled = tmp_path / "enabled"
    enabled.mkdir()
    (enabled / "demo-app").symlink_to(config)
    domains = ["old.example.com"]
    command = certbot_issue_command(domains, project="demo-app", config_path=config)
    action_id = db.create_action(
        action_type=command.action_type,
        target=command.target,
        project=command.project,
        config_path=config,
        expected_file=config,
        subcommand=command.subcommand,
        parameters=command.parameters,
    )
    calls = []
    real_run = subprocess.run

    def fake_certbot(argv, **kwargs):
        if argv[0] == "/usr/bin/certbot":
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "issued", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_certbot)
    for bad in (["other.example.com"], ["--config"], [], [None]):
        code, _ = handle_helper_request(
            "certbot-issue",
            request_payload(action_id, "certbot-issue", config, domains=bad),
            db_path=db.path,
            project_root=project_root,
            enabled_root=enabled,
        )
        assert code == 1
    assert calls == []
    payload = request_payload(action_id, "certbot-issue", config, domains=domains)
    code, result = handle_helper_request(
        "certbot-issue", payload, db_path=db.path, project_root=project_root, enabled_root=enabled
    )
    assert code == 0 and result["ok"]
    assert calls == [command.argv]
    (enabled / "demo-app").unlink()
    code, _ = handle_helper_request(
        "certbot-issue", payload, db_path=db.path, project_root=project_root, enabled_root=enabled
    )
    assert code == 1 and len(calls) == 1
    db.finish_action(action_id, status="succeeded")
    code, _ = handle_helper_request(
        "certbot-issue", payload, db_path=db.path, project_root=project_root, enabled_root=enabled
    )
    assert code == 1 and len(calls) == 1
