from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nginx_conf.configs import ProjectInventory
from nginx_conf.helper import handle_helper_request
from nginx_conf.selection import ProjectChoiceState
from nginx_conf.store import AppDatabase

HelperInvoker = Callable[[str, str], tuple[int, dict[str, object]]]


@dataclass(frozen=True, slots=True)
class ProjectGitStatus:
    project_name: str
    repo_root: Path | None
    selected_config: Path | None
    git_available: bool
    repo_dirty: bool
    selected_config_dirty: bool
    commit_required: bool
    commit_allowed: bool
    suggested_commit_message: str | None
    changed_files: tuple[str, ...] = ()
    foreign_changes: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class GitWorkflowResult:
    action_id: str
    ok: bool
    error: str | None
    git_commit_hash: str | None = None
    changed_file: str | None = None
    diff_stat: dict[str, int] | None = None


class GitWorkflowError(RuntimeError):
    pass


def inspect_project_git(project: ProjectInventory, choice: ProjectChoiceState) -> ProjectGitStatus:
    selected_config = choice.selected_nginx_config
    if selected_config is None:
        return ProjectGitStatus(
            project_name=project.project_name,
            repo_root=None,
            selected_config=None,
            git_available=False,
            repo_dirty=False,
            selected_config_dirty=False,
            commit_required=False,
            commit_allowed=False,
            suggested_commit_message=None,
            error="Keine ausgewaehlte Nginx-Config.",
        )

    repo_root_result = _run_git(project.project_path, ("rev-parse", "--show-toplevel"))
    if repo_root_result.returncode != 0:
        return ProjectGitStatus(
            project_name=project.project_name,
            repo_root=None,
            selected_config=selected_config,
            git_available=False,
            repo_dirty=False,
            selected_config_dirty=False,
            commit_required=False,
            commit_allowed=False,
            suggested_commit_message=None,
            error="Projekt ist kein Git-Repository.",
        )

    repo_root = Path(repo_root_result.stdout.strip()).resolve()
    selected_relative = selected_config.resolve().relative_to(repo_root)
    changed_files = _git_changed_files(repo_root)
    selected = str(selected_relative)
    selected_dirty = selected in changed_files
    foreign_changes = tuple(path for path in changed_files if path != selected)
    return ProjectGitStatus(
        project_name=project.project_name,
        repo_root=repo_root,
        selected_config=selected_config,
        git_available=True,
        repo_dirty=bool(changed_files),
        selected_config_dirty=selected_dirty,
        commit_required=selected_dirty,
        commit_allowed=selected_dirty,
        suggested_commit_message="Config aktualisiert" if selected_dirty else None,
        changed_files=tuple(changed_files),
        foreign_changes=foreign_changes,
    )


def live_diff_for_selected_config(project: ProjectInventory, choice: ProjectChoiceState) -> str:
    status = inspect_project_git(project, choice)
    if status.error or status.repo_root is None or status.selected_config is None:
        raise GitWorkflowError(status.error or "Git status unavailable")
    relative = status.selected_config.resolve().relative_to(status.repo_root)
    result = _run_git(status.repo_root, ("diff", "--", str(relative)))
    if result.returncode != 0:
        raise GitWorkflowError(result.stderr or result.stdout or "git diff failed")
    return result.stdout


class GitWorkflowService:
    def __init__(
        self,
        db: AppDatabase,
        *,
        project_root: Path,
        helper_invoker: HelperInvoker | None = None,
    ) -> None:
        self.db = db
        self.project_root = project_root
        self.helper_invoker = helper_invoker or self._default_helper_invoker

    def commit_selected_config(
        self,
        project: ProjectInventory,
        choice: ProjectChoiceState,
        *,
        message: str,
        action_id: str | None = None,
    ) -> GitWorkflowResult:
        if not message:
            raise GitWorkflowError("Commit message is required")
        return self._run_helper_action(
            project,
            choice,
            subcommand="commit-config",
            payload={"message": message},
            action_id=action_id,
        )

    def reset_selected_config(
        self,
        project: ProjectInventory,
        choice: ProjectChoiceState,
        *,
        action_id: str | None = None,
    ) -> GitWorkflowResult:
        return self._run_helper_action(
            project, choice, subcommand="reset-config-to-head", payload={}, action_id=action_id
        )

    def _run_helper_action(
        self,
        project: ProjectInventory,
        choice: ProjectChoiceState,
        *,
        subcommand: str,
        payload: dict[str, object],
        action_id: str | None,
    ) -> GitWorkflowResult:
        selected_config = choice.selected_nginx_config
        if selected_config is None:
            raise GitWorkflowError("No selected Nginx config")

        action_id = action_id or str(uuid.uuid4())
        self.db.create_action(
            action_id=action_id,
            action_type=f"helper-{subcommand}",
            target=str(selected_config),
            project=project.project_name,
            config_path=str(selected_config),
            subcommand=subcommand,
            expected_file=str(selected_config),
            parameters={"message": payload.get("message")} if "message" in payload else {},
        )
        self.db.mark_action_running(action_id)
        request_payload = {
            "action_id": action_id,
            "project": project.project_name,
            "config_path": str(selected_config),
            "expected_file": str(selected_config),
            **payload,
        }
        exit_code, response = self.helper_invoker(subcommand, json.dumps(request_payload))
        ok = bool(response.get("ok")) and exit_code == 0
        status = "succeeded" if ok else "failed"
        error = None if ok else str(response.get("error") or "Helper rejected action")
        git_commit_hash = _optional_str(response.get("git_commit_hash"))
        changed_file = _optional_str(response.get("changed_file"))
        diff_stat = response.get("diff_stat") if isinstance(response.get("diff_stat"), dict) else None

        self.db.finish_action(
            action_id,
            status=status,
            exit_code=exit_code,
            error_excerpt=error,
            result_metadata={
                "git_commit_hash": git_commit_hash,
                "changed_file": changed_file,
                "diff_stat": diff_stat,
            },
        )
        self.db.record_audit(
            action=subcommand,
            project=project.project_name,
            subject_type="config",
            subject=str(selected_config),
            result=status,
            git_commit_hash=git_commit_hash,
            exit_code=exit_code,
            message_excerpt=error,
        )
        return GitWorkflowResult(
            action_id=action_id,
            ok=ok,
            error=error,
            git_commit_hash=git_commit_hash,
            changed_file=changed_file,
            diff_stat=diff_stat,
        )

    def _default_helper_invoker(self, subcommand: str, payload: str) -> tuple[int, dict[str, object]]:
        return handle_helper_request(
            subcommand,
            payload,
            db_path=self.db.path,
            project_root=self.project_root,
        )


def project_git_status_to_dict(status: ProjectGitStatus) -> dict[str, object]:
    return {
        "project_name": status.project_name,
        "repo_root": _path_to_str(status.repo_root),
        "selected_config": _path_to_str(status.selected_config),
        "git_available": status.git_available,
        "repo_dirty": status.repo_dirty,
        "selected_config_dirty": status.selected_config_dirty,
        "commit_required": status.commit_required,
        "commit_allowed": status.commit_allowed,
        "suggested_commit_message": status.suggested_commit_message,
        "changed_files": list(status.changed_files),
        "foreign_changes": list(status.foreign_changes),
        "error": status.error,
    }


def git_workflow_result_to_dict(result: GitWorkflowResult) -> dict[str, object]:
    return {
        "action_id": result.action_id,
        "ok": result.ok,
        "error": result.error,
        "git_commit_hash": result.git_commit_hash,
        "changed_file": result.changed_file,
        "diff_stat": result.diff_stat,
    }


def _git_changed_files(repo_root: Path) -> list[str]:
    result = _run_git(repo_root, ("status", "--porcelain"))
    if result.returncode != 0:
        raise GitWorkflowError(result.stderr or result.stdout or "git status failed")
    changed = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        changed.append(line[3:])
    return changed


def _run_git(cwd: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def _path_to_str(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
