from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from nginx_conf.configs import NginxConfig, ProjectInventory

PROC_ROOT = Path("/proc")


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
    uncheckable_projects: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings


def check_django_allowed_hosts(projects: tuple[ProjectInventory, ...]) -> AllowedHostsInventory:
    findings = []
    uncheckable = []
    for project in projects:
        if not is_django_project(project.project_path):
            continue
        settings = _read_allowed_hosts(project.project_path)
        if settings is None:
            uncheckable.append(project.project_name)
        else:
            for config in project.configs:
                findings.extend(_find_missing_hosts(project, config, settings))
    return AllowedHostsInventory(findings=tuple(findings), uncheckable_projects=tuple(uncheckable))


def allowed_hosts_inventory_to_dict(inventory: AllowedHostsInventory) -> dict[str, object]:
    return {
        "ok": inventory.ok,
        "uncheckable_projects": list(inventory.uncheckable_projects),
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
    try:
        module_name = _running_settings_module(project_path)
        return _module_allowed_hosts(project_path.resolve(), module_name, set()) if module_name else None
    except OSError, RuntimeError:
        return None


def _running_settings_module(project_path: Path) -> str | None:
    try:
        processes = list(PROC_ROOT.iterdir())
    except OSError:
        return None
    modules = set()
    for process in processes:
        if not process.name.isdecimal():
            continue
        try:
            cwd = (process / "cwd").resolve(strict=True)
        except OSError:
            continue
        if not cwd.is_relative_to(project_path.resolve()):
            continue
        try:
            entries = (process / "environ").read_bytes().split(b"\0")
            for entry in entries:
                if entry.startswith(b"DJANGO_SETTINGS_MODULE="):
                    modules.add(entry.split(b"=", 1)[1].decode("utf-8"))
        except FileNotFoundError, ProcessLookupError:
            continue
        except OSError, UnicodeError:
            return None
    return next(iter(modules)) if len(modules) == 1 else None


def _module_allowed_hosts(
    project_path: Path, module_name: str, seen: set[str], *, exports_only: bool = False
) -> _AllowedHostsSettings | None:
    if module_name in seen or not all(part.isidentifier() for part in module_name.split(".")):
        return None
    seen = seen | {module_name}
    module_path = project_path.joinpath(*module_name.split("."))
    candidates = (module_path / "__init__.py", module_path.with_suffix(".py"))
    python_file = next((path for path in candidates if path.is_file()), None)
    if python_file is None or not python_file.resolve().is_relative_to(project_path):
        return None
    try:
        module = ast.parse(python_file.read_text(encoding="utf-8"))
    except OSError, UnicodeError, SyntaxError:
        return None
    if exports_only and any(isinstance(node, ast.Name) and node.id == "__all__" for node in ast.walk(module)):
        return None
    hosts = _parse_allowed_hosts(module, project_path, module_name, python_file.name == "__init__.py", seen)
    return _AllowedHostsSettings(path=python_file, hosts=hosts) if hosts is not None else None


def _parse_allowed_hosts(
    module: ast.Module, project_path: Path, module_name: str, package: bool, seen: set[str]
) -> tuple[str, ...] | None:
    hosts = None
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "ALLOWED_HOSTS" for target in statement.targets):
                hosts = _literal_string_sequence(statement.value, hosts)
                continue
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == "ALLOWED_HOSTS":
                hosts = _literal_string_sequence(statement.value, hosts)
                continue
        elif isinstance(statement, ast.AugAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == "ALLOWED_HOSTS":
                value = _literal_string_sequence(statement.value)
                hosts = (
                    (*hosts, *value)
                    if hosts is not None and value is not None and isinstance(statement.op, ast.Add)
                    else None
                )
                continue
        elif isinstance(statement, ast.ImportFrom):
            imports_hosts = any(
                alias.name == "*" or (alias.asname or alias.name) == "ALLOWED_HOSTS" for alias in statement.names
            )
            if imports_hosts:
                if any(
                    (alias.asname or alias.name) == "ALLOWED_HOSTS" and alias.name != "ALLOWED_HOSTS"
                    for alias in statement.names
                ):
                    hosts = None
                    continue
                parts = module_name.split(".") if package else module_name.split(".")[:-1]
                base = parts[: len(parts) - statement.level + 1] if statement.level else []
                imported_name = ".".join([*base, *(statement.module or "").split(".")]).strip(".")
                inherited = (
                    _module_allowed_hosts(
                        project_path,
                        imported_name,
                        seen,
                        exports_only=any(alias.name == "*" for alias in statement.names),
                    )
                    if statement.level <= len(parts)
                    else None
                )
                hosts = inherited.hosts if inherited else None
                continue
        elif isinstance(statement, ast.Import):
            if any((alias.asname or alias.name.split(".")[0]) == "ALLOWED_HOSTS" for alias in statement.names):
                hosts = None
        if any(
            isinstance(node, ast.Name)
            and node.id in {"ALLOWED_HOSTS", "exec", "eval", "globals", "locals"}
            or isinstance(node, ast.Attribute)
            and node.attr == "ALLOWED_HOSTS"
            for node in ast.walk(statement)
        ):
            hosts = None
    return tuple(hosts) if hosts is not None else None


def _literal_string_sequence(node: ast.AST | None, hosts: tuple[str, ...] | None = None) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name) and node.id == "ALLOWED_HOSTS":
        return hosts
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        values: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.append(item.value)
        return tuple(values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string_sequence(node.left, hosts)
        right = _literal_string_sequence(node.right, hosts)
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
