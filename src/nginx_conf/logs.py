from dataclasses import dataclass
from pathlib import Path

from nginx_conf.system_actions import CommandRunner, pm2_logs_command

DEFAULT_LOG_LINES = 100
MAX_LOG_LINES = 500
MAX_LOG_BYTES = 200_000

SYSTEM_LOG_PATHS: dict[str, tuple[str, Path]] = {
    "nginx-error": ("Nginx error.log", Path("/var/log/nginx/error.log")),
    "nginx-access": ("Nginx access.log", Path("/var/log/nginx/access.log")),
    "certbot": ("Certbot letsencrypt.log", Path("/var/log/letsencrypt/letsencrypt.log")),
}


@dataclass(frozen=True, slots=True)
class LogSection:
    key: str
    label: str
    lines: int
    content: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def normalize_log_lines(value: int | str | None) -> int:
    if value in {None, ""}:
        return DEFAULT_LOG_LINES
    try:
        lines = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Log lines must be a number") from error
    if lines < 1 or lines > MAX_LOG_LINES:
        raise ValueError(f"Log lines must be between 1 and {MAX_LOG_LINES}")
    return lines


def read_system_log(
    key: str,
    *,
    lines: int = DEFAULT_LOG_LINES,
    paths: dict[str, tuple[str, Path]] | None = None,
) -> LogSection:
    paths = paths or SYSTEM_LOG_PATHS
    if key not in paths:
        raise ValueError(f"Unsupported system log: {key}")
    label, path = paths[key]
    try:
        content = tail_text_file(path, lines=lines)
    except OSError as error:
        return LogSection(key=key, label=label, lines=lines, content="", error=str(error))
    return LogSection(key=key, label=label, lines=lines, content=content)


def read_system_logs(*, lines: int = DEFAULT_LOG_LINES) -> tuple[LogSection, ...]:
    return tuple(read_system_log(key, lines=lines) for key in SYSTEM_LOG_PATHS)


def read_pm2_log(name: str, *, runner: CommandRunner, lines: int = DEFAULT_LOG_LINES) -> LogSection:
    command = pm2_logs_command(name, lines=lines)
    try:
        result = runner.run(command.argv, stdin=None, timeout_seconds=command.timeout_seconds)
    except Exception as error:
        return LogSection(key=f"pm2:{name}", label=f"PM2 {name}", lines=lines, content="", error=str(error))
    if result.exit_code != 0:
        return LogSection(
            key=f"pm2:{name}",
            label=f"PM2 {name}",
            lines=lines,
            content=result.stdout,
            error=result.stderr or result.stdout or "pm2 logs failed",
        )
    return LogSection(key=f"pm2:{name}", label=f"PM2 {name}", lines=lines, content=result.stdout)


def tail_text_file(path: Path, *, lines: int) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - MAX_LOG_BYTES))
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def log_section_to_dict(section: LogSection) -> dict[str, object]:
    return {
        "key": section.key,
        "label": section.label,
        "lines": section.lines,
        "content": section.content,
        "error": section.error,
        "ok": section.ok,
    }
