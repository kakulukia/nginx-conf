import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from nginx_conf import logs as logs_module
from nginx_conf import main
from nginx_conf.auth import PASSWORD_HASHER, SESSION_COOKIE_NAME, configure_password
from nginx_conf.certbot_renewal import CertbotRenewalMechanism, CertbotRenewalStatus
from nginx_conf.dns_preflight import DNSPreflightResult, DomainDNSResult
from nginx_conf.helper import handle_helper_request
from nginx_conf.ssl_status import DomainCertificateStatus, ProjectSSLStatus, SSLInventory
from nginx_conf.store import ActionLockError, AppDatabase
from nginx_conf.system_actions import (
    CommandResult,
    SystemActionResult,
    SystemActionService,
    nginx_reload_command,
    nginx_test_command,
)

client = TestClient(main.app)
ROOT = main.Path(__file__).parent


def test_startup_loads_installation_password_file_before_first_login(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    path = tmp_path / "installation-password"
    path.write_text("local-install-password\n", encoding="utf-8")
    path.chmod(0o600)

    with TestClient(main.app, base_url="https://testserver") as browser:
        assert not path.exists()
        assert browser.get("/login").status_code == 200
        assert browser.get("/api/projects").status_code == 401
        response = browser.post("/api/login", data={"password": "local-install-password"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login/password"
        assert browser.get("/api/projects").status_code == 401


def authenticate_test_client(db: AppDatabase) -> None:
    client.cookies.clear()
    session_token = "local-test-session"
    db.create_session(
        session_hash=hashlib.sha256(session_token.encode()).hexdigest(), expires_at="9999-01-01T00:00:00+00:00"
    )
    client.cookies.set(SESSION_COOKIE_NAME, session_token)


def configure_test_roots(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "PROJECT_ROOT", ROOT / "opt/www")
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", ROOT / "etc/nginx/sites-enabled")
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")

    class EmptyPM2Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            return CommandResult(exit_code=0, stdout="[]", stderr="")

    monkeypatch.setattr(main, "get_command_runner", lambda db: EmptyPM2Runner())
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    authenticate_test_client(db)


def configure_temp_project_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = project_root / "demo-app"
    settings_dir = project / "demo"
    settings_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config = project / "app.nginx"
    config.write_text("server { listen 80; server_name demo.local www.demo.local; }\n", encoding="utf-8")
    (project / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (settings_dir / "settings.py").write_text('ALLOWED_HOSTS = ["demo.local"]\n', encoding="utf-8")

    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")

    class EmptyPM2Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            return CommandResult(exit_code=0, stdout="[]", stderr="")

    monkeypatch.setattr(main, "get_command_runner", lambda db: EmptyPM2Runner())
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    authenticate_test_client(db)
    return project, enabled_root, config


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("git", "-C", str(cwd), *args), capture_output=True, text=True, check=True)


def configure_git_project_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
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

    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")

    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.save_project_selection("demo-app", nginx_config_path=config, pm2_start_script_path=None)
    authenticate_test_client(db)
    return project, enabled_root, config


def configure_pm2_project_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project = project_root / "demo-app"
    project.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config = project / "app.nginx"
    start_script = project / "start.sh"
    config.write_text("server { listen 80; server_name demo.example.com; }\n", encoding="utf-8")
    start_script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.save_project_selection("demo-app", nginx_config_path=config, pm2_start_script_path=start_script)
    authenticate_test_client(db)
    return project, config, start_script


def dns_result(domain: str, *, ok: bool = True) -> DNSPreflightResult:
    return DNSPreflightResult(
        domains=(
            DomainDNSResult(
                domain=domain,
                a_records=("203.0.113.10",) if ok else (),
                aaaa_records=(),
                ok=ok,
                blockers=() if ok else ("missing_a_record",),
            ),
        ),
        configured_ipv4=("203.0.113.10",),
        configured_ipv6=(),
        ok=ok,
        blockers=(),
    )


def test_home_page(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    response = client.get("/")

    assert response.status_code == 200
    assert "Nginx-Verwaltungstool" in response.text
    assert "demo-app" in response.text
    assert "demo.local" in response.text
    assert "aktiv" in response.text
    assert "Nginx-Auswahl" in response.text
    assert "nginx -t" in response.text
    assert "/api/projects/clone" in response.text


def test_clone_project_api_runs_helper_and_reports_visible_project(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project_root.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    authenticate_test_client(db)
    calls = []

    class Service:
        def run(self, command, *, action_id=None):
            calls.append((command, action_id))
            assert command.argv == ("/usr/local/lib/nginx-conf/nginx-conf-helper", "clone-project")
            payload = command.stdin_json
            assert payload["clone_url"] == "https://github.com/freilandkiwis/new-app.git"
            Path(payload["target_path"]).mkdir()
            return SystemActionResult(
                action_id=action_id or "clone-action",
                command=command,
                result=CommandResult(
                    exit_code=0,
                    stdout=json.dumps(
                        {
                            "ok": True,
                            "target_project": "new-app",
                            "target_path": payload["target_path"],
                        }
                    ),
                    stderr="",
                ),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/projects/clone", json={"clone_url": "https://github.com/freilandkiwis/new-app"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["clone"]["action_type"] == "helper-clone-project"
    assert payload["clone_result"]["target_project"] == "new-app"
    assert payload["project_visible"] is True
    assert calls[0][0].project == "new-app"


def test_clone_project_api_rejects_non_github_or_existing_target(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    (project_root / "existing").mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    authenticate_test_client(db)

    non_github = client.post("/api/projects/clone", json={"clone_url": "https://gitlab.com/freilandkiwis/new-app.git"})
    existing = client.post("/api/projects/clone", json={"clone_url": "https://github.com/freilandkiwis/existing.git"})

    assert non_github.status_code == 400
    assert "Only GitHub" in non_github.json()["detail"]
    assert existing.status_code == 400
    assert "already exists" in existing.json()["detail"]


def test_home_page_shows_django_deploy_actions_for_django_project(monkeypatch, tmp_path) -> None:
    project, _enabled_root, _config = configure_temp_project_roots(monkeypatch, tmp_path)
    venv = project / ".venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    response = client.get("/")

    assert response.status_code == 200
    assert "Django Deploy" in response.text
    assert "/api/projects/demo-app/django/migrate" in response.text
    assert "/api/projects/demo-app/django/collectstatic" in response.text
    assert "compress nicht verfuegbar" in response.text


def test_home_page_hides_django_deploy_actions_for_non_django_project(monkeypatch, tmp_path) -> None:
    configure_git_project_roots(monkeypatch, tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "Django Deploy" not in response.text
    assert "/django/migrate" not in response.text


def test_django_deploy_action_api_runs_fixed_manage_command(monkeypatch, tmp_path) -> None:
    project, _enabled_root, _config = configure_temp_project_roots(monkeypatch, tmp_path)
    venv = project / ".venv/bin"
    venv.mkdir(parents=True)
    python = venv / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []

    class Service:
        def run(self, command, *, action_id=None):
            calls.append(command)
            return SystemActionResult(
                action_id="django-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="migrated", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/projects/demo-app/django/migrate")

    assert response.status_code == 200
    assert calls[0].argv == (str(python), str(project / "manage.py"), "migrate")
    payload = response.json()
    assert payload["action_type"] == "django-migrate"
    assert payload["ok"] is True


def test_django_deploy_action_api_rejects_missing_environment(monkeypatch, tmp_path) -> None:
    configure_temp_project_roots(monkeypatch, tmp_path)

    response = client.post("/api/projects/demo-app/django/migrate")

    assert response.status_code == 400
    assert "missing_python_environment" in response.json()["detail"]


def test_unconfigured_app_has_only_login_and_no_web_setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "auth.sqlite3")
    browser = TestClient(main.app, follow_redirects=False)
    assert browser.get("/").headers["location"] == "/login"
    assert browser.get("/api/projects").status_code == 401
    page = browser.get("/login")
    assert page.status_code == 200
    assert "Passwort" in page.text and 'name="totp_code"' not in page.text
    assert "Nutzername" not in page.text and "Setup-Token" not in page.text
    assert "disabled" in page.text
    assert main.get_database().get_auth_credentials() is None
    main.get_database().create_session(
        session_hash=hashlib.sha256(b"local-test-session").hexdigest(), expires_at="9999-01-01T00:00:00+00:00"
    )
    browser.cookies.set(SESSION_COOKIE_NAME, "local-test-session")
    assert browser.get("/setup").status_code == 404
    assert browser.post("/api/setup", json={}).status_code == 404


def test_home_page_shows_missing_django_allowed_hosts_at_domain_group(monkeypatch, tmp_path) -> None:
    configure_temp_project_roots(monkeypatch, tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "Django ALLOWED_HOSTS fehlt" in response.text
    assert "www.demo.local" in response.text


def test_configs_index_shows_demo_config(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.get("/configs")

    assert response.status_code == 200
    assert "+.nginx" in response.text
    assert "www.demo.local" in response.text


def test_projects_api_returns_structured_inventory(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.get("/api/projects")

    assert response.status_code == 200
    payload = response.json()
    project = payload["projects"][0]
    config = project["configs"][0]
    assert project["project_name"] == "demo-app"
    assert project["domains"] == ["demo.local", "www.demo.local"]
    assert config["config_name"] == "+.nginx"
    assert config["enabled"] is True
    assert config["server_blocks"][0]["server_names"] == ["demo.local", "www.demo.local"]
    choice = payload["project_choices"][0]
    assert choice["selected_nginx_config"].endswith("tests/opt/www/demo-app/+.nginx")
    assert choice["nginx_selection_source"] == "auto"
    assert choice["selected_pm2_start_script"] is None
    ssl_status = payload["ssl"]["projects"][0]["domains"][0]
    assert ssl_status["state"] == "missing"


def test_ssl_api_reports_missing_certificates(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.get("/api/ssl")

    assert response.status_code == 200
    payload = response.json()
    statuses = {status["domain"]: status for status in payload["projects"][0]["domains"]}
    assert statuses["demo.local"]["state"] == "missing"
    assert statuses["www.demo.local"]["state"] == "missing"


def test_pm2_api_reports_project_processes(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.register_pm2_process(
        project_name="demo-app",
        pm2_name="demo-app",
        pm2_id=0,
        cwd=ROOT / "opt/www/demo-app",
        start_script=ROOT / "opt/www/demo-app/start.sh",
    )

    class PM2Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            assert argv == ("/usr/bin/pm2", "jlist")
            assert stdin is None
            assert timeout_seconds == 120
            return CommandResult(
                exit_code=0,
                stdout=json.dumps(
                    [
                        {
                            "name": "demo-app",
                            "pm_id": 0,
                            "pm2_env": {
                                "pm_cwd": str(ROOT / "opt/www/demo-app"),
                                "status": "online",
                                "restart_time": 1,
                                "env": {"PORT": "8010"},
                            },
                        }
                    ]
                ),
                stderr="",
            )

    monkeypatch.setattr(main, "get_command_runner", lambda db: PM2Runner())

    response = client.get("/api/pm2")

    assert response.status_code == 200
    process = response.json()["processes"][0]
    assert process["name"] == "demo-app"
    assert process["project_name"] == "demo-app"
    assert process["status"] == "online"
    assert process["port"] == 8010
    assert process["restart_count"] == 1
    assert process["managed"] is True


def test_pm2_create_api_uses_selected_start_script_and_registers_process(monkeypatch, tmp_path) -> None:
    project, _config, start_script = configure_pm2_project_roots(monkeypatch, tmp_path)

    class Service:
        def run(self, command):
            assert command.subcommand == "pm2-start"
            assert command.argv == (
                "/usr/bin/pm2",
                "start",
                str(start_script.resolve()),
                "--name",
                "demo-app",
                "--cwd",
                str(project.resolve()),
            )
            return SystemActionResult(
                action_id="pm2-start-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="started", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/projects/demo-app/pm2/create", json={"pm2_name": "demo-app"})

    assert response.status_code == 200
    assert response.json()["action_type"] == "pm2-start"
    managed = AppDatabase(main.DATABASE_PATH).list_managed_pm2_processes("demo-app")
    assert managed[0].pm2_name == "demo-app"
    assert managed[0].cwd == str(project)
    assert managed[0].start_script == str(start_script)


def test_pm2_create_api_rejects_missing_start_script(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.post("/api/projects/demo-app/pm2/create", json={"pm2_name": "demo-app"})

    assert response.status_code == 400
    assert "PM2 start script" in response.json()["detail"]


def test_pm2_lifecycle_api_requires_registered_process(monkeypatch, tmp_path) -> None:
    configure_pm2_project_roots(monkeypatch, tmp_path)

    response = client.post("/api/projects/demo-app/pm2/demo-app/restart")

    assert response.status_code == 404


def test_pm2_lifecycle_api_runs_only_for_registered_process_and_delete_unregisters(monkeypatch, tmp_path) -> None:
    project, _config, start_script = configure_pm2_project_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.register_pm2_process(
        project_name="demo-app",
        pm2_name="demo-app",
        cwd=project,
        start_script=start_script,
    )
    calls = []

    class Service:
        def run(self, command):
            calls.append(command.subcommand)
            return SystemActionResult(
                action_id=f"{command.subcommand}-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="ok", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    restart = client.post("/api/projects/demo-app/pm2/demo-app/restart")
    delete = client.post("/api/projects/demo-app/pm2/demo-app/delete")

    assert restart.status_code == 200
    assert delete.status_code == 200
    assert calls == ["pm2-restart", "pm2-delete"]
    assert AppDatabase(main.DATABASE_PATH).list_managed_pm2_processes("demo-app") == []


def test_pm2_write_actions_use_global_action_lock(monkeypatch, tmp_path) -> None:
    configure_pm2_project_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.create_action(action_type="certbot-issue", target="demo.local")

    response = client.post("/api/projects/demo-app/pm2/create", json={"pm2_name": "demo-app"})

    assert response.status_code == 409


def test_logs_page_shows_system_and_pm2_log_sections(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    system_log = tmp_path / "nginx-error.log"
    system_log.write_text("first\nsecond\n", encoding="utf-8")
    monkeypatch.setattr(
        logs_module,
        "SYSTEM_LOG_PATHS",
        {"nginx-error": ("Nginx error.log", system_log)},
    )

    class Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            if argv == ("/usr/bin/pm2", "jlist"):
                return CommandResult(
                    exit_code=0,
                    stdout=json.dumps(
                        [
                            {
                                "name": "demo-app",
                                "pm_id": 0,
                                "pm2_env": {"pm_cwd": str(ROOT / "opt/www/demo-app"), "status": "online"},
                            }
                        ]
                    ),
                    stderr="",
                )
            assert argv == ("/usr/bin/pm2", "logs", "demo-app", "--lines", "2", "--nostream")
            return CommandResult(exit_code=0, stdout="pm2 line\n", stderr="")

    monkeypatch.setattr(main, "get_command_runner", lambda db: Runner())

    response = client.get("/logs?lines=2")

    assert response.status_code == 200
    assert "Nginx error.log" in response.text
    assert "first" in response.text
    assert "second" in response.text
    assert "PM2 demo-app" in response.text
    assert "pm2 line" in response.text


def test_pm2_log_api_requires_current_pm2_process(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.get("/api/logs/pm2/missing?lines=10")

    assert response.status_code == 400
    assert "Unknown PM2 process" in response.json()["detail"]


def test_pm2_log_api_returns_read_only_logs_without_action_lock(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    active_id = db.create_action(action_type="certbot-issue", target="demo.local")

    class Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            if argv == ("/usr/bin/pm2", "jlist"):
                return CommandResult(
                    exit_code=0,
                    stdout=json.dumps([{"name": "demo-app", "pm_id": 0, "pm2_env": {"status": "online"}}]),
                    stderr="",
                )
            assert argv == ("/usr/bin/pm2", "logs", "demo-app", "--lines", "10", "--nostream")
            return CommandResult(exit_code=0, stdout="pm2 line\n", stderr="")

    monkeypatch.setattr(main, "get_command_runner", lambda db: Runner())

    response = client.get("/api/logs/pm2/demo-app?lines=10")

    assert response.status_code == 200
    assert response.json()["content"] == "pm2 line\n"
    assert AppDatabase(main.DATABASE_PATH).get_active_action().action_id == active_id


def test_git_commit_blocks_missing_django_allowed_hosts(monkeypatch, tmp_path) -> None:
    _project, _enabled_root, config = configure_temp_project_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.save_project_selection("demo-app", nginx_config_path=config, pm2_start_script_path=None)

    response = client.post("/api/projects/demo-app/git/commit", json={"message": "Config aktualisiert"})

    assert response.status_code == 400
    assert "Django ALLOWED_HOSTS fehlt" in response.json()["detail"]
    assert "www.demo.local" in response.json()["detail"]


def test_nginx_reload_blocks_missing_django_allowed_hosts_for_enabled_config(monkeypatch, tmp_path) -> None:
    _project, enabled_root, config = configure_temp_project_roots(monkeypatch, tmp_path)
    (enabled_root / "demo-app.conf").symlink_to(config)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.set_ui_state("nginx.last_test", {"ok": True, "action_id": "test", "exit_code": 0})

    response = client.post("/api/nginx/reload")

    assert response.status_code == 400
    assert "Django ALLOWED_HOSTS fehlt" in response.json()["detail"]
    assert "www.demo.local" in response.json()["detail"]


def test_dns_preflight_api_uses_service(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    class Result:
        ok = True
        configured_ipv4 = ("203.0.113.10",)
        configured_ipv6 = ()
        blockers = ()
        domains = ()

    def fake_check(domains, db):
        assert domains == ["demo.local"]
        assert db.path == main.DATABASE_PATH
        return Result()

    monkeypatch.setattr(main, "check_dns_preflight", fake_check)

    response = client.post("/api/dns/preflight", json={"domains": ["demo.local"]})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_nginx_test_api_runs_action_and_records_reload_gate(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    class Service:
        def __init__(self, db) -> None:
            self.db = db

        def run(self, command):
            assert command.argv == nginx_test_command().argv
            return SystemActionResult(
                action_id="nginx-test-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="syntax ok", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service(db))

    response = client.post("/api/nginx/test")

    assert response.status_code == 200
    assert response.json()["stdout"] == "syntax ok"
    assert AppDatabase(main.DATABASE_PATH).get_ui_state("nginx.last_test")["ok"] is True


def test_nginx_reload_requires_successful_test(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.post("/api/nginx/reload")

    assert response.status_code == 400
    assert "successful nginx -t" in response.json()["detail"]


def test_nginx_reload_api_runs_after_successful_test(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.set_ui_state("nginx.last_test", {"ok": True, "action_id": "test", "exit_code": 0})

    class Service:
        def __init__(self, db) -> None:
            self.db = db

        def run(self, command):
            assert command.argv == nginx_reload_command().argv
            return SystemActionResult(
                action_id="reload-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service(db))

    response = client.post("/api/nginx/reload")

    assert response.status_code == 200
    assert response.json()["action_type"] == "nginx-reload"


def test_config_activation_api_runs_helper_then_nginx_test(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project_dir = project_root / "demo-app"
    project_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config_path = project_dir / "app.nginx"
    config_path.write_text("server { listen 80; server_name demo.example.com; }", encoding="utf-8")
    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.save_project_selection("demo-app", nginx_config_path=config_path, pm2_start_script_path=None)
    authenticate_test_client(db)
    calls = []

    class Service:
        def __init__(self, db) -> None:
            self.db = db

        def run(self, command, *, action_id=None):
            calls.append(command.subcommand)
            if command.subcommand == "activate-config":
                assert command.stdin_json["link_path"] == str(enabled_root / "demo-app.conf")
                return SystemActionResult(
                    action_id=action_id,
                    command=command,
                    result=CommandResult(exit_code=0, stdout='{"ok": true}', stderr=""),
                )
            assert command.argv == nginx_test_command().argv
            return SystemActionResult(
                action_id="nginx-test-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="syntax ok", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service(db))

    response = client.post("/api/projects/demo-app/configs/app.nginx/activate")

    assert response.status_code == 200
    assert calls == ["activate-config", "nginx-test"]
    payload = response.json()
    assert payload["activation"]["action_type"] == "helper-activate-config"
    assert payload["nginx_test"]["action_type"] == "nginx-test"
    assert AppDatabase(main.DATABASE_PATH).get_ui_state("nginx.last_test")["ok"] is True


def test_config_save_api_writes_with_helper_then_runs_nginx_test(monkeypatch, tmp_path) -> None:
    project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)
    calls = []

    class Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            if argv == ("/usr/local/lib/nginx-conf/nginx-conf-helper", "write-config"):
                assert stdin is not None
                calls.append("write-config")
                request = json.loads(stdin)
                assert request["new_content"] == "server { listen 80; server_name new.example.com; }\n"
                exit_code, response = handle_helper_request(
                    "write-config",
                    stdin,
                    db_path=main.DATABASE_PATH,
                    project_root=main.PROJECT_ROOT,
                )
                return CommandResult(exit_code=exit_code, stdout=json.dumps(response), stderr="")
            assert argv == nginx_test_command().argv
            calls.append("nginx-test")
            return CommandResult(exit_code=0, stdout="syntax ok", stderr="")

    runner = Runner()
    monkeypatch.setattr(main, "get_system_action_service", lambda db: SystemActionService(db, runner=runner))

    response = client.post(
        "/api/projects/demo-app/configs/app.nginx/save",
        json={"content": "server { listen 80; server_name new.example.com; }\n"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["write-config", "nginx-test"]
    assert payload["write"]["action_type"] == "helper-write-config"
    assert payload["write_result"]["diff_stat"]["files"] == 1
    assert payload["nginx_test"]["ok"] is True
    assert payload["git"]["commit_required"] is True
    assert "new.example.com" in payload["diff"]
    assert config.read_text(encoding="utf-8") == "server { listen 80; server_name new.example.com; }\n"
    state = AppDatabase(main.DATABASE_PATH).get_ui_state(f"config-edit-test:demo-app:{config}")
    assert state["ok"] is True
    assert run_git(project, "status", "--porcelain", "--", "app.nginx").stdout.startswith(" M app.nginx")


def test_config_detail_shows_static_asset_candidates(monkeypatch, tmp_path) -> None:
    project, _enabled_root, _config = configure_git_project_roots(monkeypatch, tmp_path)
    (project / "static").mkdir()

    response = client.get("/configs/demo-app/app.nginx")

    assert response.status_code == 200
    assert "Static Assets" in response.text
    assert "static" in response.text
    assert "/static/" in response.text
    assert "/static-location" in response.text


def test_static_location_api_writes_location_runs_nginx_test_and_reports_git(monkeypatch, tmp_path) -> None:
    project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)
    static_dir = project / "static"
    static_dir.mkdir()
    calls = []

    class Service:
        def run(self, command, *, action_id=None):
            calls.append(command.subcommand)
            if command.subcommand == "write-config":
                payload = command.stdin_json
                config.write_text(payload["new_content"], encoding="utf-8")
                return SystemActionResult(
                    action_id=action_id or "write-action",
                    command=command,
                    result=CommandResult(
                        exit_code=0,
                        stdout=json.dumps({"ok": True, "diff_stat": {"files": 1, "insertions": 4, "deletions": 0}}),
                        stderr="",
                    ),
                )
            assert command.subcommand == "nginx-test"
            return SystemActionResult(
                action_id="nginx-test",
                command=command,
                result=CommandResult(exit_code=0, stdout="syntax ok", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post(
        "/api/projects/demo-app/configs/app.nginx/static-location",
        json={"static_path": "static"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["write-config", "nginx-test"]
    assert payload["changed"] is True
    assert payload["static_asset"]["location_path"] == "/static/"
    assert payload["nginx_test"]["ok"] is True
    assert payload["git"]["commit_required"] is True
    content = config.read_text(encoding="utf-8")
    assert "location /static/ {" in content
    assert f"alias {static_dir.resolve()}/;" in content


def test_static_location_api_rejects_unlisted_static_path(monkeypatch, tmp_path) -> None:
    configure_git_project_roots(monkeypatch, tmp_path)

    response = client.post(
        "/api/projects/demo-app/configs/app.nginx/static-location",
        json={"static_path": "../static"},
    )

    assert response.status_code == 400
    assert "relative project path" in response.json()["detail"]


def test_home_page_shows_certbot_issue_action_for_missing_cert(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "Zertifikat ausstellen" in response.text
    assert "/server-blocks/1/certbot/issue" in response.text


def test_home_page_shows_renewal_check_for_expiring_certificate(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    expires_at = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    ssl_inventory = SSLInventory(
        projects=(
            ProjectSSLStatus(
                project_name="demo-app",
                domains=(
                    DomainCertificateStatus(
                        domain="demo.local",
                        state="red",
                        certificate_name="demo.local",
                        expires_at=expires_at,
                        days_remaining=13,
                        relative_expiry="in 13 Tagen",
                        cert_path=Path("/etc/letsencrypt/live/demo.local/cert.pem"),
                        fullchain_path=None,
                        chain_path=None,
                        privkey_path=None,
                    ),
                ),
            ),
        ),
        unassigned_lineages=(),
    )
    renewal_status = CertbotRenewalStatus(
        mechanisms=(
            CertbotRenewalMechanism(
                kind="systemd_timer",
                name="certbot.timer",
                path=Path("/etc/systemd/system/timers.target.wants/certbot.timer"),
                status="enabled",
            ),
        )
    )
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    action_id = db.create_action(action_type="certbot-renew-dry-run", target="certbot-renew")
    db.finish_action(action_id, status="succeeded", exit_code=0, stdout_excerpt="dry run ok")
    monkeypatch.setattr(main, "discover_ssl_inventory", lambda projects, letsencrypt_root: ssl_inventory)
    monkeypatch.setattr(main, "inspect_certbot_renewal", lambda: renewal_status)

    response = client.get("/")

    assert response.status_code == 200
    assert "Renewal-Pruefung" in response.text
    assert "Auto-Erneuerung gefunden" in response.text
    assert "Letzter Dry-Run: succeeded" in response.text
    assert "/api/certbot/renew/dry-run" in response.text


def test_certbot_renew_dry_run_api_runs_fixed_action(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    calls = []

    class Service:
        def run(self, command, *, action_id=None):
            calls.append(command)
            return SystemActionResult(
                action_id="renew-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="dry run ok", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/certbot/renew/dry-run")

    assert response.status_code == 200
    assert calls[0].argv == ("/usr/bin/certbot", "renew", "--dry-run", "--non-interactive")
    payload = response.json()
    assert payload["action_type"] == "certbot-renew-dry-run"
    assert payload["exit_code"] == 0


def test_certbot_renewal_api_reports_mechanisms_and_last_dry_run(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    renewal_status = CertbotRenewalStatus(
        mechanisms=(
            CertbotRenewalMechanism(
                kind="cron",
                name="certbot",
                path=Path("/etc/cron.d/certbot"),
                status="configured",
            ),
        )
    )
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    action_id = db.create_action(action_type="certbot-renew-dry-run", target="certbot-renew")
    db.finish_action(action_id, status="failed", exit_code=1, stderr_excerpt="dry run failed")
    monkeypatch.setattr(main, "inspect_certbot_renewal", lambda: renewal_status)

    response = client.get("/api/certbot/renewal")

    assert response.status_code == 200
    payload = response.json()
    assert payload["renewal"]["auto_renewal_configured"] is True
    assert payload["renewal"]["mechanisms"][0]["kind"] == "cron"
    assert payload["last_dry_run"]["action_id"] == action_id
    assert payload["last_dry_run"]["status"] == "failed"


def test_certbot_issue_api_runs_preflight_certbot_posttest_and_reload(monkeypatch, tmp_path) -> None:
    project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)
    calls = []

    def fake_dns(domains, db):
        assert tuple(domains) == ("old.example.com",)
        assert db.path == main.DATABASE_PATH
        return dns_result("old.example.com")

    class Service:
        def run(self, command, *, action_id=None):
            calls.append(command.subcommand)
            if command.subcommand == "certbot-issue":
                assert command.argv == (
                    "/usr/bin/certbot",
                    "--nginx",
                    "--non-interactive",
                    "--agree-tos",
                    "--email",
                    "andy@freilandkiwis.de",
                    "--no-eff-email",
                    "--keep-until-expiring",
                    "-d",
                    "old.example.com",
                    "--redirect",
                )
                config.write_text(
                    "server { listen 443 ssl; server_name old.example.com; ssl_certificate /etc/letsencrypt/live/old.example.com/fullchain.pem; }\n",
                    encoding="utf-8",
                )
                return SystemActionResult(
                    action_id="certbot-action",
                    command=command,
                    result=CommandResult(exit_code=0, stdout="certbot ok", stderr=""),
                )
            if command.subcommand == "nginx-reload":
                return SystemActionResult(
                    action_id="reload-action",
                    command=command,
                    result=CommandResult(exit_code=0, stdout="", stderr=""),
                )
            assert command.subcommand == "nginx-test"
            return SystemActionResult(
                action_id=f"nginx-test-{len(calls)}",
                command=command,
                result=CommandResult(exit_code=0, stdout="syntax ok", stderr=""),
            )

    monkeypatch.setattr(main, "check_dns_preflight", fake_dns)
    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/projects/demo-app/configs/app.nginx/server-blocks/1/certbot/issue")

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["nginx-test", "certbot-issue", "nginx-test", "nginx-reload"]
    assert payload["ok"] is True
    assert payload["domains"] == ["old.example.com"]
    assert payload["dns_preflight"]["ok"] is True
    assert payload["commit_required"] is True
    assert payload["git"]["changed_files"] == ["app.nginx"]
    assert AppDatabase(main.DATABASE_PATH).get_ui_state("nginx.last_test")["ok"] is True
    audit = AppDatabase(main.DATABASE_PATH).list_audit_entries()[0]
    assert audit.action == "certbot-issue-workflow"
    assert audit.result == "succeeded"
    assert run_git(project, "status", "--porcelain", "--", "app.nginx").stdout.startswith(" M app.nginx")


def test_certbot_issue_api_blocks_dns_failure_before_certbot(monkeypatch, tmp_path) -> None:
    configure_git_project_roots(monkeypatch, tmp_path)
    calls = []

    class Service:
        def run(self, command, *, action_id=None):
            calls.append(command.subcommand)
            assert command.subcommand == "nginx-test"
            return SystemActionResult(
                action_id="nginx-test",
                command=command,
                result=CommandResult(exit_code=0, stdout="syntax ok", stderr=""),
            )

    monkeypatch.setattr(main, "check_dns_preflight", lambda domains, db: dns_result("old.example.com", ok=False))
    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/projects/demo-app/configs/app.nginx/server-blocks/1/certbot/issue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "dns_preflight_failed"
    assert payload["certbot"] is None
    assert calls == ["nginx-test"]


def test_certbot_issue_api_rejects_non_certbot_capable_block(monkeypatch, tmp_path) -> None:
    _project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)
    config.write_text("server { listen 80; server_name _; }\n", encoding="utf-8")

    response = client.post("/api/projects/demo-app/configs/app.nginx/server-blocks/1/certbot/issue")

    assert response.status_code == 400
    assert "not certbot-capable" in response.json()["detail"]


def test_certbot_issue_api_blocks_uncontrolled_git_diff_before_reload(monkeypatch, tmp_path) -> None:
    _project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)
    calls = []

    class Service:
        def run(self, command, *, action_id=None):
            calls.append(command.subcommand)
            if command.subcommand == "certbot-issue":
                config.write_text(
                    "server { listen 443 ssl; server_name old.example.com; ssl_certificate /tmp/fullchain.pem; }\n",
                    encoding="utf-8",
                )
                (config.parent / "other.nginx").write_text(
                    "server { server_name other.example.com; }\n", encoding="utf-8"
                )
                return SystemActionResult(
                    action_id="certbot-action",
                    command=command,
                    result=CommandResult(exit_code=0, stdout="certbot ok", stderr=""),
                )
            assert command.subcommand == "nginx-test"
            return SystemActionResult(
                action_id=f"nginx-test-{len(calls)}",
                command=command,
                result=CommandResult(exit_code=0, stdout="syntax ok", stderr=""),
            )

    monkeypatch.setattr(main, "check_dns_preflight", lambda domains, db: dns_result("old.example.com"))
    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/projects/demo-app/configs/app.nginx/server-blocks/1/certbot/issue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "uncontrolled_git_diff"
    assert payload["reload"] is None
    assert calls == ["nginx-test", "certbot-issue", "nginx-test"]


def test_config_save_api_rejects_action_lock(monkeypatch, tmp_path) -> None:
    _project, _enabled_root, _config = configure_git_project_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.create_action(action_type="certbot", target="demo.local")

    response = client.post("/api/projects/demo-app/configs/app.nginx/save", json={"content": "server {}\n"})

    assert response.status_code == 409


def test_config_save_api_surfaces_helper_rejection_without_nginx_test(monkeypatch, tmp_path) -> None:
    _project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)

    class Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            assert argv == ("/usr/local/lib/nginx-conf/nginx-conf-helper", "write-config")
            assert stdin is not None
            return CommandResult(
                exit_code=1,
                stdout=json.dumps({"ok": False, "error": "helper rejected"}),
                stderr="",
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: SystemActionService(db, runner=Runner()))

    response = client.post(
        "/api/projects/demo-app/configs/app.nginx/save",
        json={"content": "server { listen 80; server_name rejected.example.com; }\n"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["write"]["ok"] is False
    assert payload["write_result"]["error"] == "helper rejected"
    assert payload["nginx_test"] is None
    assert "rejected.example.com" not in config.read_text(encoding="utf-8")


def test_git_commit_blocks_failed_save_nginx_test(monkeypatch, tmp_path) -> None:
    _project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)
    config.write_text("server { listen 80; server_name dirty.example.com; }\n", encoding="utf-8")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.set_ui_state(
        f"config-edit-test:demo-app:{config}",
        {"ok": False, "action_id": "nginx-test", "exit_code": 1, "project": "demo-app", "config_path": str(config)},
    )

    response = client.post("/api/projects/demo-app/git/commit", json={"message": "Config aktualisiert"})

    assert response.status_code == 400
    assert "successful nginx -t" in response.json()["detail"]


def test_git_reset_runs_nginx_test_and_clears_edit_gate(monkeypatch, tmp_path) -> None:
    _project, _enabled_root, config = configure_git_project_roots(monkeypatch, tmp_path)
    config.write_text("server { listen 80; server_name dirty.example.com; }\n", encoding="utf-8")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    db.set_ui_state(
        f"config-edit-test:demo-app:{config}",
        {"ok": False, "action_id": "nginx-test", "exit_code": 1, "project": "demo-app", "config_path": str(config)},
    )

    class Service:
        def run(self, command):
            assert command.argv == nginx_test_command().argv
            return SystemActionResult(
                action_id="nginx-test-action",
                command=command,
                result=CommandResult(exit_code=0, stdout="syntax ok", stderr=""),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/projects/demo-app/git/reset")

    assert response.status_code == 200
    assert response.json()["nginx_test"]["ok"] is True
    assert "dirty.example.com" not in config.read_text(encoding="utf-8")
    assert AppDatabase(main.DATABASE_PATH).get_ui_state(f"config-edit-test:demo-app:{config}") is None


def test_nginx_actions_return_conflict_when_action_lock_is_active(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    class Service:
        def run(self, command):
            raise ActionLockError("Aktion laeuft bereits")

    monkeypatch.setattr(main, "get_system_action_service", lambda db: Service())

    response = client.post("/api/nginx/test")

    assert response.status_code == 409


def test_project_selection_api_persists_valid_candidates(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project_dir = project_root / "demo-app"
    project_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config_path = project_dir / "app.nginx"
    script_path = project_dir / "start.sh"
    config_path.write_text("server { listen 80; server_name demo.example.com; }", encoding="utf-8")
    script_path.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    authenticate_test_client(db)

    response = client.post(
        "/api/projects/demo-app/selection",
        json={
            "nginx_config_path": str(config_path),
            "pm2_start_script_path": str(script_path),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_nginx_config"] == str(config_path)
    assert payload["selected_pm2_start_script"] == str(script_path)
    assert payload["nginx_selection_source"] == "saved"
    assert payload["pm2_selection_source"] == "saved"

    follow_up = client.get("/api/projects")
    assert follow_up.json()["project_choices"][0]["pm2_selection_source"] == "saved"


def test_project_selection_api_rejects_unknown_candidate(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.post(
        "/api/projects/demo-app/selection",
        json={"nginx_config_path": "/tmp/escape.nginx", "pm2_start_script_path": None},
    )

    assert response.status_code == 400


def test_project_git_api_reports_missing_repo(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "opt/www"
    enabled_root = tmp_path / "etc/nginx/sites-enabled"
    project_dir = project_root / "demo-app"
    project_dir.mkdir(parents=True)
    enabled_root.mkdir(parents=True)
    config_path = project_dir / "app.nginx"
    config_path.write_text("server { listen 80; server_name demo.example.com; }", encoding="utf-8")
    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "SITES_ENABLED_ROOT", enabled_root)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "nginx-conf.sqlite3")
    monkeypatch.setattr(main, "LETSENCRYPT_ROOT", tmp_path / "etc/letsencrypt")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    authenticate_test_client(db)

    response = client.get("/api/projects/demo-app/git")

    assert response.status_code == 200
    payload = response.json()
    assert payload["git_available"] is False
    assert payload["error"] == "Projekt ist kein Git-Repository."


def test_action_status_endpoints_are_read_only(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    action_id = db.create_action(action_type="certbot-issue", target="demo.local")

    active = client.get("/api/actions/active")

    assert active.status_code == 200
    assert active.json()["active_action"]["action_id"] == action_id

    db.finish_action(action_id, status="failed", exit_code=1, stderr_excerpt="certbot failed")
    last_result = client.get("/api/actions/last-result")

    assert last_result.status_code == 200
    payload = last_result.json()["last_action_result"]
    assert payload["status"] == "failed"
    assert payload["stderr_excerpt"] == "certbot failed"


def test_action_result_banner_can_be_acknowledged(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    action_id = db.create_action(action_type="nginx-test", target="nginx")
    db.finish_action(action_id, status="succeeded", exit_code=0, stdout_excerpt="syntax ok")

    banner = client.get("/")

    assert banner.status_code == 200
    assert f"/api/actions/{action_id}/ack" in banner.text
    assert "syntax ok" in banner.text

    response = client.post(f"/api/actions/{action_id}/ack", follow_redirects=False)

    assert response.status_code == 303
    assert AppDatabase(main.DATABASE_PATH).get_last_unacknowledged_result() is None


def test_config_detail_shows_config_content(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.get("/configs/demo-app/+.nginx")

    assert response.status_code == 200
    assert "server_name" in response.text
    assert "demo-app" in response.text


def test_config_detail_rejects_free_path(monkeypatch, tmp_path) -> None:
    configure_test_roots(monkeypatch, tmp_path)

    response = client.get("/configs/demo-app/../../etc/passwd")

    assert response.status_code == 404


def test_config_editor_escapes_diff_and_scopes_reset_confirmation(monkeypatch, tmp_path) -> None:
    _, _, config = configure_git_project_roots(monkeypatch, tmp_path)
    config.write_text('server { listen 80; }\n# </textarea><script>alert("example")</script>\n', encoding="utf-8")

    response = client.get("/configs/demo-app/app.nginx")

    assert response.status_code == 200
    assert '<script>alert("example")</script>' not in response.text
    assert "&lt;/textarea&gt;&lt;script&gt;" in response.text
    assert 'aria-labelledby="reset-config-title"' in response.text
    assert 'action="/api/projects/demo-app/git/reset"' in response.text
    assert 'id="cancel-reset" autofocus' in response.text


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_password_totp_login_sets_secure_cookie_and_rejects_replay(monkeypatch, tmp_path) -> None:
    import time

    import pyotp

    from nginx_conf.auth import session_valid

    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "auth.sqlite3")
    db = main.get_database()
    password = "local-test-password"
    secret = "JBSWY3DPEHPK3PXP"
    now = int(time.time())
    db.save_auth_credentials(
        password_hash=PASSWORD_HASHER.hash(password), totp_secret=secret, last_totp_step=(now - 60) // 30
    )
    browser = TestClient(main.app, base_url="https://config.pepperz.de", follow_redirects=False)
    page = browser.get("/login")
    assert 'name="totp_code"' in page.text and 'class="nc-otp__input"' in page.text
    assert 'src="https://config.pepperz.de/static/otp.js"' in page.text
    assert 'placeholder="000000"' not in page.text
    for payload in ({}, {"password": password, "totp_code": "wrong"}):
        response = browser.post("/api/login", data=payload)
        assert response.status_code == 401 and 'role="alert"' in response.text
        assert password not in response.text
    data = {"password": password, "totp_code": pyotp.TOTP(secret).at(now)}
    assert browser.post("/api/login", data=data, headers={"Origin": "https://foreign.example"}).status_code == 403
    response = browser.post("/api/login", data=data)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    token = browser.cookies.get(SESSION_COOKIE_NAME)
    assert browser.get("/login").headers["location"] == "/"
    assert browser.post("/api/logout").headers["location"] == "/login"
    assert not session_valid(db, token)
    assert browser.post("/api/login", json=data).status_code == 401
    assert db.list_server_ips() == []


def test_login_rate_limit_returns_retry_after(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "auth.sqlite3")
    configure_password(main.get_database(), password="local-test-password")
    browser = TestClient(main.app)
    for _ in range(5):
        assert browser.post("/api/login", data={"password": "wrong", "totp_code": "123456"}).status_code == 401
    response = browser.post("/api/login", data={"password": "wrong", "totp_code": "123456"})
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


def test_pm2_save_failure_keeps_registry_consistent_with_real_create_and_delete(monkeypatch, tmp_path) -> None:
    configure_pm2_project_roots(monkeypatch, tmp_path)

    class PartialService:
        def run(self, command):
            return SystemActionResult(
                action_id="partial",
                command=command,
                result=CommandResult(1, "changed", "save failed", state_changed=True),
            )

    monkeypatch.setattr(main, "get_system_action_service", lambda db: PartialService())
    response = client.post("/api/projects/demo-app/pm2/create", json={"pm2_name": "demo-app"})
    assert response.status_code == 200 and response.json()["ok"] is False
    assert len(main.get_database().list_managed_pm2_processes("demo-app")) == 1
    response = client.post("/api/projects/demo-app/pm2/demo-app/delete")
    assert response.status_code == 200 and response.json()["ok"] is False
    assert main.get_database().list_managed_pm2_processes("demo-app") == []


def test_first_login_enrolls_authenticator_before_granting_app_access(monkeypatch, tmp_path) -> None:
    import time

    import pyotp

    from nginx_conf import auth

    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "auth.sqlite3")
    now = int(time.time())
    monkeypatch.setattr(auth.time, "time", lambda: now)
    db = main.get_database()
    password = "local-test-password"
    configure_password(db, password=password)
    browser = TestClient(main.app, base_url="https://config.pepperz.de", follow_redirects=False)
    assert browser.get("/login/authenticator").status_code == 401
    assert browser.post("/api/login/authenticator", data={"totp_code": "123456"}).status_code == 401
    assert 'name="totp_code"' not in browser.get("/login").text
    assert browser.post("/api/login", data={"password": "wrong"}).status_code == 401
    response = browser.post("/api/login", data={"password": password}, headers={"Origin": "https://config.pepperz.de"})
    assert response.status_code == 303 and response.headers["location"] == "/login/password"
    assert SESSION_COOKIE_NAME not in browser.cookies
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=600" in cookie
    assert browser.get("/").headers["location"] == "/login"
    assert browser.get("/api/projects").status_code == 401
    proof = browser.cookies.get(auth.ENROLLMENT_COOKIE_NAME)
    assert auth.enrollment_secret(db, proof) is None
    assert browser.get("/login/authenticator").headers["location"] == "/login/password"
    assert (
        browser.post("/api/login/authenticator", data={"totp_code": "123456"}).headers["location"] == "/login/password"
    )
    page = browser.get("/login/password")
    assert page.status_code == 200 and "Setze dein Passwort" in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "same-origin"
    new_password = "my-new-local-password"
    data = {"password": new_password, "password_confirmation": new_password}
    assert (
        browser.post("/api/login/password", data=data, headers={"Origin": "https://foreign.example"}).status_code == 403
    )
    invalid = browser.post("/api/login/password", data={"password": new_password, "password_confirmation": "other"})
    assert invalid.status_code == 400 and "stimmen nicht überein" in invalid.text
    assert new_password not in invalid.text
    response = browser.post("/api/login/password", data=data)
    assert response.status_code == 303 and response.headers["location"] == "/login/authenticator"
    assert SESSION_COOKIE_NAME not in browser.cookies
    assert browser.cookies.get(auth.ENROLLMENT_COOKIE_NAME) != proof
    assert browser.get("/api/projects").status_code == 401
    assert not db.get_auth_credentials().password_change_required
    assert auth.PASSWORD_HASHER.verify(db.get_auth_credentials().password_hash, new_password)
    assert auth.enrollment_secret(db, proof) is None
    proof = browser.cookies.get(auth.ENROLLMENT_COOKIE_NAME)
    secret = auth.enrollment_secret(db, proof)
    page = browser.get("/login/authenticator")
    assert page.status_code == 200 and "<svg" in page.text and secret in page.text
    assert 'name="totp_code"' in page.text and 'class="nc-otp__input"' in page.text
    assert 'placeholder="000000"' not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "same-origin"
    assert browser.get("/login/authenticator").text == page.text
    assert auth.enrollment_secret(db, proof) == secret
    assert secret not in browser.get("/login").text
    code = pyotp.TOTP(secret).at(now)
    rejected = browser.post(
        "/api/login/authenticator", data={"totp_code": code}, headers={"Origin": "https://foreign.example"}
    )
    assert rejected.status_code == 403
    assert (
        browser.post("/api/login/authenticator", data={"totp_code": code}, headers={"Origin": "null"}).status_code == 403
    )
    invalid = browser.post("/api/login/authenticator", data={"totp_code": "wrong"})
    assert invalid.status_code == 401 and 'role="alert"' in invalid.text and secret in invalid.text
    assert db.get_auth_credentials().totp_secret == ""
    response = browser.post(
        "/api/login/authenticator", data={"totp_code": code}, headers={"Origin": "https://config.pepperz.de"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert SESSION_COOKIE_NAME in browser.cookies and auth.ENROLLMENT_COOKIE_NAME not in browser.cookies
    assert auth.session_valid(db, browser.cookies.get(SESSION_COOKIE_NAME))
    assert browser.get("/login").headers["location"] == "/"
    assert "<svg" not in browser.get("/login/authenticator").text
    browser.post("/api/logout")
    assert 'name="totp_code"' in browser.get("/login").text
    assert browser.post("/api/login", data={"password": new_password}).status_code == 401


def test_initial_password_page_and_submit_require_password_proof(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "auth.sqlite3")
    db = main.get_database()
    configure_password(db, password="local-test-password")
    browser = TestClient(main.app, follow_redirects=False)
    assert browser.get("/login/password").status_code == 401
    assert (
        browser.post(
            "/api/login/password", data={"password": "my-new-password", "password_confirmation": "my-new-password"}
        ).status_code
        == 401
    )
    assert db.get_auth_credentials().password_change_required


def test_expired_enrollment_returns_to_password_login(monkeypatch, tmp_path) -> None:
    from nginx_conf import auth

    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "auth.sqlite3")
    clock = [1_700_000_000]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    db = main.get_database()
    configure_password(db, password="local-test-password")
    browser = TestClient(main.app, follow_redirects=False)
    browser.post("/api/login", data={"password": "local-test-password"})
    clock[0] += 600
    page = browser.post("/api/login/authenticator", data={"totp_code": "123456"})
    assert page.status_code == 401 and "abgelaufen" in page.text
    assert "<svg" not in page.text and auth.ENROLLMENT_COOKIE_NAME not in browser.cookies
    assert db.get_auth_credentials().totp_secret == ""
