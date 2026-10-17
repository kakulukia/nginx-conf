from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from nginx_conf.configs import ProjectInventory
from nginx_conf.store import AppDatabase
from nginx_conf.system_actions import CommandRunner, pm2_list_command


@dataclass(frozen=True, slots=True)
class PM2ProcessStatus:
    name: str
    pm2_id: str | None
    cwd: Path | None
    status: str | None
    restart_count: int | None
    port: int | None
    project_name: str | None
    managed: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class PM2StatusInventory:
    processes: tuple[PM2ProcessStatus, ...]
    error: str | None = None


def read_pm2_status(
    *,
    project_root: Path,
    projects: tuple[ProjectInventory, ...],
    db: AppDatabase,
    runner: CommandRunner,
) -> PM2StatusInventory:
    try:
        result = runner.run(pm2_list_command().argv, stdin=None, timeout_seconds=120)
    except Exception as error:
        return PM2StatusInventory(processes=(), error=f"pm2 jlist konnte nicht ausgefuehrt werden: {error}")

    if result.exit_code != 0:
        return PM2StatusInventory(processes=(), error=result.stderr or result.stdout or "pm2 jlist failed")

    try:
        raw_processes = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return PM2StatusInventory(processes=(), error=f"pm2 jlist returned invalid JSON: {error.msg}")
    if not isinstance(raw_processes, list):
        return PM2StatusInventory(processes=(), error="pm2 jlist did not return a list")

    managed_keys = {(process.project_name, process.pm2_name) for process in db.list_managed_pm2_processes()}
    project_paths = {project.project_path.resolve(): project.project_name for project in projects}
    processes = tuple(
        _process_status(raw_process, project_root=project_root, project_paths=project_paths, managed_keys=managed_keys)
        for raw_process in raw_processes
        if isinstance(raw_process, dict)
    )
    return PM2StatusInventory(processes=processes)


def pm2_inventory_to_dict(inventory: PM2StatusInventory) -> dict[str, object]:
    return {
        "error": inventory.error,
        "processes": [
            {
                "name": process.name,
                "pm2_id": process.pm2_id,
                "cwd": str(process.cwd) if process.cwd else None,
                "status": process.status,
                "restart_count": process.restart_count,
                "port": process.port,
                "project_name": process.project_name,
                "managed": process.managed,
                "warning": process.warning,
            }
            for process in inventory.processes
        ],
    }


def _process_status(
    raw_process: dict,
    *,
    project_root: Path,
    project_paths: dict[Path, str],
    managed_keys: set[tuple[str, str]],
) -> PM2ProcessStatus:
    name = str(raw_process.get("name") or raw_process.get("pm_id") or "unknown")
    pm2_id = None if raw_process.get("pm_id") is None else str(raw_process.get("pm_id"))
    pm2_env = raw_process.get("pm2_env") if isinstance(raw_process.get("pm2_env"), dict) else {}
    cwd = _optional_path(pm2_env.get("pm_cwd") or raw_process.get("pm_cwd"))
    status = _optional_str(pm2_env.get("status"))
    restart_count = _optional_int(pm2_env.get("restart_time"))
    port = _extract_port(pm2_env)
    project_name = _project_for_cwd(cwd, project_root=project_root, project_paths=project_paths)
    managed = bool(project_name and (project_name, name) in managed_keys)
    warning = None
    if project_name is None:
        warning = "outside_project_root"
    elif not managed:
        warning = "unmanaged_pm2_process"

    return PM2ProcessStatus(
        name=name,
        pm2_id=pm2_id,
        cwd=cwd,
        status=status,
        restart_count=restart_count,
        port=port,
        project_name=project_name,
        managed=managed,
        warning=warning,
    )


def _project_for_cwd(cwd: Path | None, *, project_root: Path, project_paths: dict[Path, str]) -> str | None:
    if cwd is None:
        return None
    try:
        resolved_root = project_root.resolve(strict=True)
        resolved_cwd = cwd.resolve(strict=False)
    except OSError:
        return None
    if not resolved_cwd.is_relative_to(resolved_root):
        return None
    for project_path, project_name in project_paths.items():
        if resolved_cwd == project_path or resolved_cwd.is_relative_to(project_path):
            return project_name
    return None


def _extract_port(pm2_env: dict) -> int | None:
    for key in ("PORT", "port"):
        value = pm2_env.get(key)
        parsed = _optional_int(value)
        if parsed is not None:
            return parsed
    env = pm2_env.get("env")
    if isinstance(env, dict):
        for key in ("PORT", "port"):
            parsed = _optional_int(env.get(key))
            if parsed is not None:
                return parsed
    return None


def _optional_path(value: object) -> Path | None:
    return Path(value) if isinstance(value, str) and value else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
