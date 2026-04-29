from pathlib import Path

ROOT = Path(__file__).parent


def test_local_nginx_fixture_tree_exists() -> None:
    project_config = ROOT / "opt/www/demo-app/+.nginx"
    enabled_config = ROOT / "etc/nginx/sites-enabled/demo-app.conf"

    assert project_config.is_file()
    assert project_config.match("*.nginx")
    assert enabled_config.is_symlink()
    assert enabled_config.resolve() == project_config.resolve()
