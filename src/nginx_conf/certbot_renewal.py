from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SYSTEMD_UNIT_DIRS = (
    Path("/etc/systemd/system"),
    Path("/lib/systemd/system"),
    Path("/usr/lib/systemd/system"),
)
SYSTEMD_TIMER_WANTS_DIR = Path("/etc/systemd/system/timers.target.wants")
CERTBOT_TIMER_NAMES = ("certbot.timer", "snap.certbot.renew.timer")
CRON_FILES = (
    Path("/etc/cron.d/certbot"),
    Path("/etc/cron.daily/certbot"),
    Path("/etc/crontab"),
    Path("/var/spool/cron/crontabs/root"),
)


@dataclass(frozen=True, slots=True)
class CertbotRenewalMechanism:
    kind: str
    name: str
    path: Path
    status: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CertbotRenewalStatus:
    mechanisms: tuple[CertbotRenewalMechanism, ...]
    warnings: tuple[str, ...] = ()

    @property
    def auto_renewal_configured(self) -> bool:
        return any(mechanism.status in {"enabled", "configured"} for mechanism in self.mechanisms)

    @property
    def summary(self) -> str:
        if self.auto_renewal_configured:
            return "gefunden"
        if self.mechanisms:
            return "Timer installiert, aber nicht als enabled erkannt"
        return "nicht gefunden"


def inspect_certbot_renewal(
    *,
    systemd_unit_dirs: tuple[Path, ...] = SYSTEMD_UNIT_DIRS,
    systemd_timer_wants_dir: Path = SYSTEMD_TIMER_WANTS_DIR,
    cron_files: tuple[Path, ...] = CRON_FILES,
) -> CertbotRenewalStatus:
    mechanisms: list[CertbotRenewalMechanism] = []
    warnings: list[str] = []

    for timer_name in CERTBOT_TIMER_NAMES:
        enabled_path = systemd_timer_wants_dir / timer_name
        installed_path = _first_existing_path(unit_dir / timer_name for unit_dir in systemd_unit_dirs)
        if enabled_path.exists():
            mechanisms.append(
                CertbotRenewalMechanism(
                    kind="systemd_timer",
                    name=timer_name,
                    path=enabled_path,
                    status="enabled",
                    detail=f"Unit: {installed_path}" if installed_path else None,
                )
            )
        elif installed_path is not None:
            mechanisms.append(
                CertbotRenewalMechanism(
                    kind="systemd_timer",
                    name=timer_name,
                    path=installed_path,
                    status="installed",
                )
            )

    for cron_file in cron_files:
        if not cron_file.exists():
            continue
        try:
            lines = cron_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            warnings.append(f"{cron_file}: {error}")
            continue
        matching_lines = _certbot_renew_lines(lines)
        if matching_lines:
            mechanisms.append(
                CertbotRenewalMechanism(
                    kind="cron",
                    name=cron_file.name,
                    path=cron_file,
                    status="configured",
                    detail=f"{len(matching_lines)} certbot-renew Eintrag",
                )
            )

    return CertbotRenewalStatus(mechanisms=tuple(mechanisms), warnings=tuple(warnings))


def certbot_renewal_status_to_dict(status: CertbotRenewalStatus) -> dict[str, object]:
    return {
        "auto_renewal_configured": status.auto_renewal_configured,
        "summary": status.summary,
        "mechanisms": [
            {
                "kind": mechanism.kind,
                "name": mechanism.name,
                "path": str(mechanism.path),
                "status": mechanism.status,
                "detail": mechanism.detail,
            }
            for mechanism in status.mechanisms
        ],
        "warnings": list(status.warnings),
    }


def _first_existing_path(paths) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _certbot_renew_lines(lines: list[str]) -> list[str]:
    matches: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "certbot" in stripped and "renew" in stripped:
            matches.append(stripped)
    return matches
