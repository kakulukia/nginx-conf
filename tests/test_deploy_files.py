from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_systemd_unit_runs_as_dedicated_unprivileged_user() -> None:
    service = (ROOT / "deploy/nginx-conf.service").read_text(encoding="utf-8")

    assert "User=nginx-config" in service
    assert "Group=nginx-config" in service
    assert "User=root" not in service
    assert "Group=root" not in service
    assert "ReadWritePaths=/opt/nginx-config /opt/www /etc/nginx /etc/letsencrypt" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=true" in service
    assert "NoNewPrivileges=false" in service
    assert "PrivateTmp=false" in service
    assert "--app-dir /opt/nginx-conf/src" in service
    assert "PM2_HOME=/opt/nginx-config/.pm2" in service


def test_sudoers_file_uses_fixed_commands_without_wildcards() -> None:
    sudoers = (ROOT / "deploy/nginx-conf.sudoers").read_text(encoding="utf-8")

    assert "*" not in sudoers
    assert "/usr/sbin/nginx -t" in sudoers
    assert "/bin/systemctl reload nginx" in sudoers
    assert "/usr/bin/certbot renew --dry-run --non-interactive" in sudoers
    assert "/usr/local/lib/nginx-conf/nginx-conf-helper activate-config" in sudoers
    assert "/usr/local/lib/nginx-conf/nginx-conf-helper deactivate-config" in sudoers
    assert "/usr/local/lib/nginx-conf/nginx-conf-helper write-config" in sudoers
    assert "/usr/local/lib/nginx-conf/nginx-conf-helper reset-config-to-head" in sudoers
    assert "/usr/local/lib/nginx-conf/nginx-conf-helper commit-config" in sudoers
    assert "/usr/local/lib/nginx-conf/nginx-conf-helper clone-project" in sudoers


def test_admin_allowlist_docs_cover_ssh_fallback_backup_test_reload_and_audit() -> None:
    docs = (ROOT / "docs/admin-allowlist.md").read_text(encoding="utf-8")

    assert "/etc/nginx/snippets/config-admin-allowlist.conf" in docs
    assert "python -m nginx_conf.admin_allowlist" in docs
    assert "sudo cp -a" in docs
    assert "sudo nginx -t" in docs
    assert "sudo systemctl reload nginx" in docs
    assert "logger -t nginx-conf-admin-allowlist" in docs
