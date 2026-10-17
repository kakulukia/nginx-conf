from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nginx_conf.configs import ProjectInventory
from nginx_conf.django_allowed_hosts import is_django_project

DJANGO_DEPLOY_OPERATIONS = ("migrate", "collectstatic", "compress")
DEPENDENCY_FILES = ("pyproject.toml", "requirements.txt", "requirements.in", "Pipfile")
PYTHON_CANDIDATES = (".venv/bin/python", ".venv/bin/python3", "venv/bin/python", "venv/bin/python3")


@dataclass(frozen=True, slots=True)
class DjangoDeployAction:
    operation: str
    available: bool
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DjangoDeployStatus:
    project_name: str
    is_django: bool
    manage_py: Path | None
    python_path: Path | None
    compress_available: bool
    actions: tuple[DjangoDeployAction, ...]


def inspect_django_deploy(projects: tuple[ProjectInventory, ...]) -> tuple[DjangoDeployStatus, ...]:
    return tuple(_inspect_project(project) for project in projects)


def django_deploy_statuses_to_dict(statuses: tuple[DjangoDeployStatus, ...]) -> list[dict[str, object]]:
    return [
        {
            "project_name": status.project_name,
            "is_django": status.is_django,
            "manage_py": _path_to_str(status.manage_py),
            "python_path": _path_to_str(status.python_path),
            "compress_available": status.compress_available,
            "actions": [
                {
                    "operation": action.operation,
                    "available": action.available,
                    "blockers": list(action.blockers),
                }
                for action in status.actions
            ],
        }
        for status in statuses
    ]


def action_for_operation(status: DjangoDeployStatus, operation: str) -> DjangoDeployAction | None:
    for action in status.actions:
        if action.operation == operation:
            return action
    return None


def _inspect_project(project: ProjectInventory) -> DjangoDeployStatus:
    is_django = is_django_project(project.project_path)
    manage_py = project.project_path / "manage.py"
    if not manage_py.is_file() or manage_py.is_symlink():
        manage_py = None
    python_path = _find_project_python(project.project_path)
    compress_available = _dependency_mentions_compressor(project.project_path)
    actions = tuple(
        _action(
            operation,
            is_django=is_django,
            manage_py=manage_py,
            python_path=python_path,
            compress_available=compress_available,
        )
        for operation in DJANGO_DEPLOY_OPERATIONS
    )
    return DjangoDeployStatus(
        project_name=project.project_name,
        is_django=is_django,
        manage_py=manage_py,
        python_path=python_path,
        compress_available=compress_available,
        actions=actions,
    )


def _action(
    operation: str,
    *,
    is_django: bool,
    manage_py: Path | None,
    python_path: Path | None,
    compress_available: bool,
) -> DjangoDeployAction:
    blockers: list[str] = []
    if not is_django:
        blockers.append("not_django_project")
    if manage_py is None:
        blockers.append("missing_manage_py")
    if python_path is None:
        blockers.append("missing_python_environment")
    if operation == "compress" and not compress_available:
        blockers.append("compress_not_detected")
    return DjangoDeployAction(operation=operation, available=not blockers, blockers=tuple(blockers))


def _find_project_python(project_path: Path) -> Path | None:
    for candidate_name in PYTHON_CANDIDATES:
        candidate = project_path / candidate_name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _dependency_mentions_compressor(project_path: Path) -> bool:
    for name in DEPENDENCY_FILES:
        path = project_path / name
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8").lower()
        except OSError:
            continue
        if "django-compressor" in content:
            return True
    return False


def _path_to_str(path: Path | None) -> str | None:
    return None if path is None else str(path)
