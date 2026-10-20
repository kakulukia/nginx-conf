from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nginx_conf.configs import InventoryIssue, ProjectInventory
from nginx_conf.store import AppDatabase

IGNORED_STARTSCRIPT_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "cache",
    "dist",
    "node_modules",
    "venv",
}
PREFERRED_STARTSCRIPT_NAMES = ("start.sh", "run.sh", "serve.sh")
SELECTION_ISSUE_MESSAGES = {
    "missing_nginx_config": "Keine Nginx-Konfiguration gefunden.",
    "nginx_selection_required": "Nginx-Config-Auswahl erforderlich.",
    "saved_nginx_selection_missing": "Gespeicherte Nginx-Config-Auswahl ist kein aktueller Kandidat.",
    "missing_pm2_start_script": "Kein PM2-Startscript gefunden.",
    "pm2_start_script_selection_required": "PM2-Startscript-Auswahl erforderlich.",
    "saved_pm2_start_script_missing": "Gespeichertes PM2-Startscript ist kein aktueller Kandidat.",
}


@dataclass(frozen=True, slots=True)
class ProjectChoiceState:
    project_name: str
    nginx_candidates: tuple[Path, ...]
    pm2_start_scripts: tuple[Path, ...]
    selected_nginx_config: Path | None
    selected_pm2_start_script: Path | None
    nginx_selection_source: str | None
    pm2_selection_source: str | None
    issues: tuple[InventoryIssue, ...] = ()


def find_pm2_start_scripts(project_path: Path) -> tuple[Path, ...]:
    if not project_path.is_dir() or project_path.is_symlink():
        return ()

    scripts: list[Path] = []
    for current_root, dirnames, filenames in os.walk(project_path, followlinks=False):
        current_path = Path(current_root)
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if dirname not in IGNORED_STARTSCRIPT_DIRS and not (current_path / dirname).is_symlink()
        )

        for filename in sorted(filenames):
            script_path = current_path / filename
            if script_path.is_symlink() or script_path.suffix != ".sh":
                continue
            scripts.append(script_path)

    return tuple(sorted(scripts, key=lambda path: _start_script_sort_key(project_path, path)))


def build_project_choice_state(project: ProjectInventory, db: AppDatabase) -> ProjectChoiceState:
    selection = db.get_project_selection(project.project_name)
    nginx_candidates = tuple(config.config_path for config in project.configs)
    pm2_start_scripts = find_pm2_start_scripts(project.project_path)
    issues: list[InventoryIssue] = []

    selected_nginx_config, nginx_source = _resolve_selection(
        candidates=nginx_candidates,
        saved_path=selection.nginx_config_path if selection else None,
        missing_code="missing_nginx_config",
        required_code="nginx_selection_required",
        saved_missing_code="saved_nginx_selection_missing",
    )
    selected_pm2_start_script, pm2_source = _resolve_selection(
        candidates=pm2_start_scripts,
        saved_path=selection.pm2_start_script_path if selection else None,
        missing_code="missing_pm2_start_script",
        required_code="pm2_start_script_selection_required",
        saved_missing_code="saved_pm2_start_script_missing",
    )

    issues.extend(_selection_issues(project.project_path, nginx_candidates, selected_nginx_config, nginx_source))
    issues.extend(_selection_issues(project.project_path, pm2_start_scripts, selected_pm2_start_script, pm2_source))

    return ProjectChoiceState(
        project_name=project.project_name,
        nginx_candidates=nginx_candidates,
        pm2_start_scripts=pm2_start_scripts,
        selected_nginx_config=selected_nginx_config,
        selected_pm2_start_script=selected_pm2_start_script,
        nginx_selection_source=nginx_source,
        pm2_selection_source=pm2_source,
        issues=tuple(issues),
    )


def build_project_choice_states(
    projects: tuple[ProjectInventory, ...], db: AppDatabase
) -> tuple[ProjectChoiceState, ...]:
    return tuple(build_project_choice_state(project, db) for project in projects)


def save_project_choice(
    project: ProjectInventory,
    db: AppDatabase,
    *,
    nginx_config_path: str | None,
    pm2_start_script_path: str | None,
) -> ProjectChoiceState:
    nginx_candidates = {str(config.config_path) for config in project.configs}
    pm2_start_scripts = {str(script) for script in find_pm2_start_scripts(project.project_path)}

    if nginx_config_path is not None and nginx_config_path not in nginx_candidates:
        raise ValueError("Nginx-Config ist kein erlaubter Kandidat fuer dieses Projekt.")
    if any(config.enabled and str(config.config_path) != nginx_config_path for config in project.configs):
        raise ValueError("Eine andere Config ist aktiv. Bitte die Config-Auswahl auf der Projektseite verwenden.")
    if pm2_start_script_path is not None and pm2_start_script_path not in pm2_start_scripts:
        raise ValueError("PM2-Startscript ist kein erlaubter Kandidat fuer dieses Projekt.")

    db.save_project_selection(
        project.project_name,
        nginx_config_path=nginx_config_path,
        pm2_start_script_path=pm2_start_script_path,
    )
    return build_project_choice_state(project, db)


def project_choice_to_dict(choice: ProjectChoiceState) -> dict[str, object]:
    return {
        "project_name": choice.project_name,
        "nginx_candidates": [str(path) for path in choice.nginx_candidates],
        "pm2_start_scripts": [str(path) for path in choice.pm2_start_scripts],
        "selected_nginx_config": _path_to_str(choice.selected_nginx_config),
        "selected_pm2_start_script": _path_to_str(choice.selected_pm2_start_script),
        "nginx_selection_source": choice.nginx_selection_source,
        "pm2_selection_source": choice.pm2_selection_source,
        "issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "subject": issue.subject,
                "related": list(issue.related),
            }
            for issue in choice.issues
        ],
    }


def _resolve_selection(
    *,
    candidates: tuple[Path, ...],
    saved_path: str | None,
    missing_code: str,
    required_code: str,
    saved_missing_code: str,
) -> tuple[Path | None, str | None]:
    candidate_by_path = {str(candidate): candidate for candidate in candidates}
    if saved_path is not None:
        if saved_path in candidate_by_path:
            return candidate_by_path[saved_path], "saved"
        return None, saved_missing_code

    if len(candidates) == 1:
        return candidates[0], "auto"
    if len(candidates) == 0:
        return None, missing_code
    return None, required_code


def _selection_issues(
    project_path: Path,
    candidates: tuple[Path, ...],
    selected: Path | None,
    source: str | None,
) -> list[InventoryIssue]:
    if selected is not None or source in {"auto", "saved"}:
        return []
    if source is None:
        return []
    return [
        InventoryIssue(
            code=source,
            message=SELECTION_ISSUE_MESSAGES[source],
            subject=str(project_path),
            related=tuple(str(candidate) for candidate in candidates),
        )
    ]


def _start_script_sort_key(project_path: Path, script_path: Path) -> tuple[int, int, str]:
    relative_path = script_path.relative_to(project_path)
    preferred_names = (*PREFERRED_STARTSCRIPT_NAMES, f"{project_path.name}.sh")
    try:
        preferred_rank = preferred_names.index(script_path.name)
    except ValueError:
        preferred_rank = len(preferred_names)
    return (preferred_rank, len(relative_path.parts), str(relative_path))


def _path_to_str(path: Path | None) -> str | None:
    return None if path is None else str(path)
