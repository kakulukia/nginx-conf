from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_default_vhost_rejects_unknown_names_on_both_ip_versions() -> None:
    config = (ROOT / "deploy/nginx-default-deny.conf").read_text(encoding="utf-8")

    for listener in [
        "80 default_server",
        "[::]:80 default_server",
        "443 ssl default_server",
        "[::]:443 ssl default_server",
    ]:
        assert f"listen {listener};" in config
    assert "ssl_reject_handshake on;" in config
    assert config.count("return 444;") == 2
    assert "proxy_pass" not in config


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


def test_nginx_test_has_writable_pid_file_after_nginx_start() -> None:
    service = (ROOT / "deploy/nginx-conf.service").read_text(encoding="utf-8")
    directives = dict(line.split("=", 1) for line in service.splitlines() if "=" in line)

    assert "nginx.service" in directives["After"].split()
    assert "nginx.service" in directives["Wants"].split()
    assert "nginx.service" in directives["PartOf"].split()
    writable_paths = directives["ReadWritePaths"].split()
    assert "/run/nginx.pid" in writable_paths
    assert "/run" not in writable_paths


def test_systemd_unit_keeps_shared_tmp_writable_for_certbot_and_sockets() -> None:
    service = (ROOT / "deploy/nginx-conf.service").read_text(encoding="utf-8")
    directives = dict(line.split("=", 1) for line in service.splitlines() if "=" in line)

    assert directives["PrivateTmp"] == "false"
    assert "/tmp" in directives["ReadWritePaths"].split()


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
