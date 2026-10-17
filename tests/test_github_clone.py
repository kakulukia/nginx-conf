from pathlib import Path

import pytest

from nginx_conf.github_clone import GitHubCloneValidationError, plan_github_clone


def test_plan_github_clone_accepts_https_and_normalizes_target(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    root.mkdir(parents=True)

    plan = plan_github_clone("https://github.com/freilandkiwis/demo-app", project_root=root)

    assert plan.clone_url == "https://github.com/freilandkiwis/demo-app.git"
    assert plan.owner == "freilandkiwis"
    assert plan.repo == "demo-app"
    assert plan.target_project == "demo-app"
    assert plan.target_path == root.resolve() / "demo-app"


def test_plan_github_clone_accepts_ssh_url(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    root.mkdir(parents=True)

    plan = plan_github_clone("git@github.com:freilandkiwis/demo-app.git", project_root=root)

    assert plan.clone_url == "git@github.com:freilandkiwis/demo-app.git"
    assert plan.target_path == root.resolve() / "demo-app"


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://gitlab.com/freilandkiwis/demo-app.git",
        "https://github.com/freilandkiwis/demo-app.git?token=x",
        "https://github.com/freilandkiwis/demo-app/extra.git",
        "https://github.com/freilandkiwis/../escape.git",
        "ssh://github.com/freilandkiwis/demo-app.git",
    ],
)
def test_plan_github_clone_rejects_unsupported_or_ambiguous_urls(tmp_path: Path, clone_url: str) -> None:
    root = tmp_path / "opt/www"
    root.mkdir(parents=True)

    with pytest.raises(GitHubCloneValidationError):
        plan_github_clone(clone_url, project_root=root)


def test_plan_github_clone_rejects_existing_target(tmp_path: Path) -> None:
    root = tmp_path / "opt/www"
    (root / "demo-app").mkdir(parents=True)

    with pytest.raises(GitHubCloneValidationError, match="already exists"):
        plan_github_clone("https://github.com/freilandkiwis/demo-app.git", project_root=root)
