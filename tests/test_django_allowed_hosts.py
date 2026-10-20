from pathlib import Path

import pytest

from nginx_conf import django_allowed_hosts
from nginx_conf.configs import discover_inventory
from nginx_conf.django_allowed_hosts import (
    allowed_hosts_inventory_to_dict,
    check_django_allowed_hosts,
    findings_for_config,
    is_django_project,
)


@pytest.fixture(autouse=True)
def process_root(monkeypatch, tmp_path):
    monkeypatch.setattr(django_allowed_hosts, "PROC_ROOT", tmp_path / "proc", raising=False)


def running_settings(tmp_path, project, module, pid="100"):
    process = tmp_path / "proc" / pid
    process.mkdir(parents=True, exist_ok=True)
    cwd = process / "cwd"
    cwd.unlink(missing_ok=True)
    cwd.symlink_to(project, target_is_directory=True)
    (process / "environ").write_bytes(f"DJANGO_SETTINGS_MODULE={module}\0".encode())


def make_project(
    tmp_path: Path,
    *,
    settings_content: str | None,
    config_content: str = "server { listen 80; server_name demo.example.com www.demo.example.com; }\n",
) -> tuple[Path, Path]:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = project_root / "demo-app"
    settings_dir = project / "demo"
    settings_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    (project / "app.nginx").write_text(config_content, encoding="utf-8")
    if settings_content is not None:
        (project / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
        (settings_dir / "settings.py").write_text(settings_content, encoding="utf-8")
        running_settings(tmp_path, project, "demo.settings")
    return project_root, enabled_root


def test_django_allowed_hosts_accepts_static_matching_hosts(tmp_path: Path) -> None:
    project_root, enabled_root = make_project(
        tmp_path,
        settings_content='ALLOWED_HOSTS = ["demo.example.com", ".demo.example.com"]\n',
    )
    inventory = discover_inventory(project_root, enabled_root)

    result = check_django_allowed_hosts(inventory.projects)

    assert result.ok is True
    assert result.findings == ()


def test_django_allowed_hosts_reports_missing_static_hosts_per_server_block(tmp_path: Path) -> None:
    project_root, enabled_root = make_project(
        tmp_path,
        settings_content='ALLOWED_HOSTS = ["demo.example.com"]\n',
    )
    inventory = discover_inventory(project_root, enabled_root)

    result = check_django_allowed_hosts(inventory.projects)

    assert result.ok is False
    finding = result.findings[0]
    assert finding.project_name == "demo-app"
    assert finding.server_block_ordinal == 1
    assert finding.missing_domains == ("www.demo.example.com",)
    assert finding.settings_path.name == "settings.py"
    assert allowed_hosts_inventory_to_dict(result)["findings"][0]["missing_domains"] == ["www.demo.example.com"]


def test_django_allowed_hosts_ignores_non_django_projects(tmp_path: Path) -> None:
    project_root, enabled_root = make_project(tmp_path, settings_content=None)
    inventory = discover_inventory(project_root, enabled_root)

    assert is_django_project(inventory.projects[0].project_path) is False
    assert check_django_allowed_hosts(inventory.projects).ok is True


def test_django_allowed_hosts_reports_dynamic_assignments_as_uncheckable(tmp_path: Path) -> None:
    project_root, enabled_root = make_project(
        tmp_path,
        settings_content='ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")\n',
    )
    inventory = discover_inventory(project_root, enabled_root)

    result = check_django_allowed_hosts(inventory.projects)

    assert result.ok is True
    assert result.findings == ()
    assert result.uncheckable_projects == ("demo-app",)


def test_running_stage_settings_override_common_hosts_without_executing_project_code(tmp_path):
    root, enabled = make_project(tmp_path, settings_content='ALLOWED_HOSTS = ["wrong.example.com"]')
    project = root / "demo-app"
    settings = project / "demo"
    (settings / "__init__.py").write_text("")
    (settings / "stage.py").write_text(
        'from .settings import *\nALLOWED_HOSTS = ["demo.example.com", "www.demo.example.com"]\n'
        'raise RuntimeError("Project code must never execute")\n'
    )
    running_settings(tmp_path, project, "demo.stage")

    result = check_django_allowed_hosts(discover_inventory(root, enabled).projects)

    assert result.ok and result.findings == ()
    assert result.uncheckable_projects == ()


def test_running_settings_inherit_hosts_from_project_local_imports(tmp_path):
    root, enabled = make_project(tmp_path, settings_content='ALLOWED_HOSTS = ["demo.example.com"]')
    project = root / "demo-app"
    (project / "demo/stage.py").write_text(
        'from .settings import ALLOWED_HOSTS\nALLOWED_HOSTS += ["www.demo.example.com"]'
    )
    running_settings(tmp_path, project, "demo.stage")

    result = check_django_allowed_hosts(discover_inventory(root, enabled).projects)

    assert result.ok and result.uncheckable_projects == ()


def test_only_processes_in_this_project_determine_settings_and_conflicts_are_uncheckable(tmp_path):
    root, enabled = make_project(tmp_path, settings_content='ALLOWED_HOSTS = [".demo.example.com"]')
    project = root / "demo-app"
    foreign = tmp_path / "other-project"
    foreign.mkdir()
    running_settings(tmp_path, foreign, "other.settings", pid="200")
    inventory = discover_inventory(root, enabled)
    assert check_django_allowed_hosts(inventory.projects).uncheckable_projects == ()

    running_settings(tmp_path, project, "other.settings", pid="200")
    result = check_django_allowed_hosts(inventory.projects)
    assert result.ok and result.findings == ()
    assert result.uncheckable_projects == ("demo-app",)


@pytest.mark.parametrize(
    "state",
    ["missing_process", "missing_module", "conditional", "mutation", "cycle", "escape", "alias", "exports", "eval"],
)
def test_unresolved_or_dynamic_active_settings_never_fall_back_to_an_unrelated_host_list(tmp_path, state):
    root, enabled = make_project(tmp_path, settings_content='ALLOWED_HOSTS = ["wrong.example.com"]')
    project = root / "demo-app"
    active = project / "demo/stage.py"
    running_settings(tmp_path, project, "demo.stage")
    if state == "missing_process":
        (tmp_path / "proc/100/environ").unlink()
    elif state == "conditional":
        active.write_text('from .settings import *\nif flag:\n    ALLOWED_HOSTS = ["demo.example.com"]\n')
    elif state == "mutation":
        active.write_text('from .settings import *\nALLOWED_HOSTS.append("demo.example.com")\n')
    elif state == "cycle":
        active.write_text("from .stage import *\n")
    elif state == "escape":
        foreign = tmp_path / "outside.py"
        foreign.write_text('ALLOWED_HOSTS = ["wrong.example.com"]')
        active.symlink_to(foreign)
    elif state == "alias":
        active.write_text("from .settings import OTHER as ALLOWED_HOSTS\n")
    elif state == "exports":
        (project / "demo/settings.py").write_text('__all__ = []\nALLOWED_HOSTS = ["wrong.example.com"]\n')
        active.write_text("from .settings import *\n")
    elif state == "eval":
        active.write_text('from .settings import *\neval("ALLOWED_HOSTS.clear()")\n')
    result = check_django_allowed_hosts(discover_inventory(root, enabled).projects)
    assert result.ok and result.findings == ()
    assert result.uncheckable_projects == ("demo-app",)
    assert allowed_hosts_inventory_to_dict(result)["uncheckable_projects"] == ["demo-app"]


def test_django_allowed_hosts_can_check_one_config(tmp_path: Path) -> None:
    project_root, enabled_root = make_project(
        tmp_path,
        settings_content='ALLOWED_HOSTS = ["demo.example.com"] + ["api.demo.example.com"]\n',
        config_content=(
            "server { listen 80; server_name demo.example.com; }\n"
            "server { listen 80; server_name www.demo.example.com; }\n"
        ),
    )
    inventory = discover_inventory(project_root, enabled_root)
    project = inventory.projects[0]
    config = project.configs[0]

    findings = findings_for_config(project, config.config_path)

    assert len(findings) == 1
    assert findings[0].server_block_ordinal == 2
    assert findings[0].missing_domains == ("www.demo.example.com",)
