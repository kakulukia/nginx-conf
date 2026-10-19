# Deployment

Erstes Ziel ist pepperz (`81.169.200.21`, SSH-Nutzer `andy`, Ubuntu 26.04).
Die Admin-Domain ist `config.pepperz.de`, das erste Projekt wird unter
`va.pepperz.de` vorbereitet. Eine Uebernahme vorhandener Daten ist nicht
vereinbart. Die Installation braucht eine gesonderte Deployment-Freigabe.
Nginx und die Verwaltungs-App laufen als Systemdienste, Webapps unter PM2.

## Pakete und Programmcode

Benoetigt werden Git, Nginx, sudo, Certbot samt Nginx-Plugin, Python ab 3.15,
uv und Node.js samt npm. Die Admin-App verwendet Python 3.15. Das bisher
auf pepperz vorhandene System-Python 3.14.4 reicht dafür nicht. Einen passenden
Interpreter als root unter `/opt/python` installieren und dieses Verzeichnis
auch beim `uv sync` angeben. Vorspiel-Abrechnung benötigt mindestens Python
3.14.6; dessen eigene Laufzeit und Abhängigkeiten separat prüfen.
Die Installation samt Elternverzeichnissen muss fuer den App-Nutzer lesbar
und ausfuehrbar sein.
uv wird aus der offiziellen Distribution nach `/usr/local/bin` installiert.

```bash
sudo apt update
sudo apt install git nginx sudo certbot python3-certbot-nginx python3-venv nodejs npm
sudo npm install -g pm2 sass
```

Die App erwartet PM2 unter `/usr/bin/pm2`. Falls npm es unter
`/usr/local/bin/pm2` installiert, dort einen root-eigenen Symlink anlegen,
ohne eine bestehende Datei zu ueberschreiben. Node und Sass muessen im
Service-PATH liegen, nicht in persoenlichen fnm-/nvm-Verzeichnissen.

Den freigegebenen Git-Stand nach `/opt/nginx-conf` uebertragen. Code,
virtuelle Umgebung, Helper und Elternverzeichnisse bleiben root-owned und
fuer `nginx-config` nicht schreibbar. Keine lokale Datenbank uebertragen.
Private Repositories brauchen einen separat eingerichteten Deploy-Key.

```bash
cd /opt/nginx-conf
sudo env UV_PYTHON_INSTALL_DIR=/opt/python /usr/local/bin/uv python install 3.15
sudo env UV_PYTHON_INSTALL_DIR=/opt/python /usr/local/bin/uv sync --locked --no-dev --python 3.15
sudo chown -R root:root /opt/nginx-conf
sudo chmod -R go-w /opt/nginx-conf
sudo useradd --system --home /opt/nginx-config --shell /usr/sbin/nologin nginx-config
sudo install -d -o nginx-config -g nginx-config -m 0750 /opt/nginx-config
sudo install -d -o root -g root -m 0755 /opt/www /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt /var/log/nginx
sudo install -d -o root -g root -m 0711 /run/sudo
```

Der App-Systemuser bekommt keine breiten Schreibrechte auf `/opt/www`.
Das Virtualenv muss einen fuer ihn ausfuehrbaren Interpreter verwenden.
Ein Python unter `/root` oder einem Home-Verzeichnis passt wegen
`ProtectHome=true` nicht zum Service. Den root-eigenen Interpreter unter
`/opt/python` verwenden und dessen Les- und Ausführungsrechte prüfen.

## Helper und sudoers

```bash
sudo install -d -o root -g root -m 0755 /usr/local/lib/nginx-conf
sudo install -o root -g root -m 0755 scripts/nginx-conf-helper /usr/local/lib/nginx-conf/nginx-conf-helper
sudo install -o root -g root -m 0440 deploy/nginx-conf.sudoers /etc/sudoers.d/nginx-conf
sudo visudo -cf /etc/sudoers.d/nginx-conf
```

Der kopierte Helper nutzt fest `/opt/nginx-conf/.venv/bin/python -I` und
`/opt/nginx-conf/src`. Sein Importpfad haengt nicht vom Installationsort ab;
Python-Umgebungsvariablen werden ignoriert.

Der Runner verwendet `sudo -n` nur fuer feste Nginx-Kommandos, Renewal-Dry-Run
und erlaubte Helper-Subcommands. Variable Daten gehen als JSON ueber stdin.
`certbot-issue` validiert die laufende `action_id`, Projektauswahl, Config-Pfade
und exakt eine aktive `server_name`-Gruppe, bevor es das feste Certbot-Template
ausfuehrt. Keine Shell oder sudoers-Wildcards freigeben.
PM2 und Django laufen ohne sudo als App-Nutzer.

## Netzwerk und HTTPS

DNS fuer `config.pepperz.de` muss auf pepperz zeigen. Auch vorhandene
AAAA-Records muessen stimmen. Beide Domains muessen auf `81.169.200.21`
zeigen. Port 8080 bleibt ausschliesslich an Loopback.

Zuerst die Admin-Quell-IPs nach `docs/admin-allowlist.md` eintragen.
`deploy/nginx-conf.nginx` root-owned nach
`/etc/nginx/sites-available/nginx-conf` kopieren und von `sites-enabled`
verlinken. Das ist eine initiale HTTP-Config. Erst nach erfolgreicher
Zertifikatsausstellung ueber den Domainnamen anmelden.

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx --non-interactive --agree-tos --email andy@freilandkiwis.de --no-eff-email --keep-until-expiring -d config.pepperz.de --redirect
```

Danach HTTPS, Redirect und die IP-Allowlist pruefen. Die Allowlist muss auch
im HTTPS-vhost enthalten bleiben. Das ist ein manueller Bootstrap ausserhalb
der noch nicht gestarteten App.

## PM2 und systemd

Alle App-Aufrufe und der PM2-Startdienst verwenden denselben Nutzer und
`PM2_HOME=/opt/nginx-config/.pm2`. PM2 wird vor der Admin-App als eigener
Startdienst eingerichtet, damit die Webapps nicht von deren Lebensdauer
oder Mount-Namespace abhaengen.

```bash
sudo -u nginx-config env HOME=/opt/nginx-config PM2_HOME=/opt/nginx-config/.pm2 /usr/bin/pm2 save --force
sudo env PATH=/usr/local/bin:/usr/bin:/bin PM2_HOME=/opt/nginx-config/.pm2 /usr/bin/pm2 startup systemd -u nginx-config --hp /opt/nginx-config
sudo -u nginx-config env HOME=/opt/nginx-config PM2_HOME=/opt/nginx-config/.pm2 /usr/bin/pm2 kill
sudo systemctl enable --now pm2-nginx-config
sudo install -o root -g root -m 0644 deploy/nginx-conf.service /etc/systemd/system/nginx-conf.service
sudo systemd-analyze verify /etc/systemd/system/nginx-conf.service
sudo systemctl daemon-reload
sudo systemctl enable --now nginx-conf.service
```

Den erzeugten PM2-Dienst auf denselben Datenpfad pruefen. Erfolgreiche
PM2-Schreibaktionen aus der App speichern die Prozessliste mit
`pm2 save --force`. Ein Fehler beim Speichern wird gemeldet.
Bei manuellen PM2-Aenderungen ebenfalls speichern.

Die Admin-Unit behaelt `ProtectSystem=strict` und `ProtectHome=true`.
Nur benoetigte Betriebsverzeichnisse haben beschreibbare Mounts. Unix-Rechte
verhindern weiterhin direkte Config-/Code-Aenderungen durch den App-Nutzer.
`NoNewPrivileges=false` ermoeglicht die separat durch sudoers begrenzte
Rechteerhoehung. `PrivateTmp=false` macht Gunicorn-Sockets unter `/tmp`
fuer den Host-Nginx sichtbar.

## Passwort und TOTP

Die Anwendung hat genau einen Zugang ohne Nutzernamen. Ein Web-Setup und
Login-Tokens entfallen. Bei der Installation einmalig im Terminal ausführen:

```bash
sudo -u nginx-config /opt/nginx-conf/.venv/bin/python -I /opt/nginx-conf/scripts/nginx-conf-auth
```

Der Befehl fragt ausschließlich das Passwort zweimal verdeckt ab und speichert
es als Argon2id-Hash. Das Passwort hat mindestens 12 Zeichen. Keine Passwörter
oder TOTP-Geheimnisse als Kommandozeilenargumente übergeben.

Anschließend `https://config.pepperz.de/login` öffnen und das Passwort eingeben.
Beim ersten Login erscheint der QR-Code für die Authenticator-App. Scannen
oder den Schlüssel manuell eintragen und den aktuellen Code bestätigen.
Erst danach ist die App zugänglich. Die Einrichtung läuft nach zehn Minuten
ab; dann erneut mit dem Passwort beginnen. QR-Code und Schlüssel werden
vollständig auf dem Server erzeugt und nicht an einen QR-Dienst übertragen.
Bei späteren Anmeldungen Passwort und aktuellen TOTP-Code eingeben.

Die SQLite-Datei enthält den Passwort-Hash und das TOTP-Geheimnis und erhält
Modus `0600`. Auch Backups dieser Datei entsprechend schützen. Die Dateien
bleiben auf dem Server. Die Systemzeit muss synchronisiert sein.

Fünf Anmeldeversuche ohne erfolgreichen Login sperren weitere Versuche für
60 Sekunden. Die Sperre bleibt bei einem Prozessneustart erhalten. Ein
TOTP-Code ist nur einmal nutzbar. HTTPS-Sessions haben Secure-, HttpOnly- und
SameSite-Lax-Cookies und laufen nach 30 Tagen ab. Abmelden invalidiert die Session.

Bei verlorenem Passwort oder Authenticator den gleichen Installationsbefehl
mit `--replace` ausführen. Das setzt ein neues Passwort, entfernt den bisherigen Authenticator und meldet
alle bestehenden Sessions ab. Auch laufende Einrichtungen werden ungültig.
Beim nächsten Login den Authenticator erneut im Browser einrichten.
Im Browser gibt es keinen Weg zum Zurücksetzen.

Beim Update vom bisherigen Modell werden alte Nutzer, Token-Sessions und
die Setup-Token-Datei entfernt. Projekte, Einstellungen, Audit-Daten und
bereits gespeicherte Server-IPs bleiben erhalten. Neue Server-IPs sollen
später über einen eigenen Dialog gepflegt werden. Ohne konfigurierte IPs
bleiben DNS-Preflight und davon abhängige Zertifikatsaktionen blockiert.

## Erstes Projekt: vorspiel-abrechnung

Das Repository wird root-owned unter `/opt/www/vorspiel-abrechnung`
bereitgestellt. Initiales Virtualenv, Secrets, Datenbank, Domain und
E-Mail-Konfiguration sind projektbezogene Handarbeit. Kein Live-Datenimport
und kein E-Mail-Versand ohne ausdruecklich vereinbarten Umfang.

Code und `.git` bleiben fuer `nginx-config` schreibgeschuetzt. Veraenderliche
Daten gehoeren dem App-Nutzer und liegen unter
`/opt/nginx-config/projects/vorspiel-abrechnung`. `db.sqlite3`, `media`,
`private` und `static` koennen auf diese Datenpfade verweisen.
SQLite-WAL-Dateien muessen ebenfalls dort entstehen; nicht den gesamten
Codeordner beschreibbar machen.

Beim App-Nutzer genau den Projektpfad als Git-`safe.directory` hinterlegen,
keine Wildcard. Im Projekt-Git eine Commit-Identitaet setzen, damit
Config-Commits durch den Helper funktionieren. Private GitHub-Clones brauchen
root-lesbare SSH-Zugangsdaten ausserhalb der gesperrten Home-Verzeichnisse,
etwa unter `/etc/nginx-conf`.

`settings/deployment/project.nginx` enthaelt noch Domain-/SSL-Platzhalter
und darf so nicht aktiviert werden. Zuerst eine gueltige HTTP-Config fuer
`va.pepperz.de` erstellen, Django `ALLOWED_HOSTS` und `CSRF_TRUSTED_ORIGINS`
angleichen, dann SSL einrichten. Als PM2-Startscript explizit
`settings/deployment/project.sh` auswaehlen; es gibt auch `worker.sh`.

Django-Migrationen, `collectstatic` und `compress` laufen als App-Nutzer.
Sass muss im PATH liegen. Aufgaben-Worker und Cronjobs werden separat nach
der Projektanleitung eingerichtet. Der MVP verwaltet nur den ausgewaehlten
Webprozess; manuell gestartete Worker bleiben als unverwaltet sichtbar.

## Betriebspruefung

```bash
sudo systemctl status nginx-conf.service pm2-nginx-config nginx
sudo journalctl -u nginx-conf.service -n 100 --no-pager
curl --fail http://127.0.0.1:8080/health
sudo nginx -t
sudo certbot renew --dry-run --non-interactive
```

Auf pepperz Einrichtung, Passwort-/TOTP-Anmeldung, Config-Schreibzugriff, Nginx-Test/Reload,
Prozessstart und Wiederanlauf nach Neustart pruefen. Ein lokaler Containercheck
ersetzt diese Abnahme auf dem Zielserver nicht.
