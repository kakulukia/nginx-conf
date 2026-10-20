from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nginx_conf.configs import certificate_server_group, parse_server_blocks
from nginx_conf.github_clone import GitHubClonePlan, GitHubCloneValidationError, plan_github_clone
from nginx_conf.store import AppDatabase, default_database_path
from nginx_conf.system_actions import certbot_issue_command, nginx_test_command

PROJECT_ROOT = Path("/opt/www")
ENABLED_ROOT = Path("/etc/nginx/sites-enabled")
ALLOWED_SUBCOMMANDS = {
    "activate-config",
    "clone-project",
    "commit-config",
    "deactivate-config",
    "reset-config-to-head",
    "write-config",
    "certbot-issue",
}
FORBIDDEN_REQUEST_KEYS = {"tempfile_path", "tmp_path", "before_hash", "after_hash", "full_diff", "git_diff"}


@dataclass(frozen=True, slots=True)
class HelperContext:
    db: AppDatabase
    project_root: Path = PROJECT_ROOT
    enabled_root: Path = ENABLED_ROOT


@dataclass(frozen=True, slots=True)
class ValidatedRequest:
    action_id: str
    subcommand: str
    project: str
    config_path: Path
    expected_file: Path
    repo_root: Path
    repo_relative_file: Path
    project_path: Path
    enabled_root: Path
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ValidatedCloneRequest:
    action_id: str
    subcommand: str
    plan: GitHubClonePlan


def handle_helper_request(
    subcommand: str,
    stdin_text: str,
    *,
    db_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    enabled_root: Path = ENABLED_ROOT,
) -> tuple[int, dict[str, Any]]:
    action_id: str | None = None
    try:
        payload = _parse_payload(stdin_text)
        action_id = _optional_str(payload.get("action_id"))
        context = HelperContext(
            db=AppDatabase(db_path or default_database_path(), read_only=True),
            project_root=project_root,
            enabled_root=enabled_root,
        )
        if subcommand == "clone-project":
            clone_request = _validate_clone_request(payload, context)
            return 0, _clone_project(clone_request)
        request = _validate_request(subcommand, payload, context)

        if subcommand == "write-config":
            return 0, _write_config(request)
        if subcommand == "reset-config-to-head":
            return 0, _reset_config_to_head(request)
        if subcommand == "commit-config":
            return 0, _commit_config(request)
        if subcommand == "activate-config":
            if context.db.get_action(request.action_id).parameters.get("switch_config") is True:
                return _switch_config(request, context)
            return 0, _activate_config(request)
        if subcommand == "deactivate-config":
            return 0, _deactivate_config(request)
        if subcommand == "certbot-issue":
            return _issue_certificate(request, context)
        raise HelperRequestError(f"Unsupported helper subcommand: {subcommand}")
    except HelperRequestError as error:
        return 1, _error_response(action_id, subcommand, str(error))
    except Exception as error:
        return 1, _error_response(action_id, subcommand, f"Unexpected helper error: {error}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        response = _error_response(None, None, "Exactly one helper subcommand is required")
        print(json.dumps(response, sort_keys=True))
        return 1

    exit_code, response = handle_helper_request(argv[0], sys.stdin.read())
    print(json.dumps(response, sort_keys=True))
    return exit_code


class HelperRequestError(ValueError):
    pass


def _parse_payload(stdin_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError as error:
        raise HelperRequestError(f"Invalid JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise HelperRequestError("JSON object expected")
    for key in FORBIDDEN_REQUEST_KEYS:
        if key in payload:
            raise HelperRequestError(f"Unsupported request field: {key}")
    if "files" in payload:
        raise HelperRequestError("Multi-file helper requests are not supported")
    return payload


def _validate_request(subcommand: str, payload: dict[str, Any], context: HelperContext) -> ValidatedRequest:
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise HelperRequestError(f"Unsupported helper subcommand: {subcommand}")
    if subcommand == "clone-project":
        raise HelperRequestError("clone-project requires clone validation")

    action_id = _required_str(payload, "action_id")
    project = _required_str(payload, "project")
    config_path = Path(_required_str(payload, "config_path"))
    expected_file = Path(_required_str(payload, "expected_file"))

    action = context.db.get_action(action_id)
    if action is None:
        raise HelperRequestError("Unknown action_id")
    if action.status not in {"started", "running"}:
        raise HelperRequestError("Action is not active")
    if action.subcommand != subcommand:
        raise HelperRequestError("Subcommand does not match action_id")
    if action.project != project:
        raise HelperRequestError("Project does not match action_id")
    if action.config_path != str(config_path) or action.expected_file != str(expected_file):
        raise HelperRequestError("Config path does not match action_id")

    selection = context.db.get_project_selection(project)
    saved_config = selection.nginx_config_path if selection else None
    switching = subcommand == "activate-config" and action.parameters.get("switch_config") is True
    if switching:
        if "previous_selection" not in action.parameters or saved_config != action.parameters["previous_selection"]:
            raise HelperRequestError("Saved selection changed since the config switch was requested")
    elif saved_config != str(expected_file):
        raise HelperRequestError("Expected file is not the saved project Nginx selection")

    resolved_project = _validate_project_path(project, context.project_root)
    resolved_expected = _validate_existing_config(expected_file, resolved_project)
    resolved_config = _validate_existing_config(config_path, resolved_project)
    if resolved_expected != resolved_config:
        raise HelperRequestError("Config path and expected file differ")

    repo_root = _git_repo_root(resolved_project)
    if not resolved_expected.is_relative_to(repo_root):
        raise HelperRequestError("Expected file is outside the Git repository")

    return ValidatedRequest(
        action_id=action_id,
        subcommand=subcommand,
        project=project,
        config_path=resolved_config,
        expected_file=resolved_expected,
        repo_root=repo_root,
        repo_relative_file=resolved_expected.relative_to(repo_root),
        project_path=resolved_project,
        enabled_root=context.enabled_root,
        payload=payload,
    )


def _validate_clone_request(payload: dict[str, Any], context: HelperContext) -> ValidatedCloneRequest:
    action_id = _required_str(payload, "action_id")
    clone_url = _required_str(payload, "clone_url")
    target_project = _required_str(payload, "target_project")
    target_path = Path(_required_str(payload, "target_path"))

    action = context.db.get_action(action_id)
    if action is None:
        raise HelperRequestError("Unknown action_id")
    if action.status not in {"started", "running"}:
        raise HelperRequestError("Action is not active")
    if action.subcommand != "clone-project":
        raise HelperRequestError("Subcommand does not match action_id")
    if action.project != target_project:
        raise HelperRequestError("Project does not match action_id")
    if action.config_path is not None or action.expected_file is not None:
        raise HelperRequestError("Clone action must not carry config paths")

    try:
        plan = plan_github_clone(clone_url, project_root=context.project_root)
    except GitHubCloneValidationError as error:
        raise HelperRequestError(str(error)) from error
    if plan.target_project != target_project or plan.target_path != target_path:
        raise HelperRequestError("Clone target does not match action_id")
    return ValidatedCloneRequest(action_id=action_id, subcommand="clone-project", plan=plan)


def _write_config(request: ValidatedRequest) -> dict[str, Any]:
    new_content = _required_str(request.payload, "new_content")
    request.expected_file.write_text(new_content, encoding="utf-8")
    return _success_response(
        request,
        changed_file=str(request.expected_file),
        diff_stat=_git_diff_stat(request.repo_root, request.repo_relative_file),
    )


def _reset_config_to_head(request: ValidatedRequest) -> dict[str, Any]:
    result = _run_git(request.repo_root, ("checkout", "--", str(request.repo_relative_file)))
    if result.returncode != 0:
        raise HelperRequestError(result.stderr or result.stdout or "git checkout failed")
    return _success_response(
        request,
        changed_file=str(request.expected_file),
        diff_stat=_git_diff_stat(request.repo_root, request.repo_relative_file),
    )


def _commit_config(request: ValidatedRequest) -> dict[str, Any]:
    message = _required_str(request.payload, "message")
    if not _git_file_has_changes(request.repo_root, request.repo_relative_file):
        raise HelperRequestError("Expected file has no changes to commit")

    add = _run_git(request.repo_root, ("add", "--", str(request.repo_relative_file)))
    if add.returncode != 0:
        raise HelperRequestError(add.stderr or add.stdout or "git add failed")

    commit = _run_git(request.repo_root, ("commit", "-m", message, "--", str(request.repo_relative_file)))
    if commit.returncode != 0:
        raise HelperRequestError(commit.stderr or commit.stdout or "git commit failed")

    commit_hash = _run_git(request.repo_root, ("rev-parse", "HEAD"))
    if commit_hash.returncode != 0:
        raise HelperRequestError(commit_hash.stderr or commit_hash.stdout or "git rev-parse failed")

    return _success_response(
        request,
        changed_file=str(request.expected_file),
        git_commit_hash=commit_hash.stdout.strip(),
    )


def _activate_config(request: ValidatedRequest) -> dict[str, Any]:
    link_path = _project_enabled_link(request)
    if link_path.exists() or link_path.is_symlink():
        _validate_managed_enabled_symlink(link_path, request.expected_file)
    else:
        link_path.symlink_to(request.expected_file)
    return _success_response(request, changed_file=str(link_path), diff_stat=None)


def _deactivate_config(request: ValidatedRequest) -> dict[str, Any]:
    link_path = _project_enabled_link(request)
    if link_path.exists() or link_path.is_symlink():
        _validate_managed_enabled_symlink(link_path, request.expected_file)
        link_path.unlink()
    return _success_response(request, changed_file=str(link_path), diff_stat=None)


def _project_enabled_link(request: ValidatedRequest) -> Path:
    requested = _validate_enabled_link_path(_required_str(request.payload, "link_path"), request)
    enabled = _validate_enabled_root(request.enabled_root)
    names = {request.project, f"{request.project}.conf"}
    links = []
    for entry in enabled.iterdir():
        belongs_to_project = entry.is_symlink() and entry.resolve().is_relative_to(request.project_path)
        if entry.name in names or belongs_to_project:
            if not entry.is_symlink():
                raise HelperRequestError("Enabled path exists but is not a symlink")
            if entry.name not in names:
                raise HelperRequestError("Project config is active under an unmanaged symlink name")
            links.append(entry)
    if len(links) > 1:
        raise HelperRequestError("Multiple enabled symlinks for this project must be resolved before switching")
    return links[0] if links else requested


def _replace_enabled_link(link: Path, target: str | Path) -> None:
    temporary = link.parent / f".nginx-conf-{uuid.uuid4().hex}"
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def _switch_config(request: ValidatedRequest, context: HelperContext) -> tuple[int, dict[str, Any]]:
    action = context.db.get_action(request.action_id)
    if "previous_active_config" not in action.parameters:
        raise HelperRequestError("Missing previous active config")
    previous = action.parameters["previous_active_config"]
    if previous is not None:
        if not isinstance(previous, str):
            raise HelperRequestError("Invalid previous active config")
        previous = _validate_existing_config(Path(previous), request.project_path)
    link = _project_enabled_link(request)
    rollback = request.payload.get("rollback") is True
    expected = request.expected_file if rollback else previous
    if expected is None:
        if link.exists() or link.is_symlink():
            raise HelperRequestError("Enabled state changed since the config switch was requested")
    else:
        _validate_managed_enabled_symlink(link, expected)

    if rollback:
        if previous is None:
            link.unlink()
        else:
            _replace_enabled_link(link, previous)
        return 0, _success_response(request, changed_file=str(link))

    original_target = os.readlink(link) if link.is_symlink() else None
    _replace_enabled_link(link, request.expected_file)
    try:
        command = nginx_test_command()
        test = subprocess.run(
            command.argv, capture_output=True, text=True, shell=False, check=False, timeout=command.timeout_seconds
        )
        if test.returncode != 0:
            raise HelperRequestError("nginx -t fehlgeschlagen: " + (test.stderr or test.stdout)[-4000:])
    except Exception as error:
        if original_target is None:
            link.unlink()
        else:
            _replace_enabled_link(link, original_target)
        raise HelperRequestError(f"Config nicht übernommen. Bisheriger Zustand wiederhergestellt. {error}") from error
    return 0, {**_success_response(request, changed_file=str(link)), "nginx_test": test.stderr or test.stdout}


def _clone_project(request: ValidatedCloneRequest) -> dict[str, Any]:
    result = _run_git_clone(request.plan.clone_url, request.plan.target_path)
    if result.returncode != 0:
        raise HelperRequestError(result.stderr or result.stdout or "git clone failed")
    return {
        "action_id": request.action_id,
        "subcommand": request.subcommand,
        "ok": True,
        "changed_file": str(request.plan.target_path),
        "exit_code": 0,
        "diff_stat": None,
        "git_commit_hash": None,
        "error": None,
        "target_project": request.plan.target_project,
        "target_path": str(request.plan.target_path),
    }


def _issue_certificate(request: ValidatedRequest, context: HelperContext) -> tuple[int, dict[str, Any]]:
    domains = request.payload.get("domains")
    if not isinstance(domains, list) or not domains or not all(isinstance(domain, str) for domain in domains):
        raise HelperRequestError("Certificate domains must be a non-empty string list")
    action = context.db.get_action(request.action_id)
    if action.action_type != "certbot-issue" or action.parameters.get("domains") != domains:
        raise HelperRequestError("Certificate domains do not match action_id")
    group = certificate_server_group(parse_server_blocks(request.expected_file), tuple(domains))
    if group is None:
        raise HelperRequestError("Certificate domains must match one unambiguous HTTP/HTTPS configuration")
    enabled = _validate_enabled_root(context.enabled_root)
    if not any(link.is_symlink() and link.resolve() == request.expected_file for link in enabled.iterdir()):
        raise HelperRequestError("Certificate config must be active")
    command = certbot_issue_command(domains)
    # Leave time to clean up Certbot before the caller times out.
    result = subprocess.run(
        command.argv, capture_output=True, text=True, check=False, shell=False, timeout=command.timeout_seconds - 30
    )
    return result.returncode, {
        "action_id": request.action_id,
        "subcommand": request.subcommand,
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def _validate_project_path(project: str, project_root: Path) -> Path:
    if project != Path(project).name:
        raise HelperRequestError("Invalid project id")
    try:
        resolved_root = project_root.resolve(strict=True)
        resolved_project = (project_root / project).resolve(strict=True)
    except OSError as error:
        raise HelperRequestError(str(error)) from error
    if not resolved_project.is_relative_to(resolved_root):
        raise HelperRequestError("Project path escapes project root")
    if not resolved_project.is_dir():
        raise HelperRequestError("Project path is not a directory")
    return resolved_project


def _validate_existing_config(path: Path, project_path: Path) -> Path:
    if path.is_symlink():
        raise HelperRequestError("Config symlinks are not allowed")
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise HelperRequestError(str(error)) from error
    if not resolved_path.is_relative_to(project_path):
        raise HelperRequestError("Config path escapes project root")
    if not resolved_path.is_file() or resolved_path.suffix != ".nginx":
        raise HelperRequestError("Expected file is not an existing *.nginx config")
    return resolved_path


def _validate_enabled_root(enabled_root: Path) -> Path:
    try:
        resolved_root = enabled_root.resolve(strict=True)
    except OSError as error:
        raise HelperRequestError(str(error)) from error
    if not resolved_root.is_dir():
        raise HelperRequestError("Enabled root is not a directory")
    return resolved_root


def _validate_enabled_link_path(value: str, request: ValidatedRequest) -> Path:
    link_path = Path(value)
    enabled_root = _validate_enabled_root(request.enabled_root)
    try:
        resolved_parent = link_path.parent.resolve(strict=True)
    except OSError as error:
        raise HelperRequestError(str(error)) from error
    if resolved_parent != enabled_root:
        raise HelperRequestError("Enabled link path escapes enabled root")
    if link_path.name != f"{request.project}.conf":
        raise HelperRequestError("Enabled link name must match the project")
    return link_path


def _validate_managed_enabled_symlink(link_path: Path, expected_file: Path) -> None:
    if not link_path.is_symlink():
        raise HelperRequestError("Enabled path exists but is not a symlink")
    try:
        target = link_path.resolve(strict=True)
    except OSError as error:
        raise HelperRequestError(f"Enabled symlink is broken: {error}") from error
    if target != expected_file:
        raise HelperRequestError("Enabled symlink points to a different config")


def _git_repo_root(project_path: Path) -> Path:
    result = _run_git(project_path, ("rev-parse", "--show-toplevel"))
    if result.returncode != 0:
        raise HelperRequestError("Project is not a Git repository")
    return Path(result.stdout.strip()).resolve(strict=True)


def _git_file_has_changes(repo_root: Path, relative_file: Path) -> bool:
    result = _run_git(repo_root, ("status", "--porcelain", "--", str(relative_file)))
    if result.returncode != 0:
        raise HelperRequestError(result.stderr or result.stdout or "git status failed")
    return bool(result.stdout.strip())


def _git_diff_stat(repo_root: Path, relative_file: Path) -> dict[str, int]:
    result = _run_git(repo_root, ("diff", "--numstat", "--", str(relative_file)))
    if result.returncode != 0 or not result.stdout.strip():
        return {"files": 0, "insertions": 0, "deletions": 0}

    files = 0
    insertions = 0
    deletions = 0
    for line in result.stdout.splitlines():
        added, removed, _path = line.split("\t", maxsplit=2)
        files += 1
        insertions += 0 if added == "-" else int(added)
        deletions += 0 if removed == "-" else int(removed)
    return {"files": files, "insertions": insertions, "deletions": deletions}


def _run_git(cwd: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def _run_git_clone(clone_url: str, target_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "clone", clone_url, str(target_path)),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def _success_response(
    request: ValidatedRequest,
    *,
    changed_file: str,
    diff_stat: dict[str, int] | None = None,
    git_commit_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "action_id": request.action_id,
        "subcommand": request.subcommand,
        "ok": True,
        "changed_file": changed_file,
        "exit_code": 0,
        "diff_stat": diff_stat,
        "git_commit_hash": git_commit_hash,
        "error": None,
    }


def _error_response(action_id: str | None, subcommand: str | None, error: str) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "subcommand": subcommand,
        "ok": False,
        "changed_file": None,
        "exit_code": 1,
        "diff_stat": None,
        "git_commit_hash": None,
        "error": error,
    }


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HelperRequestError(f"Missing or invalid field: {key}")
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


if __name__ == "__main__":
    raise SystemExit(main())
