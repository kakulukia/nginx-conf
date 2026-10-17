from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class GitHubCloneValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GitHubClonePlan:
    clone_url: str
    owner: str
    repo: str
    target_project: str
    target_path: Path


def plan_github_clone(clone_url: str, *, project_root: Path) -> GitHubClonePlan:
    owner, repo, normalized_url = _parse_github_url(clone_url)
    target_project = _target_project_name(repo)
    target_path = _validate_target_path(project_root, target_project)
    return GitHubClonePlan(
        clone_url=normalized_url,
        owner=owner,
        repo=repo,
        target_project=target_project,
        target_path=target_path,
    )


def github_clone_plan_to_dict(plan: GitHubClonePlan) -> dict[str, object]:
    return {
        "clone_url": plan.clone_url,
        "owner": plan.owner,
        "repo": plan.repo,
        "target_project": plan.target_project,
        "target_path": str(plan.target_path),
    }


def _parse_github_url(value: str) -> tuple[str, str, str]:
    clone_url = value.strip()
    if not clone_url:
        raise GitHubCloneValidationError("GitHub clone URL is required")
    if clone_url.startswith("git@github.com:"):
        return _parse_ssh_url(clone_url)
    return _parse_https_url(clone_url)


def _parse_https_url(clone_url: str) -> tuple[str, str, str]:
    parsed = urlparse(clone_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise GitHubCloneValidationError("Only GitHub HTTPS or SSH clone URLs are supported")
    if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
        raise GitHubCloneValidationError("GitHub clone URL must not contain credentials, query or fragment")
    parts = tuple(part for part in parsed.path.strip("/").split("/") if part)
    if len(parts) != 2:
        raise GitHubCloneValidationError("GitHub clone URL must contain owner and repository")
    owner, repo = _validate_owner_repo(parts[0], parts[1])
    return owner, repo, f"https://github.com/{owner}/{repo}.git"


def _parse_ssh_url(clone_url: str) -> tuple[str, str, str]:
    remainder = clone_url.removeprefix("git@github.com:")
    parts = tuple(part for part in remainder.split("/") if part)
    if len(parts) != 2:
        raise GitHubCloneValidationError("GitHub SSH clone URL must contain owner and repository")
    owner, repo = _validate_owner_repo(parts[0], parts[1])
    return owner, repo, f"git@github.com:{owner}/{repo}.git"


def _validate_owner_repo(owner: str, repo: str) -> tuple[str, str]:
    repo_name = repo.removesuffix(".git")
    for value, label in ((owner, "owner"), (repo_name, "repository")):
        if value in {"", ".", ".."} or not NAME_RE.fullmatch(value) or "/" in value:
            raise GitHubCloneValidationError(f"Invalid GitHub {label}: {value}")
    return owner, repo_name


def _target_project_name(repo: str) -> str:
    if repo in {"", ".", ".."} or not NAME_RE.fullmatch(repo):
        raise GitHubCloneValidationError(f"Invalid target project name: {repo}")
    return repo


def _validate_target_path(project_root: Path, target_project: str) -> Path:
    try:
        resolved_root = project_root.resolve(strict=True)
    except OSError as error:
        raise GitHubCloneValidationError(str(error)) from error
    if not resolved_root.is_dir():
        raise GitHubCloneValidationError("Project root is not a directory")
    target_path = resolved_root / target_project
    if target_path.exists() or target_path.is_symlink():
        raise GitHubCloneValidationError(f"Target project already exists: {target_project}")
    if target_path.parent != resolved_root:
        raise GitHubCloneValidationError("Target path escapes project root")
    return target_path
