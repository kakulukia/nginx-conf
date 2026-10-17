from pathlib import Path

from nginx_conf.certbot_renewal import (
    certbot_renewal_status_to_dict,
    inspect_certbot_renewal,
)


def test_inspect_certbot_renewal_detects_enabled_systemd_timer(tmp_path: Path) -> None:
    unit_dir = tmp_path / "lib/systemd/system"
    wants_dir = tmp_path / "etc/systemd/system/timers.target.wants"
    unit_dir.mkdir(parents=True)
    wants_dir.mkdir(parents=True)
    timer = unit_dir / "certbot.timer"
    timer.write_text("[Timer]\nOnCalendar=*-*-* 00,12:00:00\n", encoding="utf-8")
    (wants_dir / "certbot.timer").symlink_to(timer)

    status = inspect_certbot_renewal(
        systemd_unit_dirs=(unit_dir,),
        systemd_timer_wants_dir=wants_dir,
        cron_files=(),
    )

    assert status.auto_renewal_configured is True
    assert status.summary == "gefunden"
    assert status.mechanisms[0].kind == "systemd_timer"
    assert status.mechanisms[0].status == "enabled"


def test_inspect_certbot_renewal_reports_installed_but_not_enabled_timer(tmp_path: Path) -> None:
    unit_dir = tmp_path / "lib/systemd/system"
    wants_dir = tmp_path / "etc/systemd/system/timers.target.wants"
    unit_dir.mkdir(parents=True)
    wants_dir.mkdir(parents=True)
    (unit_dir / "certbot.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")

    status = inspect_certbot_renewal(
        systemd_unit_dirs=(unit_dir,),
        systemd_timer_wants_dir=wants_dir,
        cron_files=(),
    )

    assert status.auto_renewal_configured is False
    assert status.summary == "Timer installiert, aber nicht als enabled erkannt"
    assert status.mechanisms[0].status == "installed"


def test_inspect_certbot_renewal_detects_cron_renew_job(tmp_path: Path) -> None:
    cron_file = tmp_path / "etc/cron.d/certbot"
    cron_file.parent.mkdir(parents=True)
    cron_file.write_text(
        "# ignored\n0 */12 * * * root test -x /usr/bin/certbot && certbot -q renew\n",
        encoding="utf-8",
    )

    status = inspect_certbot_renewal(
        systemd_unit_dirs=(),
        systemd_timer_wants_dir=tmp_path / "missing",
        cron_files=(cron_file,),
    )

    payload = certbot_renewal_status_to_dict(status)

    assert payload["auto_renewal_configured"] is True
    assert payload["mechanisms"][0]["kind"] == "cron"
    assert payload["mechanisms"][0]["status"] == "configured"
