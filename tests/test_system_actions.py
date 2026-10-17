import subprocess
from pathlib import Path

import pytest

from nginx_conf.github_clone import plan_github_clone
from nginx_conf.store import ActionLockError, AppDatabase
from nginx_conf.system_actions import (
    CERTBOT_EMAIL,
    CommandResult,
    CommandValidationError,
    PathValidationError,
    SubprocessCommandRunner,
    SystemActionService,
    certbot_issue_command,
    certbot_renew_dry_run_command,
    django_manage_command,
    github_clone_helper_command,
    helper_command,
    nginx_reload_command,
    nginx_test_command,
    pm2_lifecycle_command,
    pm2_logs_command,
    pm2_start_command,
    validate_existing_path_under_root,
)


class FakeRunner:
    def __init__(self, result: CommandResult | None = None) -> None:
        self.result = result or CommandResult(exit_code=0, stdout="ok", stderr="")
        self.calls: list[tuple[tuple[str, ...], str | None, int]] = []

    def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
        self.calls.append((argv, stdin, timeout_seconds))
        return self.result


def make_db(tmp_path: Path) -> AppDatabase:
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    return db


def test_nginx_commands_are_fixed_argv_without_shell_strings() -> None:
    assert nginx_test_command().argv == ("/usr/sbin/nginx", "-t")
    assert nginx_reload_command().argv == ("/bin/systemctl", "reload", "nginx")


def test_certbot_command_uses_fixed_non_interactive_template() -> None:
    command = certbot_issue_command(("example.com", "www.example.com"))

    assert command.argv == (
        "/usr/bin/certbot",
        "--nginx",
        "--non-interactive",
        "--agree-tos",
        "--email",
        CERTBOT_EMAIL,
        "--no-eff-email",
        "--keep-until-expiring",
        "-d",
        "example.com",
        "-d",
        "www.example.com",
        "--redirect",
    )
    assert "--cert-name" not in command.argv


def test_certbot_renew_dry_run_command_is_fixed_non_interactive_template() -> None:
    command = certbot_renew_dry_run_command()

    assert command.argv == ("/usr/bin/certbot", "renew", "--dry-run", "--non-interactive")
    assert command.action_type == "certbot-renew-dry-run"
    assert command.timeout_seconds == 1800


@pytest.mark.parametrize("domain", ["_", "localhost", "*.example.com", "127.0.0.1", "internal"])
def test_certbot_command_rejects_non_public_or_unsupported_domains(domain: str) -> None:
    with pytest.raises(CommandValidationError):
        certbot_issue_command((domain,))


def test_path_validation_rejects_escapes_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    project = root / "demo-app"
    project.mkdir(parents=True)
    script = project / "start.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    linked = project / "linked.sh"
    linked.symlink_to(outside)

    assert validate_existing_path_under_root(script, root, expected_file=True) == script.resolve()
    with pytest.raises(PathValidationError):
        validate_existing_path_under_root(outside, root, expected_file=True)
    with pytest.raises(PathValidationError):
        validate_existing_path_under_root(linked, root, expected_file=True)


def test_pm2_commands_are_allowlisted_and_validate_paths(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    project = root / "demo-app"
    project.mkdir(parents=True)
    script = project / "start.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")

    assert pm2_lifecycle_command("restart", "demo-app").argv == ("/usr/bin/pm2", "restart", "demo-app")
    assert pm2_logs_command("demo-app").argv == ("/usr/bin/pm2", "logs", "demo-app", "--lines", "100", "--nostream")

    start = pm2_start_command(name="demo-app", project_root=root, cwd=project, start_script=script)
    assert start.argv == (
        "/usr/bin/pm2",
        "start",
        str(script.resolve()),
        "--name",
        "demo-app",
        "--cwd",
        str(project.resolve()),
    )

    with pytest.raises(CommandValidationError):
        pm2_lifecycle_command("save", "demo-app")
    with pytest.raises(CommandValidationError):
        pm2_logs_command("bad/name")


def test_django_manage_commands_are_allowlisted_and_validate_paths(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    project = root / "demo-app"
    venv = project / ".venv/bin"
    venv.mkdir(parents=True)
    manage_py = project / "manage.py"
    python = venv / "python"
    manage_py.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    python.write_text("#!/bin/sh\n", encoding="utf-8")

    migrate = django_manage_command("migrate", project_root=root, project_path=project)
    collectstatic = django_manage_command("collectstatic", project_root=root, project_path=project)
    compress = django_manage_command("compress", project_root=root, project_path=project)

    assert migrate.argv == (str(python), str(manage_py), "migrate")
    assert collectstatic.argv == (str(python), str(manage_py), "collectstatic", "--noinput")
    assert compress.argv == (str(python), str(manage_py), "compress")
    assert migrate.timeout_seconds == 1800

    with pytest.raises(CommandValidationError):
        django_manage_command("shell", project_root=root, project_path=project)
    with pytest.raises(PathValidationError):
        django_manage_command("migrate", project_root=root, project_path=tmp_path)


def test_django_manage_command_reports_missing_python_environment(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    project = root / "demo-app"
    project.mkdir(parents=True)
    (project / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")

    with pytest.raises(CommandValidationError, match="Python environment"):
        django_manage_command("migrate", project_root=root, project_path=project)


def test_helper_command_uses_fixed_subcommand_and_json_stdin_only() -> None:
    command = helper_command(
        subcommand="commit-config",
        action_id="action-1",
        project="demo-app",
        config_path=Path("/opt/www/demo-app/app.nginx"),
        expected_file=Path("/opt/www/demo-app/app.nginx"),
        payload={"message": "Config aktualisiert"},
    )

    assert command.argv == ("/usr/local/lib/nginx-conf/nginx-conf-helper", "commit-config")
    assert command.parameters == {
        "action_id": "action-1",
        "project": "demo-app",
        "expected_file": "/opt/www/demo-app/app.nginx",
    }
    assert command.stdin_json == {
        "action_id": "action-1",
        "project": "demo-app",
        "config_path": "/opt/www/demo-app/app.nginx",
        "expected_file": "/opt/www/demo-app/app.nginx",
        "message": "Config aktualisiert",
    }

    with pytest.raises(CommandValidationError):
        helper_command(
            subcommand="arbitrary-shell",
            action_id="action-1",
            project="demo-app",
            config_path=Path("/opt/www/demo-app/app.nginx"),
            expected_file=Path("/opt/www/demo-app/app.nginx"),
            payload={},
        )


def test_github_clone_helper_command_uses_fixed_subcommand_and_json_stdin_only(tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project_root.mkdir(parents=True)
    plan = plan_github_clone("https://github.com/freilandkiwis/demo-app.git", project_root=project_root)

    command = github_clone_helper_command(action_id="action-1", plan=plan)

    assert command.argv == ("/usr/local/lib/nginx-conf/nginx-conf-helper", "clone-project")
    assert command.project == "demo-app"
    assert command.parameters == {
        "action_id": "action-1",
        "clone_url": "https://github.com/freilandkiwis/demo-app.git",
        "target_project": "demo-app",
        "target_path": str(project_root.resolve() / "demo-app"),
    }
    assert command.stdin_json == command.parameters


def test_system_action_service_records_status_audit_and_uses_runner_without_shell(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    runner = FakeRunner(CommandResult(exit_code=0, stdout="nginx ok", stderr=""))
    service = SystemActionService(db, runner)

    result = service.run(nginx_test_command(), action_id="action-1")

    assert result.action_id == "action-1"
    assert runner.calls == [((("/usr/sbin/nginx", "-t")), None, 120)]
    action = db.get_action("action-1")
    assert action is not None
    assert action.status == "succeeded"
    assert action.stdout_excerpt == "nginx ok"
    audit = db.list_audit_entries()[0]
    assert audit.action == "nginx-test"
    assert audit.result == "succeeded"


def test_system_action_service_rejects_parallel_action_starts(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    service = SystemActionService(db, FakeRunner())
    service.start_background_action(certbot_issue_command(("example.com",)), action_id="running")

    with pytest.raises(ActionLockError):
        service.run(nginx_reload_command(), action_id="reload")


def test_real_runner_uses_noninteractive_sudo_only_for_fixed_privileged_commands(monkeypatch) -> None:
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr("nginx_conf.system_actions.subprocess.run", fake_run)
    runner = SubprocessCommandRunner()
    for command in (nginx_test_command(), nginx_reload_command(), certbot_renew_dry_run_command()):
        runner.run(command.argv, stdin=None, timeout_seconds=120)
        assert calls[-1][0] == ["/usr/bin/sudo", "-n", *command.argv]
        assert calls[-1][1]["shell"] is False
    runner.run(("/usr/local/lib/nginx-conf/nginx-conf-helper", "write-config"), stdin="{}", timeout_seconds=120)
    assert calls[-1][0][:2] == ["/usr/bin/sudo", "-n"]
    runner.run(certbot_issue_command(("example.com",)).argv, stdin="{}", timeout_seconds=120)
    assert calls[-1][0] == ["/usr/bin/sudo", "-n", "/usr/local/lib/nginx-conf/nginx-conf-helper", "certbot-issue"]
    runner.run(pm2_lifecycle_command("restart", "demo-app").argv, stdin=None, timeout_seconds=120)
    assert calls[-2][0] == ["/usr/bin/pm2", "restart", "demo-app"]
    assert calls[-1][0] == ("/usr/bin/pm2", "save", "--force")
    with pytest.raises(CommandValidationError):
        runner.run(("/usr/local/lib/nginx-conf/nginx-conf-helper", "shell"), stdin=None, timeout_seconds=120)
    with pytest.raises(CommandValidationError):
        runner.run(("/usr/bin/certbot", "--config", "/tmp/arbitrary"), stdin=None, timeout_seconds=120)


@pytest.mark.parametrize("mutation_exit,save_exit,expected_calls", [(0, 1, 2), (1, 0, 1)])
def test_pm2_snapshot_failure_reports_error_without_losing_actual_process_change(
    monkeypatch, mutation_exit, save_exit, expected_calls
) -> None:
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, save_exit if len(calls) == 2 else mutation_exit, "", "failure")

    monkeypatch.setattr("nginx_conf.system_actions.subprocess.run", fake_run)
    result = SubprocessCommandRunner().run(
        pm2_lifecycle_command("start", "demo-app").argv, stdin=None, timeout_seconds=120
    )
    assert result.exit_code == 1
    assert result.state_changed is (mutation_exit == 0)
    assert len(calls) == expected_calls
