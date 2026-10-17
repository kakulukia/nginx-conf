# Deployment

Diese Datei beschreibt den MVP-Betrieb der Verwaltungs-App auf einem
Ubuntu-/Debian-Server. Nginx bleibt Systemdienst. PM2 ist nur fuer verwaltete
Webapps vorgesehen, nicht fuer diese Admin-App.

## Benutzer und Pfade

```bash
sudo useradd --system --home /opt/nginx-config --shell /usr/sbin/nologin nginx-config
sudo install -d -o nginx-config -g nginx-config -m 0750 /opt/nginx-config
sudo install -d -o root -g root -m 0755 /opt/nginx-conf
```

Erwartete Pfade:

- App-Checkout oder Release: `/opt/nginx-conf`
- Virtuelle Umgebung: `/opt/nginx-conf/.venv`
- SQLite und Setup-Token: `/opt/nginx-config`
- Helper-Installation: `/usr/local/lib/nginx-conf/nginx-conf-helper`

Der Systemuser `nginx-config` bekommt keine breiten Schreibrechte auf
`/opt/www`.

## Helper installieren

```bash
sudo install -d -o root -g root -m 0755 /usr/local/lib/nginx-conf
sudo install -o root -g root -m 0755 scripts/nginx-conf-helper /usr/local/lib/nginx-conf/nginx-conf-helper
```

Der Helper akzeptiert feste Subcommands. Variable Daten laufen als JSON ueber
stdin. Es gibt keinen dauerhaft laufenden Helper-Daemon.

## systemd-Unit installieren

```bash
sudo install -o root -g root -m 0644 deploy/nginx-conf.service /etc/systemd/system/nginx-conf.service
sudo systemctl daemon-reload
sudo systemctl enable --now nginx-conf.service
```

Die Unit setzt `User=nginx-config` und `Group=nginx-config`, bindet die App an
`127.0.0.1:8080`, schreibt nur nach `/opt/nginx-config` und behandelt
`/opt/www`, `/etc/nginx` und `/etc/letsencrypt` read-only.

## sudoers installieren

```bash
sudo install -o root -g root -m 0440 deploy/nginx-conf.sudoers /etc/sudoers.d/nginx-conf
sudo visudo -cf /etc/sudoers.d/nginx-conf
```

Der Ausschnitt erlaubt dem App-User nur:

- `nginx -t`
- `systemctl reload nginx`
- `certbot renew --dry-run --non-interactive`
- den root-owned Helper mit den festen Subcommands `write-config`,
  `reset-config-to-head`, `commit-config`, `activate-config` und
  `deactivate-config`
- den root-owned Helper mit `clone-project` fuer GitHub-Clone nach `/opt/www`

Keine freie Shell und keine breiten Wildcards eintragen.

Variable Certbot-Aktionen wie Zertifikatsausstellung mit projektspezifischen
Domains brauchen eigene enge Wrapper oder Helper-Subcommands, bevor sie in
sudoers freigegeben werden.

## Betrieb

```bash
sudo systemctl status nginx-conf.service
sudo journalctl -u nginx-conf.service -f
sudo systemctl restart nginx-conf.service
sudo systemctl stop nginx-conf.service
```

Beim ersten Start erzeugt die App im Setup-Modus ein serverseitiges
`/opt/nginx-config/setup-token`. Das Token wird nach erfolgreichem Setup
geloescht.
