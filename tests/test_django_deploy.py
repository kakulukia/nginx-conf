from pathlib import Path

from nginx_conf.configs import ProjectInventory
from nginx_conf.django_deploy import django_deploy_statuses_to_dict, inspect_django_deploy


def project(path: Path) -> ProjectInventory:
    return ProjectInventory(
        project_name=path.name,
        project_path=path,
        configs=(),
        domains=(),
    )


def test_inspect_django_deploy_enables_migrate_and_collectstatic_for_project_venv(tmp_path: Path) -> None:
    project_path = tmp_path / "demo-app"
    venv = project_path / ".venv/bin"
    venv.mkdir(parents=True)
    (project_path / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    status = inspect_django_deploy((project(project_path),))[0]
    actions = {action.operation: action for action in status.actions}

    assert status.is_django is True
    assert status.manage_py == project_path / "manage.py"
    assert status.python_path == project_path / ".venv/bin/python"
    assert actions["migrate"].available is True
    assert actions["collectstatic"].available is True
    assert actions["compress"].available is False
    assert actions["compress"].blockers == ("compress_not_detected",)


def test_inspect_django_deploy_enables_compress_only_with_dependency_marker(tmp_path: Path) -> None:
    project_path = tmp_path / "demo-app"
    venv = project_path / ".venv/bin"
    venv.mkdir(parents=True)
    (project_path / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (project_path / "requirements.txt").write_text("django-compressor==4.5\n", encoding="utf-8")
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    payload = django_deploy_statuses_to_dict(inspect_django_deploy((project(project_path),)))
    actions = {action["operation"]: action for action in payload[0]["actions"]}

    assert payload[0]["compress_available"] is True
    assert actions["compress"]["available"] is True


def test_inspect_django_deploy_reports_missing_manage_py_or_environment(tmp_path: Path) -> None:
    dependency_only = tmp_path / "dependency-only"
    dependency_only.mkdir()
    (dependency_only / "pyproject.toml").write_text('dependencies = ["django"]\n', encoding="utf-8")
    no_env = tmp_path / "no-env"
    no_env.mkdir()
    (no_env / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")

    statuses = {
        status.project_name: status for status in inspect_django_deploy((project(dependency_only), project(no_env)))
    }

    dependency_actions = {action.operation: action for action in statuses["dependency-only"].actions}
    no_env_actions = {action.operation: action for action in statuses["no-env"].actions}
    assert dependency_actions["migrate"].blockers == ("missing_manage_py", "missing_python_environment")
    assert no_env_actions["migrate"].blockers == ("missing_python_environment",)
