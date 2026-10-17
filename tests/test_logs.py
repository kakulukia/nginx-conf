from pathlib import Path

import pytest

from nginx_conf.logs import (
    LogSection,
    normalize_log_lines,
    read_pm2_log,
    read_system_log,
    tail_text_file,
)
from nginx_conf.system_actions import CommandResult


def test_normalize_log_lines_defaults_and_bounds() -> None:
    assert normalize_log_lines(None) == 100
    assert normalize_log_lines("25") == 25

    with pytest.raises(ValueError):
        normalize_log_lines("0")
    with pytest.raises(ValueError):
        normalize_log_lines("501")
    with pytest.raises(ValueError):
        normalize_log_lines("x")


def test_tail_text_file_returns_last_lines(tmp_path: Path) -> None:
    log = tmp_path / "demo.log"
    log.write_text("one\ntwo\nthree\n", encoding="utf-8")

    assert tail_text_file(log, lines=2) == "two\nthree"


def test_read_system_log_uses_allowlisted_key_and_handles_missing_file(tmp_path: Path) -> None:
    log = tmp_path / "nginx.log"
    log.write_text("ok\n", encoding="utf-8")
    paths = {"nginx-error": ("Nginx error.log", log)}

    section = read_system_log("nginx-error", lines=10, paths=paths)

    assert section == LogSection(key="nginx-error", label="Nginx error.log", lines=10, content="ok")
    with pytest.raises(ValueError):
        read_system_log("/tmp/free.log", paths=paths)

    missing = read_system_log("nginx-error", paths={"nginx-error": ("Nginx error.log", tmp_path / "missing.log")})
    assert missing.ok is False
    assert missing.error


def test_read_pm2_log_uses_nostream_command_and_limits_lines() -> None:
    class Runner:
        def run(self, argv: tuple[str, ...], *, stdin: str | None, timeout_seconds: int) -> CommandResult:
            assert argv == ("/usr/bin/pm2", "logs", "demo-app", "--lines", "50", "--nostream")
            assert stdin is None
            return CommandResult(exit_code=0, stdout="line\n", stderr="")

    section = read_pm2_log("demo-app", runner=Runner(), lines=50)

    assert section.ok is True
    assert section.content == "line\n"
