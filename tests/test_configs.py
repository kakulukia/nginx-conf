from pathlib import Path

from nginx_conf.configs import find_project_configs

ROOT = Path(__file__).parent


def test_find_project_configs_finds_demo_app_config() -> None:
    configs = find_project_configs(ROOT / "opt/www")

    assert [(config.project_name, config.config_path.relative_to(ROOT)) for config in configs] == [
        ("demo-app", Path("opt/www/demo-app/+.nginx")),
    ]


def test_find_project_configs_ignores_non_config_files() -> None:
    configs = find_project_configs(ROOT / "opt/www")

    assert all(config.config_path.suffix == ".nginx" for config in configs)
    assert all(config.config_path.name != ".gitkeep" for config in configs)


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
