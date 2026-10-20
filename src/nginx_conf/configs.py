from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InventoryIssue:
    code: str
    message: str
    subject: str | None = None
    related: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EnabledSymlink:
    link_path: Path
    raw_target: str
    resolved_target: Path


@dataclass(frozen=True, slots=True)
class NginxServerBlock:
    ordinal: int
    server_names: tuple[str, ...]
    listen_ports: tuple[int, ...]
    certbot_eligible: bool
    certbot_blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NginxConfig:
    project_name: str
    config_path: Path
    server_blocks: tuple[NginxServerBlock, ...] = ()
    enabled: bool = False
    enabled_symlinks: tuple[EnabledSymlink, ...] = ()

    @property
    def domain_groups(self) -> tuple[NginxServerBlock, ...]:
        return group_server_blocks(self.server_blocks)


def group_server_blocks(blocks: tuple[NginxServerBlock, ...]) -> tuple[NginxServerBlock, ...]:
    groups: dict[frozenset[str], list[NginxServerBlock]] = {}
    for block in blocks:
        groups.setdefault(frozenset(block.server_names), []).append(block)

    result: list[NginxServerBlock] = []
    for names, members in groups.items():
        # Only fold the standard HTTP/HTTPS pair with exactly the same domains.
        if names and len(members) == 2 and {block.listen_ports for block in members} == {(80,), (443,)}:
            https = next(block for block in members if block.listen_ports == (443,))
            result.append(replace(https, listen_ports=(80, 443)))
        else:
            result.extend(members)
    return tuple(sorted(result, key=lambda block: block.ordinal))


def certificate_server_group(blocks: tuple[NginxServerBlock, ...], domains: tuple[str, ...]) -> NginxServerBlock | None:
    groups = group_server_blocks(blocks)
    names = set(domains)
    matching = [group for group in groups if set(group.server_names) == names]
    if len(matching) != 1 or any(names.intersection(group.server_names) for group in groups if group is not matching[0]):
        return None
    return matching[0]


@dataclass(frozen=True, slots=True)
class ProjectInventory:
    project_name: str
    project_path: Path
    configs: tuple[NginxConfig, ...]
    domains: tuple[str, ...]
    issues: tuple[InventoryIssue, ...] = ()

    @property
    def complete_for_nginx(self) -> bool:
        return bool(self.configs)


@dataclass(frozen=True, slots=True)
class NginxInventory:
    project_root: Path
    enabled_root: Path
    projects: tuple[ProjectInventory, ...]
    issues: tuple[InventoryIssue, ...] = ()


def find_project_configs(root: Path) -> list[NginxConfig]:
    root = Path(root)
    if not root.is_dir():
        return []

    base_root = root.resolve()
    configs: list[NginxConfig] = []
    for project_dir in sorted(root.iterdir()):
        if project_dir.name == ".venv" or project_dir.is_symlink() or not project_dir.is_dir():
            continue
        project_configs, _issues = _find_project_configs(project_dir, base_root)
        configs.extend(project_configs)
    return configs


def discover_inventory(project_root: Path, enabled_root: Path) -> NginxInventory:
    project_root = Path(project_root)
    enabled_root = Path(enabled_root)
    issues: list[InventoryIssue] = []

    if not project_root.is_dir():
        return NginxInventory(
            project_root=project_root,
            enabled_root=enabled_root,
            projects=(),
            issues=(
                InventoryIssue(
                    code="project_root_missing",
                    message=f"Projektwurzel existiert nicht: {project_root}",
                    subject=str(project_root),
                ),
            ),
        )

    base_root = project_root.resolve()
    project_records: list[tuple[str, Path, list[NginxConfig], list[InventoryIssue]]] = []
    config_by_resolved_path: dict[Path, NginxConfig] = {}

    for project_dir in sorted(project_root.iterdir()):
        if project_dir.name == ".venv":
            continue
        if project_dir.is_symlink():
            issues.append(
                InventoryIssue(
                    code="project_symlink_skipped",
                    message=f"Projekt-Symlink wird nicht verfolgt: {project_dir.name}",
                    subject=str(project_dir),
                )
            )
            continue

        if not project_dir.is_dir():
            continue

        configs, project_issues = _find_project_configs(project_dir, base_root)
        if not configs:
            project_issues.append(
                InventoryIssue(
                    code="missing_nginx_config",
                    message=(
                        "Keine Nginx-Konfiguration gefunden. Bitte stelle sicher, "
                        "dass eine *.nginx-Datei im Projektordner liegt."
                    ),
                    subject=str(project_dir),
                )
            )

        for config in configs:
            config_by_resolved_path[config.config_path.resolve()] = config

        project_records.append((project_dir.name, project_dir, configs, project_issues))

    enabled_links_by_config = _scan_enabled_symlinks(enabled_root, base_root, config_by_resolved_path, issues)
    project_issues_by_name = {name: list(project_issues) for name, _, _, project_issues in project_records}

    for resolved_path, links in enabled_links_by_config.items():
        if len(links) > 1:
            config = config_by_resolved_path[resolved_path]
            project_issues_by_name[config.project_name].append(
                InventoryIssue(
                    code="duplicate_enabled_symlink",
                    message=f"Mehrere enabled-Symlinks zeigen auf {config.config_path}",
                    subject=str(config.config_path),
                    related=tuple(str(link.link_path) for link in links),
                )
            )

    active_records = [
        (name, path, [config for config in configs if config.config_path.resolve() in enabled_links_by_config], issues)
        for name, path, configs, issues in project_records
    ]
    _add_duplicate_conflicts(project_records, project_issues_by_name, set(enabled_links_by_config))
    for name, path, configs, _ in active_records:
        if len(configs) > 1:
            project_issues_by_name[name].append(
                InventoryIssue(
                    code="multiple_active_configs",
                    message="Mehrere Configs dieses Projekts sind aktiv. Bitte die enabled-Symlinks bereinigen.",
                    subject=str(path),
                    related=tuple(str(config.config_path) for config in configs),
                )
            )

    projects: list[ProjectInventory] = []
    for project_name, project_path, configs, _project_issues in project_records:
        activated_configs = []
        for config in configs:
            links = tuple(enabled_links_by_config.get(config.config_path.resolve(), ()))
            activated_configs.append(
                replace(
                    config,
                    enabled=bool(links),
                    enabled_symlinks=links,
                )
            )

        domains = tuple(
            sorted(
                {
                    server_name
                    for config in activated_configs
                    for block in config.server_blocks
                    for server_name in block.server_names
                }
            )
        )
        projects.append(
            ProjectInventory(
                project_name=project_name,
                project_path=project_path,
                configs=tuple(activated_configs),
                domains=domains,
                issues=tuple(project_issues_by_name[project_name]),
            )
        )

    return NginxInventory(
        project_root=project_root,
        enabled_root=enabled_root,
        projects=tuple(projects),
        issues=tuple(issues),
    )


def inventory_to_dict(inventory: NginxInventory) -> dict[str, object]:
    return {
        "project_root": str(inventory.project_root),
        "enabled_root": str(inventory.enabled_root),
        "issues": [_issue_to_dict(issue) for issue in inventory.issues],
        "projects": [
            {
                "project_name": project.project_name,
                "project_path": str(project.project_path),
                "complete_for_nginx": project.complete_for_nginx,
                "domains": list(project.domains),
                "issues": [_issue_to_dict(issue) for issue in project.issues],
                "configs": [
                    {
                        "project_name": config.project_name,
                        "config_path": str(config.config_path),
                        "config_name": config.config_path.name,
                        "enabled": config.enabled,
                        "enabled_symlinks": [
                            {
                                "link_path": str(link.link_path),
                                "raw_target": link.raw_target,
                                "resolved_target": str(link.resolved_target),
                            }
                            for link in config.enabled_symlinks
                        ],
                        "server_blocks": [
                            {
                                "ordinal": block.ordinal,
                                "server_names": list(block.server_names),
                                "listen_ports": list(block.listen_ports),
                                "certbot_eligible": block.certbot_eligible,
                                "certbot_blockers": list(block.certbot_blockers),
                            }
                            for block in config.server_blocks
                        ],
                    }
                    for config in project.configs
                ],
            }
            for project in inventory.projects
        ],
    }


def _issue_to_dict(issue: InventoryIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "message": issue.message,
        "subject": issue.subject,
        "related": list(issue.related),
    }


def _find_project_configs(project_dir: Path, base_root: Path) -> tuple[list[NginxConfig], list[InventoryIssue]]:
    configs: list[NginxConfig] = []
    issues: list[InventoryIssue] = []

    for current_root, dirnames, filenames in os.walk(project_dir, followlinks=False):
        current_path = Path(current_root)
        dirnames[:] = [dirname for dirname in dirnames if dirname != ".venv"]
        skipped_symlink_dirs = [dirname for dirname in dirnames if (current_path / dirname).is_symlink()]
        for dirname in sorted(skipped_symlink_dirs):
            issues.append(
                InventoryIssue(
                    code="config_dir_symlink_skipped",
                    message=f"Symlink-Verzeichnis wird nicht als Config-Quelle verfolgt: {current_path / dirname}",
                    subject=str(current_path / dirname),
                )
            )
        dirnames[:] = sorted(dirname for dirname in dirnames if not (current_path / dirname).is_symlink())

        for filename in sorted(filenames):
            if not filename.endswith(".nginx"):
                continue

            config_path = current_path / filename
            if config_path.is_symlink():
                issues.append(
                    InventoryIssue(
                        code="config_symlink_skipped",
                        message=f"Config-Symlink wird nicht als Projektconfig verwendet: {config_path}",
                        subject=str(config_path),
                    )
                )
                continue

            resolved_path = config_path.resolve()
            if not resolved_path.is_relative_to(base_root):
                issues.append(
                    InventoryIssue(
                        code="config_path_escape",
                        message=f"Config liegt nicht unterhalb der Projektwurzel: {config_path}",
                        subject=str(config_path),
                    )
                )
                continue

            configs.append(
                NginxConfig(
                    project_name=project_dir.name,
                    config_path=config_path,
                    server_blocks=parse_server_blocks(config_path),
                )
            )

    return configs, issues


def _scan_enabled_symlinks(
    enabled_root: Path,
    base_root: Path,
    known_configs: dict[Path, NginxConfig],
    issues: list[InventoryIssue],
) -> dict[Path, list[EnabledSymlink]]:
    enabled_links_by_config: dict[Path, list[EnabledSymlink]] = {}

    if not enabled_root.is_dir():
        issues.append(
            InventoryIssue(
                code="enabled_root_missing",
                message=f"Nginx enabled-Verzeichnis existiert nicht: {enabled_root}",
                subject=str(enabled_root),
            )
        )
        return enabled_links_by_config

    admin_config = (enabled_root.parent / "sites-available" / "nginx-conf").resolve()
    for link_path in sorted(enabled_root.iterdir()):
        if not link_path.is_symlink():
            issues.append(
                InventoryIssue(
                    code="enabled_entry_not_symlink",
                    message=f"Enabled-Eintrag ist kein Symlink: {link_path}",
                    subject=str(link_path),
                )
            )
            continue

        raw_target = os.readlink(link_path)
        target_path = Path(raw_target)
        if not target_path.is_absolute():
            target_path = link_path.parent / target_path
        resolved_target = target_path.resolve(strict=False)
        enabled_link = EnabledSymlink(link_path=link_path, raw_target=raw_target, resolved_target=resolved_target)

        if not target_path.exists():
            issues.append(
                InventoryIssue(
                    code="broken_enabled_symlink",
                    message=f"Enabled-Symlink zeigt auf ein fehlendes Ziel: {link_path}",
                    subject=str(link_path),
                    related=(raw_target,),
                )
            )
            continue

        if link_path.name == "nginx-conf" and resolved_target == admin_config and resolved_target.is_file():
            continue

        if not resolved_target.is_relative_to(base_root):
            issues.append(
                InventoryIssue(
                    code="foreign_enabled_symlink",
                    message=f"Enabled-Symlink zeigt nicht auf eine bekannte Projektconfig: {link_path}",
                    subject=str(link_path),
                    related=(str(resolved_target),),
                )
            )
            continue

        if resolved_target not in known_configs:
            issues.append(
                InventoryIssue(
                    code="unknown_enabled_symlink",
                    message=f"Enabled-Symlink zeigt auf keine erkannte *.nginx-Projektconfig: {link_path}",
                    subject=str(link_path),
                    related=(str(resolved_target),),
                )
            )
            continue

        enabled_links_by_config.setdefault(resolved_target, []).append(enabled_link)

    return enabled_links_by_config


def _add_duplicate_conflicts(
    project_records: list[tuple[str, Path, list[NginxConfig], list[InventoryIssue]]],
    issues_by_project: dict[str, list[InventoryIssue]],
    active_paths: set[Path],
) -> None:
    domains: dict[tuple[Path | None, str], list[str]] = {}

    for _project_name, _project_path, configs, _issues in project_records:
        for config in configs:
            # Inactive alternatives only conflict within their own file.
            scope = None if config.config_path.resolve() in active_paths else config.config_path
            for block in config.domain_groups:
                block_subject = f"{config.config_path}#server[{block.ordinal}]"
                for server_name in block.server_names:
                    domains.setdefault((scope, server_name), []).append(block_subject)

    for (_scope, server_name), locations in domains.items():
        if len(locations) < 2:
            continue
        issue = InventoryIssue(
            code="duplicate_domain",
            message=f"Domain kommt in mehreren server_name-Gruppen vor: {server_name}",
            subject=server_name,
            related=tuple(locations),
        )
        _append_issue_to_related_projects(issue, project_records, issues_by_project)


def _append_issue_to_related_projects(
    issue: InventoryIssue,
    project_records: list[tuple[str, Path, list[NginxConfig], list[InventoryIssue]]],
    issues_by_project: dict[str, list[InventoryIssue]],
) -> None:
    for project_name, _project_path, configs, _issues in project_records:
        config_paths = {str(config.config_path) for config in configs}
        if any(location.split("#", maxsplit=1)[0] in config_paths for location in issue.related):
            issues_by_project[project_name].append(issue)


def parse_server_blocks(config_path: Path) -> tuple[NginxServerBlock, ...]:
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return ()

    blocks: list[NginxServerBlock] = []
    for ordinal, block_tokens in enumerate(_extract_server_block_tokens(_tokenize_nginx(content)), start=1):
        server_names: list[str] = []
        listen_ports: list[int] = []
        for directive in _top_level_directives(block_tokens):
            name = directive[0]
            values = directive[1:]
            if name == "server_name":
                server_names.extend(value for value in values if value)
            elif name == "listen":
                listen_ports.extend(port for port in (_parse_listen_port(value) for value in values) if port is not None)

        blockers = _certbot_blockers(tuple(server_names))
        blocks.append(
            NginxServerBlock(
                ordinal=ordinal,
                server_names=tuple(server_names),
                listen_ports=tuple(sorted(set(listen_ports))),
                certbot_eligible=not blockers,
                certbot_blockers=blockers,
            )
        )

    return tuple(blocks)


def _tokenize_nginx(content: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    skip_comment = False

    for char in content:
        if skip_comment:
            if char == "\n":
                skip_comment = False
            continue

        if quote is not None:
            if escaped:
                current.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            else:
                current.append(char)
            continue

        if char in {"'", '"'}:
            quote = char
            continue

        if char == "#":
            if current:
                tokens.append("".join(current))
                current = []
            skip_comment = True
            continue

        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue

        if char in {"{", "}", ";"}:
            if current:
                tokens.append("".join(current))
                current = []
            tokens.append(char)
            continue

        current.append(char)

    if current:
        tokens.append("".join(current))

    return tokens


def _extract_server_block_tokens(tokens: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    index = 0
    while index < len(tokens) - 1:
        if tokens[index] != "server" or tokens[index + 1] != "{":
            index += 1
            continue

        depth = 1
        block: list[str] = []
        index += 2
        while index < len(tokens) and depth:
            token = tokens[index]
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth == 0:
                    break
            block.append(token)
            index += 1

        blocks.append(block)
        index += 1

    return blocks


def _top_level_directives(block_tokens: list[str]) -> list[list[str]]:
    directives: list[list[str]] = []
    current: list[str] = []
    depth = 0

    for token in block_tokens:
        if token == "{":
            depth += 1
            current = []
            continue
        if token == "}":
            depth = max(0, depth - 1)
            current = []
            continue
        if depth > 0:
            continue
        if token == ";":
            if current:
                directives.append(current)
                current = []
            continue

        current.append(token)

    return directives


def _parse_listen_port(value: str) -> int | None:
    if value in {"default_server", "ssl", "http2", "proxy_protocol", "reuseport"} or value.startswith("unix:"):
        return None

    if value.isdigit():
        return int(value)

    match = re.search(r"(?::|\]:)(\d+)$", value)
    if match:
        return int(match.group(1))

    return None


def _certbot_blockers(server_names: tuple[str, ...]) -> tuple[str, ...]:
    blockers: list[str] = []

    if not server_names:
        return ("missing_server_name",)

    for server_name in server_names:
        if server_name in {"_", "localhost"}:
            blockers.append(f"not_public_domain:{server_name}")
            continue
        if "*" in server_name:
            blockers.append(f"wildcard:{server_name}")
            continue
        if "$" in server_name:
            blockers.append(f"variable:{server_name}")
            continue
        try:
            ipaddress.ip_address(server_name)
        except ValueError:
            pass
        else:
            blockers.append(f"ip_address:{server_name}")
            continue
        if "." not in server_name:
            blockers.append(f"single_label:{server_name}")

    return tuple(blockers)
