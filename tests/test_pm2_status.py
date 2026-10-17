import json
from pathlib import Path

from nginx_conf.configs import ProjectInventory
from nginx_conf.pm2_status import pm2_inventory_to_dict, read_pm2_status
from nginx_conf.store import AppDatabase
from nginx_conf.system_actions import CommandResult, pm2_list_command


class FakeRunner:
    def __init__(self, result: CommandResult | None = None, error: Exception | None = None) -> None:
        self.result = result or CommandResult(exit_code=0, stdout="[]", stderr="")
        self.error = error
        self.calls: list[tuple[tuple[str, ...], str | None, int]] = []

    def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
        self.calls.append((argv, stdin, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.result


def make_db(tmp_path: Path) -> AppDatabase:
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    return db


def make_project(root: Path, name: str = "demo-app") -> ProjectInventory:
    project_path = root / name
    project_path.mkdir(parents=True)
    return ProjectInventory(project_name=name, project_path=project_path, configs=(), domains=())


def test_pm2_status_maps_registered_process_to_project(tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project = make_project(project_root)
    db = make_db(tmp_path)
    db.register_pm2_process(
        project_name="demo-app",
        pm2_name="demo-app",
        pm2_id=0,
        cwd=project.project_path,
        start_script=project.project_path / "start.sh",
    )
    runner = FakeRunner(
        CommandResult(
            exit_code=0,
            stdout=json.dumps(
                [
                    {
                        "name": "demo-app",
                        "pm_id": 0,
                        "pm2_env": {
                            "pm_cwd": str(project.project_path),
                            "status": "online",
                            "restart_time": 3,
                            "env": {"PORT": "8010"},
                        },
                    }
                ]
            ),
            stderr="",
        )
    )

    inventory = read_pm2_status(project_root=project_root, projects=(project,), db=db, runner=runner)

    assert runner.calls == [(pm2_list_command().argv, None, 120)]
    assert inventory.error is None
    process = inventory.processes[0]
    assert process.name == "demo-app"
    assert process.pm2_id == "0"
    assert process.cwd == project.project_path
    assert process.status == "online"
    assert process.restart_count == 3
    assert process.port == 8010
    assert process.project_name == "demo-app"
    assert process.managed is True
    assert process.warning is None
    assert pm2_inventory_to_dict(inventory)["processes"][0]["cwd"] == str(project.project_path)


def test_pm2_status_warns_for_unmanaged_project_process(tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project = make_project(project_root)
    db = make_db(tmp_path)
    runner = FakeRunner(
        CommandResult(
            exit_code=0,
            stdout=json.dumps(
                [
                    {
                        "name": "manual-demo",
                        "pm_id": 1,
                        "pm2_env": {
                            "pm_cwd": str(project.project_path / "current"),
                            "status": "stopped",
                            "PORT": 8020,
                        },
                    }
                ]
            ),
            stderr="",
        )
    )

    process = read_pm2_status(project_root=project_root, projects=(project,), db=db, runner=runner).processes[0]

    assert process.project_name == "demo-app"
    assert process.managed is False
    assert process.warning == "unmanaged_pm2_process"
    assert process.port == 8020


def test_pm2_status_warns_for_process_outside_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project = make_project(project_root)
    db = make_db(tmp_path)
    runner = FakeRunner(
        CommandResult(
            exit_code=0,
            stdout=json.dumps(
                [
                    {
                        "name": "foreign",
                        "pm_id": 2,
                        "pm2_env": {"pm_cwd": str(tmp_path / "srv/foreign"), "status": "online"},
                    }
                ]
            ),
            stderr="",
        )
    )

    process = read_pm2_status(project_root=project_root, projects=(project,), db=db, runner=runner).processes[0]

    assert process.project_name is None
    assert process.managed is False
    assert process.warning == "outside_project_root"


def test_pm2_status_reports_invalid_or_failed_pm2_output(tmp_path: Path) -> None:
    project_root = tmp_path / "opt/www"
    project = make_project(project_root)
    db = make_db(tmp_path)

    invalid = read_pm2_status(
        project_root=project_root,
        projects=(project,),
        db=db,
        runner=FakeRunner(CommandResult(exit_code=0, stdout="{", stderr="")),
    )
    failed = read_pm2_status(
        project_root=project_root,
        projects=(project,),
        db=db,
        runner=FakeRunner(CommandResult(exit_code=1, stdout="", stderr="pm2 missing")),
    )
    missing = read_pm2_status(
        project_root=project_root,
        projects=(project,),
        db=db,
        runner=FakeRunner(error=FileNotFoundError("/usr/bin/pm2")),
    )

    assert "invalid JSON" in (invalid.error or "")
    assert failed.error == "pm2 missing"
    assert "konnte nicht ausgefuehrt werden" in (missing.error or "")
