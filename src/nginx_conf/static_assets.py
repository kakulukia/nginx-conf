from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

IGNORED_DIRS = {".git", ".mypy_cache", ".ruff_cache", ".tox", ".venv", "__pycache__", "node_modules", "venv"}
STATIC_DIR_LOCATIONS = {
    "assets": "/assets/",
    "build": "/static/",
    "dist": "/static/",
    "public": "/static/",
    "static": "/static/",
    "staticfiles": "/static/",
}


class StaticAssetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StaticAssetCandidate:
    relative_path: str
    asset_path: Path
    location_path: str


def discover_static_asset_candidates(project_path: Path, *, max_depth: int = 3) -> tuple[StaticAssetCandidate, ...]:
    candidates: list[StaticAssetCandidate] = []
    if not project_path.is_dir():
        return ()
    resolved_project = project_path.resolve(strict=True)
    for path in sorted(project_path.rglob("*")):
        if not path.is_dir() or path.is_symlink():
            continue
        relative_parts = path.relative_to(project_path).parts
        if len(relative_parts) > max_depth or any(part in IGNORED_DIRS for part in relative_parts):
            continue
        location_path = STATIC_DIR_LOCATIONS.get(path.name)
        if location_path is None:
            continue
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_relative_to(resolved_project):
            continue
        candidates.append(
            StaticAssetCandidate(
                relative_path=str(path.relative_to(project_path)),
                asset_path=resolved_path,
                location_path=location_path,
            )
        )
    return tuple(candidates)


def select_static_asset_candidate(project_path: Path, relative_path: str) -> StaticAssetCandidate:
    normalized = Path(relative_path)
    if normalized.is_absolute() or ".." in normalized.parts or str(normalized) in {"", "."}:
        raise StaticAssetError("Static path must be a relative project path")
    candidates = {candidate.relative_path: candidate for candidate in discover_static_asset_candidates(project_path)}
    try:
        return candidates[str(normalized)]
    except KeyError as error:
        raise StaticAssetError("Static path is not an allowed project static asset candidate") from error


def insert_static_location(content: str, candidate: StaticAssetCandidate) -> tuple[str, bool]:
    alias_path = f"{candidate.asset_path}/"
    location_re = re.compile(rf"location\s+{re.escape(candidate.location_path)}\s*\{{")
    alias_line = f"alias {alias_path};"
    if location_re.search(content):
        if alias_line in content:
            return content, False
        raise StaticAssetError(f"location {candidate.location_path} already exists with a different alias")

    insert_at = _first_server_block_end(content)
    block = _render_location_block(candidate.location_path, alias_path)
    return f"{content[:insert_at].rstrip()}\n{block}\n{content[insert_at:].lstrip()}", True


def static_asset_candidate_to_dict(candidate: StaticAssetCandidate) -> dict[str, object]:
    return {
        "relative_path": candidate.relative_path,
        "asset_path": str(candidate.asset_path),
        "location_path": candidate.location_path,
    }


def _render_location_block(location_path: str, alias_path: str) -> str:
    return (
        "    # Managed by nginx-conf static-assets workflow.\n"
        f"    location {location_path} {{\n"
        f"        alias {alias_path};\n"
        "    }"
    )


def _first_server_block_end(content: str) -> int:
    tokens = _tokens_with_positions(content)
    for index, token in enumerate(tokens[:-1]):
        if token[0] != "server" or tokens[index + 1][0] != "{":
            continue
        depth = 1
        for nested_token, start, _end in tokens[index + 2 :]:
            if nested_token == "{":
                depth += 1
            elif nested_token == "}":
                depth -= 1
                if depth == 0:
                    return start
    raise StaticAssetError("No server block found in selected config")


def _tokens_with_positions(content: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    current: list[str] = []
    current_start: int | None = None
    quote: str | None = None
    escaped = False
    skip_comment = False

    def flush(end: int) -> None:
        nonlocal current, current_start
        if current and current_start is not None:
            tokens.append(("".join(current), current_start, end))
            current = []
            current_start = None

    for index, char in enumerate(content):
        if skip_comment:
            if char == "\n":
                skip_comment = False
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            if current_start is None:
                current_start = index
            continue
        if char == "#":
            flush(index)
            skip_comment = True
            continue
        if char.isspace():
            flush(index)
            continue
        if char in {"{", "}", ";"}:
            flush(index)
            tokens.append((char, index, index + 1))
            continue
        if current_start is None:
            current_start = index
        current.append(char)
    flush(len(content))
    return tokens
