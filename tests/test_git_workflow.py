import subprocess
from pathlib import Path

import pytest

from nginx_conf.configs import discover_inventory
from nginx_conf.git_workflow import GitWorkflowService, inspect_project_git, live_diff_for_selected_config
from nginx_conf.selection import build_project_choice_state
from nginx_conf.store import ActionLockError, AppDatabase


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=True,
    )


def create_project(tmp_path: Path) -> tuple[Path, Path, Path, AppDatabase]:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = project_root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
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
    return project_root, enabled_root, config, db


def project_and_choice(project_root: Path, enabled_root: Path, db: AppDatabase):
    inventory = discover_inventory(project_root, enabled_root)
    project = inventory.projects[0]
    choice = build_project_choice_state(project, db)
    return project, choice


def test_inspect_project_git_reports_clean_and_dirty_config(tmp_path: Path) -> None:
    project_root, enabled_root, config, db = create_project(tmp_path)
    project, choice = project_and_choice(project_root, enabled_root, db)

    clean = inspect_project_git(project, choice)
    assert clean.git_available is True
    assert clean.repo_dirty is False
    assert clean.commit_required is False

    config.write_text("server { listen 80; server_name new.example.com; }\n", encoding="utf-8")
    dirty = inspect_project_git(project, choice)

    assert dirty.repo_dirty is True
    assert dirty.selected_config_dirty is True
    assert dirty.commit_required is True
    assert dirty.commit_allowed is True
    assert dirty.suggested_commit_message == "Config aktualisiert"
    assert dirty.changed_files == ("app.nginx",)
    assert "new.example.com" in live_diff_for_selected_config(project, choice)


def test_inspect_project_git_reports_foreign_changes_without_selecting_them(tmp_path: Path) -> None:
    project_root, enabled_root, config, db = create_project(tmp_path)
    foreign = config.parent / "notes.txt"
    foreign.write_text("dirty\n", encoding="utf-8")
    config.write_text("server { listen 80; server_name new.example.com; }\n", encoding="utf-8")
    project, choice = project_and_choice(project_root, enabled_root, db)

    status = inspect_project_git(project, choice)

    assert status.commit_required is True
    assert status.foreign_changes == ("notes.txt",)


def test_git_workflow_commit_uses_helper_and_audits_commit_hash(tmp_path: Path) -> None:
    project_root, enabled_root, config, db = create_project(tmp_path)
    foreign = config.parent / "notes.txt"
    foreign.write_text("dirty\n", encoding="utf-8")
    config.write_text("server { listen 80; server_name committed.example.com; }\n", encoding="utf-8")
    project, choice = project_and_choice(project_root, enabled_root, db)

    result = GitWorkflowService(db, project_root=project_root).commit_selected_config(
        project,
        choice,
        message="Config aktualisiert",
        action_id="commit-action",
    )

    assert result.ok is True
    assert result.git_commit_hash
    action = db.get_action("commit-action")
    assert action.status == "succeeded"
    audit = db.list_audit_entries()[0]
    assert audit.action == "commit-config"
    assert audit.git_commit_hash == result.git_commit_hash
    committed_files = run_git(config.parent, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert committed_files == ["app.nginx"]
    assert run_git(config.parent, "status", "--porcelain", "--", "notes.txt").stdout.startswith("?? notes.txt")


def test_git_workflow_surfaces_helper_rejection(tmp_path: Path) -> None:
    project_root, enabled_root, config, db = create_project(tmp_path)
    config.write_text("server { listen 80; server_name rejected.example.com; }\n", encoding="utf-8")
    project, choice = project_and_choice(project_root, enabled_root, db)

    def rejecting_helper(_subcommand: str, _payload: str):
        return 1, {"ok": False, "error": "helper rejected", "git_commit_hash": None, "changed_file": None}

    result = GitWorkflowService(db, project_root=project_root, helper_invoker=rejecting_helper).commit_selected_config(
        project,
        choice,
        message="Config aktualisiert",
        action_id="commit-action",
    )

    assert result.ok is False
    assert result.error == "helper rejected"
    assert db.get_action("commit-action").status == "failed"
    assert db.list_audit_entries()[0].result == "failed"


def test_git_workflow_uses_global_action_lock(tmp_path: Path) -> None:
    project_root, enabled_root, config, db = create_project(tmp_path)
    config.write_text("server { listen 80; server_name locked.example.com; }\n", encoding="utf-8")
    project, choice = project_and_choice(project_root, enabled_root, db)
    db.create_action(action_type="certbot", target="example.com")

    with pytest.raises(ActionLockError):
        GitWorkflowService(db, project_root=project_root).commit_selected_config(
            project,
            choice,
            message="Config aktualisiert",
        )


def test_inspect_project_git_reports_missing_repo(tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = project_root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config = project / "app.nginx"
    config.write_text("server { listen 80; server_name demo.example.com; }\n", encoding="utf-8")
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    db.save_project_selection("demo-app", nginx_config_path=str(config), pm2_start_script_path=None)
    project_inventory, choice = project_and_choice(project_root, enabled_root, db)

    status = inspect_project_git(project_inventory, choice)

    assert status.git_available is False
    assert status.error == "Projekt ist kein Git-Repository."
