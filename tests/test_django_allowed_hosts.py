from pathlib import Path

from nginx_conf.configs import discover_inventory
from nginx_conf.django_allowed_hosts import (
    allowed_hosts_inventory_to_dict,
    check_django_allowed_hosts,
    findings_for_config,
    is_django_project,
)


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


def test_django_allowed_hosts_ignores_dynamic_or_unreadable_assignments(tmp_path: Path) -> None:
    project_root, enabled_root = make_project(
        tmp_path,
        settings_content='ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")\n',
    )
    inventory = discover_inventory(project_root, enabled_root)

    result = check_django_allowed_hosts(inventory.projects)

    assert result.ok is True
    assert result.findings == ()


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
