from pathlib import Path

import pytest

from nginx_conf.configs import discover_inventory
from nginx_conf.selection import build_project_choice_state, find_pm2_start_scripts, save_project_choice
from nginx_conf.store import AppDatabase


def make_db(tmp_path: Path) -> AppDatabase:
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    return db


def issue_codes(choice) -> set[str]:
    return {issue.code for issue in choice.issues}


def test_find_pm2_start_scripts_uses_only_sh_files_and_ignores_cache_dirs_and_symlinks(tmp_path: Path) -> None:
    project = tmp_path / "demo-app"
    project.mkdir()
    (project / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (project / "ecosystem.config.js").write_text("module.exports = {}", encoding="utf-8")
    (project / "deploy.yaml").write_text("ignored: true", encoding="utf-8")
    (project / "node_modules").mkdir()
    (project / "node_modules/hidden.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    outside_script = tmp_path / "outside.sh"
    outside_script.write_text("#!/bin/sh\n", encoding="utf-8")
    (project / "linked.sh").symlink_to(outside_script)

    scripts = find_pm2_start_scripts(project)

    assert scripts == (project / "start.sh",)


def test_build_project_choice_state_auto_selects_single_candidates(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config_path = project / "app.nginx"
    script_path = project / "start.sh"
    config_path.write_text("server { listen 80; server_name demo.example.com; }", encoding="utf-8")
    script_path.write_text("#!/bin/sh\n", encoding="utf-8")
    inventory = discover_inventory(root, enabled_root)

    choice = build_project_choice_state(inventory.projects[0], make_db(tmp_path))

    assert choice.selected_nginx_config == config_path
    assert choice.nginx_selection_source == "auto"
    assert choice.selected_pm2_start_script == script_path
    assert choice.pm2_selection_source == "auto"
    assert choice.issues == ()


def test_build_project_choice_state_requires_selection_for_multiple_candidates(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    (project / "app.nginx").write_text("server { listen 80; server_name app.example.com; }", encoding="utf-8")
    (project / "staging.nginx").write_text("server { listen 80; server_name stage.example.com; }", encoding="utf-8")
    (project / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (project / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    inventory = discover_inventory(root, enabled_root)

    choice = build_project_choice_state(inventory.projects[0], make_db(tmp_path))

    assert choice.selected_nginx_config is None
    assert choice.selected_pm2_start_script is None
    assert "nginx_selection_required" in issue_codes(choice)
    assert "pm2_start_script_selection_required" in issue_codes(choice)


def test_saved_selection_is_used_and_not_overwritten_by_single_candidate_scan(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config_path = project / "app.nginx"
    script_path = project / "start.sh"
    config_path.write_text("server { listen 80; server_name app.example.com; }", encoding="utf-8")
    script_path.write_text("#!/bin/sh\n", encoding="utf-8")
    inventory = discover_inventory(root, enabled_root)
    db = make_db(tmp_path)
    db.save_project_selection(
        "demo-app",
        nginx_config_path=str(config_path),
        pm2_start_script_path=str(script_path),
    )

    choice = build_project_choice_state(inventory.projects[0], db)

    assert choice.selected_nginx_config == config_path
    assert choice.nginx_selection_source == "saved"
    assert choice.selected_pm2_start_script == script_path
    assert choice.pm2_selection_source == "saved"


def test_invalid_saved_selection_is_not_replaced_by_auto_choice(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    (project / "app.nginx").write_text("server { listen 80; server_name app.example.com; }", encoding="utf-8")
    (project / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    inventory = discover_inventory(root, enabled_root)
    db = make_db(tmp_path)
    db.save_project_selection(
        "demo-app",
        nginx_config_path="/opt/www/demo-app/old.nginx",
        pm2_start_script_path="/opt/www/demo-app/old.sh",
    )

    choice = build_project_choice_state(inventory.projects[0], db)

    assert choice.selected_nginx_config is None
    assert choice.selected_pm2_start_script is None
    assert "saved_nginx_selection_missing" in issue_codes(choice)
    assert "saved_pm2_start_script_missing" in issue_codes(choice)


def test_missing_start_script_marks_project_incomplete_for_pm2(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    (project / "app.nginx").write_text("server { listen 80; server_name app.example.com; }", encoding="utf-8")
    inventory = discover_inventory(root, enabled_root)

    choice = build_project_choice_state(inventory.projects[0], make_db(tmp_path))

    assert choice.selected_nginx_config == project / "app.nginx"
    assert choice.selected_pm2_start_script is None
    assert "missing_pm2_start_script" in issue_codes(choice)


def test_save_project_choice_rejects_paths_that_are_not_candidates(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    (project / "app.nginx").write_text("server { listen 80; server_name app.example.com; }", encoding="utf-8")
    inventory = discover_inventory(root, enabled_root)

    with pytest.raises(ValueError):
        save_project_choice(
            inventory.projects[0],
            make_db(tmp_path),
            nginx_config_path="/tmp/escape.nginx",
            pm2_start_script_path=None,
        )
