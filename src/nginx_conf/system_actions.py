from __future__ import annotations

import ipaddress
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from nginx_conf.github_clone import GitHubClonePlan
from nginx_conf.store import AppDatabase

NGINX_BIN = "/usr/sbin/nginx"
SYSTEMCTL_BIN = "/bin/systemctl"
CERTBOT_BIN = "/usr/bin/certbot"
PM2_BIN = "/usr/bin/pm2"
CONFIG_HELPER_BIN = "/usr/local/lib/nginx-conf/nginx-conf-helper"
SUDO_BIN = "/usr/bin/sudo"
CERTBOT_EMAIL = "andy@freilandkiwis.de"
DEFAULT_TIMEOUT_SECONDS = 120
LONG_RUNNING_TIMEOUT_SECONDS = 1800
PM2_OPERATIONS_WITH_NAME = {"delete", "reload", "restart", "start", "stop"}
DJANGO_MANAGE_OPERATIONS = {
    "collectstatic": ("collectstatic", "--noinput"),
    "compress": ("compress",),
    "migrate": ("migrate",),
}


class PathValidationError(ValueError):
    pass


class CommandValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    state_changed: bool = False


@dataclass(frozen=True, slots=True)
class CommandSpec:
    action_type: str
    subcommand: str
    target: str
    argv: tuple[str, ...]
    project: str | None = None
    config_path: Path | None = None
    expected_file: Path | None = None
    parameters: dict[str, object] | None = None
    stdin_json: dict[str, object] | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class SystemActionResult:
    action_id: str
    command: CommandSpec
    result: CommandResult


class CommandRunner(Protocol):
    def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
        pass


class SubprocessCommandRunner:
    def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
        if argv in {nginx_test_command().argv, nginx_reload_command().argv, certbot_renew_dry_run_command().argv}:
            argv = (SUDO_BIN, "-n", *argv)
        elif len(argv) == 2 and argv[0] == CONFIG_HELPER_BIN:
            if argv[1] not in {
                "activate-config",
                "deactivate-config",
                "write-config",
                "reset-config-to-head",
                "commit-config",
                "clone-project",
            }:
                raise CommandValidationError("Unsupported privileged helper command")
            argv = (SUDO_BIN, "-n", *argv)
        elif argv and argv[0] == CERTBOT_BIN:
            domains = argv[9:-1:2]
            if certbot_issue_command(domains).argv != argv:
                raise CommandValidationError("Unsupported privileged Certbot command")
            argv = (SUDO_BIN, "-n", CONFIG_HELPER_BIN, "certbot-issue")
        completed = subprocess.run(
            list(argv),
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout_seconds,
        )
        if completed.returncode == 0 and len(argv) > 1 and argv[0] == PM2_BIN and argv[1] in PM2_OPERATIONS_WITH_NAME:
            saved = subprocess.run(
                (PM2_BIN, "save", "--force"),
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            if saved.returncode != 0:
                return CommandResult(
                    saved.returncode,
                    completed.stdout,
                    "PM2-Aktion ausgefuehrt, aber Prozessliste nicht gespeichert: " + saved.stderr,
                    state_changed=True,
                )
        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class SystemActionService:
    def __init__(self, db: AppDatabase, runner: CommandRunner | None = None) -> None:
        self.db = db
        self.runner = runner or SubprocessCommandRunner()

    def start_background_action(self, command: CommandSpec, *, action_id: str | None = None) -> str:
        return self.db.create_action(
            action_id=action_id,
            action_type=command.action_type,
            target=command.target,
            project=command.project,
            config_path=command.config_path,
            subcommand=command.subcommand,
            expected_file=command.expected_file,
            parameters=command.parameters or {},
        )

    def run(self, command: CommandSpec, *, action_id: str | None = None) -> SystemActionResult:
        action_id = self.start_background_action(command, action_id=action_id)
        stdin = json.dumps(command.stdin_json, sort_keys=True) if command.stdin_json is not None else None
        if command.action_type == "certbot-issue":
            stdin = json.dumps(
                {
                    "action_id": action_id,
                    "project": command.project,
                    "config_path": str(command.config_path) if command.config_path else None,
                    "expected_file": str(command.expected_file) if command.expected_file else None,
                    "domains": (command.parameters or {}).get("domains"),
                }
            )
        try:
            self.db.mark_action_running(action_id)
            result = self.runner.run(command.argv, stdin=stdin, timeout_seconds=command.timeout_seconds)
        except Exception as error:
            self.db.finish_action(
                action_id,
                status="failed",
                error_excerpt=str(error),
            )
            self.db.record_audit(
                action=command.action_type,
                project=command.project,
                subject_type="system-action",
                subject=command.target,
                result="failed",
                message_excerpt=str(error),
            )
            raise

        status = "succeeded" if result.exit_code == 0 else "failed"
        self.db.finish_action(
            action_id,
            status=status,
            exit_code=result.exit_code,
            stdout_excerpt=result.stdout,
            stderr_excerpt=result.stderr,
            result_metadata={
                "subcommand": command.subcommand,
                "target": command.target,
            },
        )
        self.db.record_audit(
            action=command.action_type,
            project=command.project,
            subject_type="system-action",
            subject=command.target,
            result=status,
            exit_code=result.exit_code,
            message_excerpt=result.stderr or result.stdout,
        )
        return SystemActionResult(action_id=action_id, command=command, result=result)


def nginx_test_command() -> CommandSpec:
    return CommandSpec(
        action_type="nginx-test",
        subcommand="nginx-test",
        target="nginx",
        argv=(NGINX_BIN, "-t"),
    )


def nginx_reload_command() -> CommandSpec:
    return CommandSpec(
        action_type="nginx-reload",
        subcommand="nginx-reload",
        target="nginx",
        argv=(SYSTEMCTL_BIN, "reload", "nginx"),
    )


def certbot_issue_command(
    domains: tuple[str, ...] | list[str], *, project: str | None = None, config_path: Path | None = None
) -> CommandSpec:
    normalized_domains = tuple(domains)
    _validate_certbot_domains(normalized_domains)
    domain_args = tuple(arg for domain in normalized_domains for arg in ("-d", domain))
    return CommandSpec(
        action_type="certbot-issue",
        subcommand="certbot-issue",
        target=", ".join(normalized_domains),
        project=project,
        config_path=config_path,
        expected_file=config_path,
        argv=(
            CERTBOT_BIN,
            "--nginx",
            "--non-interactive",
            "--agree-tos",
            "--email",
            CERTBOT_EMAIL,
            "--no-eff-email",
            "--keep-until-expiring",
            *domain_args,
            "--redirect",
        ),
        parameters={"domains": list(normalized_domains), "redirect": True, "email": CERTBOT_EMAIL},
        timeout_seconds=LONG_RUNNING_TIMEOUT_SECONDS,
    )


def certbot_renew_dry_run_command() -> CommandSpec:
    return CommandSpec(
        action_type="certbot-renew-dry-run",
        subcommand="certbot-renew-dry-run",
        target="certbot-renew",
        argv=(CERTBOT_BIN, "renew", "--dry-run", "--non-interactive"),
        timeout_seconds=LONG_RUNNING_TIMEOUT_SECONDS,
    )


def pm2_list_command() -> CommandSpec:
    return CommandSpec(
        action_type="pm2-list",
        subcommand="pm2-list",
        target="pm2",
        argv=(PM2_BIN, "jlist"),
    )


def pm2_logs_command(name: str, *, lines: int = 100) -> CommandSpec:
    _validate_pm2_name(name)
    if lines < 1 or lines > 500:
        raise CommandValidationError("PM2 log lines must be between 1 and 500")
    return CommandSpec(
        action_type="pm2-logs",
        subcommand="pm2-logs",
        target=name,
        argv=(PM2_BIN, "logs", name, "--lines", str(lines), "--nostream"),
        parameters={"name": name, "lines": lines},
    )


def pm2_lifecycle_command(operation: str, name: str) -> CommandSpec:
    if operation not in PM2_OPERATIONS_WITH_NAME:
        raise CommandValidationError(f"Unsupported PM2 operation: {operation}")
    _validate_pm2_name(name)
    return CommandSpec(
        action_type=f"pm2-{operation}",
        subcommand=f"pm2-{operation}",
        target=name,
        argv=(PM2_BIN, operation, name),
        parameters={"name": name},
    )


def pm2_start_command(
    *,
    name: str,
    project_root: Path,
    cwd: Path,
    start_script: Path,
) -> CommandSpec:
    _validate_pm2_name(name)
    resolved_cwd = validate_existing_path_under_root(cwd, project_root, expected_file=False)
    resolved_script = validate_existing_path_under_root(start_script, resolved_cwd, expected_file=True)
    return CommandSpec(
        action_type="pm2-start",
        subcommand="pm2-start",
        target=name,
        argv=(PM2_BIN, "start", str(resolved_script), "--name", name, "--cwd", str(resolved_cwd)),
        project=resolved_cwd.name,
        parameters={"name": name, "cwd": str(resolved_cwd), "start_script": str(resolved_script)},
    )


def django_manage_command(
    operation: str,
    *,
    project_root: Path,
    project_path: Path,
    python_path: Path | None = None,
) -> CommandSpec:
    if operation not in DJANGO_MANAGE_OPERATIONS:
        raise CommandValidationError(f"Unsupported Django deploy operation: {operation}")

    resolved_project = validate_existing_path_under_root(project_path, project_root, expected_file=False)
    manage_py = validate_existing_path_under_root(resolved_project / "manage.py", resolved_project, expected_file=True)
    python = _validated_project_python(resolved_project, python_path)
    manage_args = DJANGO_MANAGE_OPERATIONS[operation]
    return CommandSpec(
        action_type=f"django-{operation}",
        subcommand=f"django-{operation}",
        target=resolved_project.name,
        argv=(str(python), str(manage_py), *manage_args),
        project=resolved_project.name,
        parameters={
            "operation": operation,
            "project_path": str(resolved_project),
            "manage_py": str(manage_py),
            "python": str(python),
        },
        timeout_seconds=LONG_RUNNING_TIMEOUT_SECONDS,
    )


def helper_command(
    *,
    subcommand: str,
    action_id: str,
    project: str,
    config_path: Path,
    expected_file: Path,
    payload: dict[str, object],
) -> CommandSpec:
    if subcommand not in {
        "activate-config",
        "commit-config",
        "deactivate-config",
        "reset-config-to-head",
        "write-config",
    }:
        raise CommandValidationError(f"Unsupported helper subcommand: {subcommand}")
    request_payload = {
        "action_id": action_id,
        "project": project,
        "config_path": str(config_path),
        "expected_file": str(expected_file),
        **payload,
    }
    return CommandSpec(
        action_type=f"helper-{subcommand}",
        subcommand=subcommand,
        target=str(expected_file),
        argv=(CONFIG_HELPER_BIN, subcommand),
        project=project,
        config_path=config_path,
        expected_file=expected_file,
        parameters={"action_id": action_id, "project": project, "expected_file": str(expected_file)},
        stdin_json=request_payload,
        timeout_seconds=LONG_RUNNING_TIMEOUT_SECONDS,
    )


def github_clone_helper_command(*, action_id: str, plan: GitHubClonePlan) -> CommandSpec:
    return CommandSpec(
        action_type="helper-clone-project",
        subcommand="clone-project",
        target=plan.target_project,
        argv=(CONFIG_HELPER_BIN, "clone-project"),
        project=plan.target_project,
        parameters={
            "action_id": action_id,
            "clone_url": plan.clone_url,
            "target_project": plan.target_project,
            "target_path": str(plan.target_path),
        },
        stdin_json={
            "action_id": action_id,
            "clone_url": plan.clone_url,
            "target_project": plan.target_project,
            "target_path": str(plan.target_path),
        },
        timeout_seconds=LONG_RUNNING_TIMEOUT_SECONDS,
    )


def validate_existing_path_under_root(path: Path, root: Path, *, expected_file: bool) -> Path:
    if path.is_symlink():
        raise PathValidationError(f"Symlink-Pfad ist nicht erlaubt: {path}")

    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise PathValidationError(str(error)) from error

    if not resolved_path.is_relative_to(resolved_root):
        raise PathValidationError(f"Pfad liegt ausserhalb der erlaubten Wurzel: {path}")
    if expected_file and not resolved_path.is_file():
        raise PathValidationError(f"Datei erwartet: {path}")
    if not expected_file and not resolved_path.is_dir():
        raise PathValidationError(f"Verzeichnis erwartet: {path}")

    return resolved_path


def _validate_certbot_domains(domains: tuple[str, ...]) -> None:
    if not domains:
        raise CommandValidationError("At least one domain is required")
    for domain in domains:
        if domain in {"_", "localhost"}:
            raise CommandValidationError(f"Domain is not certbot-capable: {domain}")
        if "*" in domain:
            raise CommandValidationError(f"Wildcard domains are not supported in the MVP: {domain}")
        if "$" in domain:
            raise CommandValidationError(f"Variable server_name values are not supported: {domain}")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise CommandValidationError(f"IP addresses are not certbot domains: {domain}")
        if "." not in domain:
            raise CommandValidationError(f"Single-label domains are not public certbot targets: {domain}")


def _validate_pm2_name(name: str) -> None:
    if not name or any(char.isspace() for char in name) or "/" in name:
        raise CommandValidationError(f"Invalid PM2 process name: {name}")


def _validated_project_python(project_path: Path, python_path: Path | None) -> Path:
    candidates = (
        (python_path,)
        if python_path is not None
        else (
            project_path / ".venv/bin/python",
            project_path / ".venv/bin/python3",
            project_path / "venv/bin/python",
            project_path / "venv/bin/python3",
        )
    )
    for candidate in candidates:
        normalized = _normalize_project_child_path(candidate, project_path)
        if normalized.exists() and normalized.is_file():
            return normalized
    raise CommandValidationError("Django Python environment not found")


def _normalize_project_child_path(path: Path, project_path: Path) -> Path:
    resolved_project = project_path.resolve(strict=True)
    absolute_path = Path(os.path.abspath(path))
    if not absolute_path.is_relative_to(resolved_project):
        raise PathValidationError(f"Pfad liegt ausserhalb der erlaubten Projektwurzel: {path}")
    return absolute_path
