from pathlib import Path

import pytest

from nginx_conf.configs import certificate_server_group, discover_inventory, find_project_configs, parse_server_blocks

ROOT = Path(__file__).parent
ENABLED_ROOT = ROOT / "etc/nginx/sites-enabled"


def issue_codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_find_project_configs_finds_demo_app_config() -> None:
    configs = find_project_configs(ROOT / "opt/www")

    assert [(config.project_name, config.config_path.relative_to(ROOT)) for config in configs] == [
        ("demo-app", Path("opt/www/demo-app/+.nginx")),
    ]


def test_find_project_configs_does_not_follow_symlink_dirs(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    root.mkdir(parents=True)

    project_dir = root / "demo-app"
    project_dir.mkdir()
    project_config = project_dir / "app.nginx"
    project_config.write_text("# config", encoding="utf-8")

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_config = outside_dir / "escape.nginx"
    outside_config.write_text("# escaped", encoding="utf-8")

    (root / "delphi").symlink_to(outside_dir, target_is_directory=True)

    configs = find_project_configs(root)

    assert [config.config_path for config in configs] == [project_config]
    assert outside_config not in [config.config_path for config in configs]


def test_discover_inventory_keeps_server_name_groups_and_enabled_status() -> None:
    inventory = discover_inventory(ROOT / "opt/www", ENABLED_ROOT)

    assert "project_symlink_skipped" in issue_codes(inventory.issues)
    assert [project.project_name for project in inventory.projects] == ["demo-app"]

    project = inventory.projects[0]
    assert project.domains == ("demo.local", "www.demo.local")
    assert not project.issues

    config = project.configs[0]
    assert config.enabled is True
    assert config.enabled_symlinks[0].link_path.relative_to(ROOT) == Path("etc/nginx/sites-enabled/demo-app.conf")

    assert len(config.server_blocks) == 1
    block = config.server_blocks[0]
    assert block.server_names == ("demo.local", "www.demo.local")
    assert block.listen_ports == (80,)
    assert block.certbot_eligible is True


def test_parse_server_blocks_preserves_multiple_server_blocks(tmp_path: Path) -> None:
    config_path = tmp_path / "app.nginx"
    config_path.write_text(
        """
        server {
            listen 80;
            server_name example.com www.example.com;
            location / { try_files $uri $uri/ =404; }
        }

        server {
            listen [::]:443 ssl;
            server_name _;
        }
        """,
        encoding="utf-8",
    )

    blocks = parse_server_blocks(config_path)

    assert [block.server_names for block in blocks] == [
        ("example.com", "www.example.com"),
        ("_",),
    ]
    assert [block.listen_ports for block in blocks] == [(80,), (443,)]
    assert blocks[0].certbot_eligible is True
    assert blocks[1].certbot_eligible is False
    assert blocks[1].certbot_blockers == ("not_public_domain:_",)


def test_discover_inventory_marks_projects_without_nginx_config_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project_dir = root / "legacy-app"
    project_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    (project_dir / "nginx.conf").write_text("server { server_name ignored.example.com; }", encoding="utf-8")
    (project_dir / "app.nginx-plesk-config").write_text("server { server_name ignored.example.com; }", encoding="utf-8")

    inventory = discover_inventory(root, enabled_root)

    assert len(inventory.projects) == 1
    assert inventory.projects[0].configs == ()
    assert "missing_nginx_config" in issue_codes(inventory.projects[0].issues)


def test_discover_inventory_reports_missing_project_root(tmp_path: Path) -> None:
    inventory = discover_inventory(tmp_path / "missing", tmp_path / "sites-enabled")

    assert inventory.projects == ()
    assert issue_codes(inventory.issues) == {"project_root_missing"}


def test_discover_inventory_rejects_config_symlinks_and_foreign_enabled_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project_dir = root / "demo-app"
    project_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_config = outside_dir / "escape.nginx"
    outside_config.write_text("server { listen 80; server_name escape.example.com; }", encoding="utf-8")

    (project_dir / "linked.nginx").symlink_to(outside_config)
    (enabled_root / "escape.conf").symlink_to(outside_config)

    inventory = discover_inventory(root, enabled_root)

    assert "foreign_enabled_symlink" in issue_codes(inventory.issues)
    assert "config_symlink_skipped" in issue_codes(inventory.projects[0].issues)
    assert "missing_nginx_config" in issue_codes(inventory.projects[0].issues)
    assert inventory.projects[0].configs == ()


def test_discover_inventory_reports_broken_enabled_symlink(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project_dir = root / "demo-app"
    project_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    (project_dir / "app.nginx").write_text("server { listen 80; server_name demo.example.com; }", encoding="utf-8")
    (enabled_root / "broken.conf").symlink_to(tmp_path / "missing.nginx")

    inventory = discover_inventory(root, enabled_root)

    assert "broken_enabled_symlink" in issue_codes(inventory.issues)
    assert inventory.projects[0].configs[0].enabled is False


def test_discover_inventory_reports_duplicate_domains_only_for_active_configs(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    enabled_root.mkdir(parents=True)

    first_project = root / "first"
    second_project = root / "second"
    first_project.mkdir(parents=True)
    second_project.mkdir()
    (first_project / "app.nginx").write_text("server { listen 8080; server_name clash.example.com; }", encoding="utf-8")
    (second_project / "app.nginx").write_text("server { listen 8080; server_name clash.example.com; }", encoding="utf-8")

    assert all(not project.issues for project in discover_inventory(root, enabled_root).projects)
    for project in (first_project, second_project):
        (enabled_root / project.name).symlink_to(project / "app.nginx")
    inventory = discover_inventory(root, enabled_root)

    assert "duplicate_domain" in issue_codes(inventory.projects[0].issues)
    assert "duplicate_listen_port" not in issue_codes(inventory.projects[0].issues)
    assert "duplicate_domain" in issue_codes(inventory.projects[1].issues)
    assert "duplicate_listen_port" not in issue_codes(inventory.projects[1].issues)


def test_http_https_pair_is_one_domain_group_without_duplicate_conflicts(tmp_path: Path) -> None:
    project = tmp_path / "www/demo-app"
    project.mkdir(parents=True)
    config = project / "app.nginx"
    config.write_text(
        "server { listen 80; listen [::]:80; server_name demo.example.com www.demo.example.com; }\n"
        "server { listen 443 ssl; listen [::]:443 ssl; server_name www.demo.example.com demo.example.com; }\n"
    )
    inventory = discover_inventory(project.parent, tmp_path / "enabled")
    discovered = inventory.projects[0].configs[0]
    assert len(discovered.server_blocks) == 2
    assert len(discovered.domain_groups) == 1
    assert discovered.domain_groups[0].ordinal == 2
    assert discovered.domain_groups[0].listen_ports == (80, 443)
    assert "duplicate_domain" not in issue_codes(inventory.projects[0].issues)
    assert "duplicate_listen_port" not in issue_codes(inventory.projects[0].issues)
    assert certificate_server_group(discovered.server_blocks, ("demo.example.com", "www.demo.example.com")) is not None


def test_active_virtual_hosts_can_share_http_https_ports(tmp_path: Path) -> None:
    root = tmp_path / "www"
    enabled = tmp_path / "enabled"
    enabled.mkdir()
    for name in ("first", "second"):
        project = root / name
        project.mkdir(parents=True)
        (project / "app.nginx").write_text(
            f"server {{ listen 80; server_name {name}.example.com; }}\n"
            f"server {{ listen 443 ssl; server_name {name}.example.com; }}\n"
        )
        (enabled / name).symlink_to(project / "app.nginx")
    inventory = discover_inventory(root, enabled)
    for project in inventory.projects:
        assert not project.issues


@pytest.mark.parametrize(
    "extra",
    [
        "server { listen 443 ssl; server_name demo.example.com; }",
        "server { listen 443 ssl; server_name demo.example.com www.demo.example.com; }",
    ],
    ids=["duplicate-https", "different-aliases"],
)
def test_domain_group_preserves_real_config_conflicts(tmp_path: Path, extra: str) -> None:
    project = tmp_path / "www/demo-app"
    project.mkdir(parents=True)
    config = project / "app.nginx"
    config.write_text(
        "server { listen 80; server_name demo.example.com; }\n"
        "server { listen 443 ssl; server_name demo.example.com; }\n" + extra
    )
    enabled = tmp_path / "enabled"
    enabled.mkdir()
    (enabled / "demo-app").symlink_to(config)
    inventory = discover_inventory(project.parent, enabled)
    discovered = inventory.projects[0].configs[0]
    assert "duplicate_domain" in issue_codes(inventory.projects[0].issues)
    assert "duplicate_listen_port" not in issue_codes(inventory.projects[0].issues)
    assert certificate_server_group(discovered.server_blocks, ("demo.example.com",)) is None


def test_config_search_skips_venv_before_symlink_checks_but_keeps_hidden_configs(tmp_path: Path) -> None:
    project = tmp_path / "www/demo"
    (project / ".venv/lib").mkdir(parents=True)
    (project / ".venv/lib64").symlink_to("lib", target_is_directory=True)
    (project / ".venv/dependency.nginx").write_text("server { listen 80; server_name ignored.example.com; }")
    (project / ".nginx").mkdir()
    config = project / ".nginx/stage.nginx"
    config.write_text("server { listen 80; server_name stage.example.com; }")
    (project / ".gitignore").write_text(".nginx/\n")
    inventory = discover_inventory(project.parent, tmp_path / "enabled")
    assert [item.config_path for item in inventory.projects[0].configs] == [config]
    assert not inventory.projects[0].issues


def test_multiple_active_configs_of_one_project_are_reported(tmp_path: Path) -> None:
    project = tmp_path / "www/demo"
    project.mkdir(parents=True)
    enabled = tmp_path / "enabled"
    enabled.mkdir()
    for name in ("stage", "live"):
        config = project / f"{name}.nginx"
        config.write_text(f"server {{ listen 80; server_name {name}.example.com; }}")
        (enabled / name).symlink_to(config)
    inventory = discover_inventory(project.parent, enabled)
    assert "multiple_active_configs" in issue_codes(inventory.projects[0].issues)


def test_config_search_excludes_top_level_venv_project(tmp_path):
    root = tmp_path / "www"
    (root / ".venv").mkdir(parents=True)
    (root / ".venv/app.nginx").write_text("server { listen 80; server_name ignored.example.com; }")
    assert find_project_configs(root) == []
    assert discover_inventory(root, tmp_path / "enabled").projects == ()


@pytest.mark.parametrize("state", ["own", "own-relative", "foreign-target", "foreign-name", "broken", "directory"])
def test_admin_site_is_exempt_only_for_intact_known_enabled_link(tmp_path, state):
    root = tmp_path / "www"
    root.mkdir()
    nginx = tmp_path / "etc/nginx"
    enabled = nginx / "sites-enabled"
    available = nginx / "sites-available"
    enabled.mkdir(parents=True)
    available.mkdir()
    target = available / ("other" if state == "foreign-target" else "nginx-conf")
    if state == "directory":
        target.mkdir()
    elif state != "broken":
        target.write_text("server { listen 80; server_name config.pepperz.de; }")
    raw_target = Path("../sites-available/nginx-conf") if state == "own-relative" else target
    (enabled / ("other" if state == "foreign-name" else "nginx-conf")).symlink_to(raw_target)
    inventory = discover_inventory(root, enabled)
    if state in {"own", "own-relative"}:
        assert not inventory.issues
    else:
        expected = "broken_enabled_symlink" if state == "broken" else "foreign_enabled_symlink"
        assert issue_codes(inventory.issues) == {expected}
