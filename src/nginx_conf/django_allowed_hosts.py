from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from nginx_conf.configs import NginxConfig, ProjectInventory

IGNORED_DIRS = {".git", ".mypy_cache", ".ruff_cache", ".tox", ".venv", "__pycache__", "node_modules", "venv"}


@dataclass(frozen=True, slots=True)
class AllowedHostsFinding:
    project_name: str
    config_path: Path
    server_block_ordinal: int
    settings_path: Path
    missing_domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AllowedHostsInventory:
    findings: tuple[AllowedHostsFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


def check_django_allowed_hosts(projects: tuple[ProjectInventory, ...]) -> AllowedHostsInventory:
    findings = [
        finding for project in projects if is_django_project(project.project_path) for finding in _check_project(project)
    ]
    return AllowedHostsInventory(findings=tuple(findings))


def allowed_hosts_inventory_to_dict(inventory: AllowedHostsInventory) -> dict[str, object]:
    return {
        "ok": inventory.ok,
        "findings": [
            {
                "project_name": finding.project_name,
                "config_path": str(finding.config_path),
                "server_block_ordinal": finding.server_block_ordinal,
                "settings_path": str(finding.settings_path),
                "missing_domains": list(finding.missing_domains),
            }
            for finding in inventory.findings
        ],
    }


def is_django_project(project_path: Path) -> bool:
    if (project_path / "manage.py").is_file():
        return True
    for dependency_file in _iter_dependency_files(project_path):
        try:
            content = dependency_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "django" in content.lower():
            return True
    return False


def findings_for_config(
    project: ProjectInventory,
    config_path: Path,
    *,
    only_enabled: bool = False,
) -> tuple[AllowedHostsFinding, ...]:
    if not is_django_project(project.project_path):
        return ()
    settings = _read_allowed_hosts(project.project_path)
    if settings is None:
        return ()

    findings: list[AllowedHostsFinding] = []
    for config in project.configs:
        if config.config_path != config_path:
            continue
        if only_enabled and not config.enabled:
            continue
        findings.extend(_find_missing_hosts(project, config, settings))
    return tuple(findings)


def _check_project(project: ProjectInventory) -> tuple[AllowedHostsFinding, ...]:
    settings = _read_allowed_hosts(project.project_path)
    if settings is None:
        return ()
    return tuple(finding for config in project.configs for finding in _find_missing_hosts(project, config, settings))


@dataclass(frozen=True, slots=True)
class _AllowedHostsSettings:
    path: Path
    hosts: tuple[str, ...]


def _find_missing_hosts(
    project: ProjectInventory,
    config: NginxConfig,
    settings: _AllowedHostsSettings,
) -> tuple[AllowedHostsFinding, ...]:
    findings: list[AllowedHostsFinding] = []
    for block in config.server_blocks:
        domains = tuple(domain for domain in block.server_names if _should_check_domain(domain))
        missing = tuple(domain for domain in domains if not _host_allowed(domain, settings.hosts))
        if missing:
            findings.append(
                AllowedHostsFinding(
                    project_name=project.project_name,
                    config_path=config.config_path,
                    server_block_ordinal=block.ordinal,
                    settings_path=settings.path,
                    missing_domains=missing,
                )
            )
    return tuple(findings)


def _read_allowed_hosts(project_path: Path) -> _AllowedHostsSettings | None:
    for python_file in _iter_python_files(project_path):
        try:
            content = python_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "ALLOWED_HOSTS" not in content:
            continue
        hosts = _parse_allowed_hosts(content)
        if hosts is not None:
            return _AllowedHostsSettings(path=python_file, hosts=hosts)
    return None


def _parse_allowed_hosts(content: str) -> tuple[str, ...] | None:
    try:
        module = ast.parse(content)
    except SyntaxError:
        return None

    last_value: ast.AST | None = None
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "ALLOWED_HOSTS" for target in statement.targets):
                last_value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == "ALLOWED_HOSTS":
                last_value = statement.value

    if last_value is None:
        return None
    parsed = _literal_string_sequence(last_value)
    return tuple(parsed) if parsed is not None else None


def _literal_string_sequence(node: ast.AST | None) -> tuple[str, ...] | None:
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        values: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.append(item.value)
        return tuple(values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string_sequence(node.left)
        right = _literal_string_sequence(node.right)
        if left is not None and right is not None:
            return (*left, *right)
    return None


def _host_allowed(domain: str, allowed_hosts: tuple[str, ...]) -> bool:
    if "*" in allowed_hosts:
        return True
    for allowed in allowed_hosts:
        if domain == allowed:
            return True
        if allowed.startswith(".") and (domain == allowed[1:] or domain.endswith(allowed)):
            return True
    return False


def _should_check_domain(domain: str) -> bool:
    return bool(domain) and domain != "_" and "*" not in domain


def _iter_dependency_files(project_path: Path):
    for name in ("pyproject.toml", "requirements.txt", "requirements.in", "Pipfile"):
        candidate = project_path / name
        if candidate.is_file():
            yield candidate


def _iter_python_files(project_path: Path):
    for path in sorted(project_path.rglob("*.py")):
        if any(part in IGNORED_DIRS for part in path.relative_to(project_path).parts):
            continue
        yield path
