# Deployment

Erstes Ziel ist pepperz (`81.169.200.21`, SSH-Nutzer `andy`, Ubuntu 26.04).
Die Admin-Domain ist `config.pepperz.de`, das erste Projekt wird unter
`va.pepperz.de` vorbereitet. Eine Uebernahme vorhandener Daten ist nicht
vereinbart. Die Installation braucht eine gesonderte Deployment-Freigabe.
Nginx und die Verwaltungs-App laufen als Systemdienste, Webapps unter PM2.

## Geprüfter Serverstand vom 19.10.2026

Der lesende Check über `andy@config.pepperz.de` bestätigt Ubuntu 26.04.1 LTS
auf x86_64. Der interne Hostname ist noch `stage.pepperz.de`. Der bestätigte
ED25519-Fingerprint lautet
`SHA256:XI2RzZ0HFnBYBES5WXXICA3B7xISqT3vngcpT2wWJG4`.
Passwortloses sudo funktioniert.

Git und sudo sind vorhanden, das System-Python ist 3.14.4. Nginx, Certbot,
Node/npm, PM2, Sass, uv und Python 3.15 fehlen. Der Nutzer `nginx-config`,
App-Code, Datenverzeichnis, Helper und App-Dienste sind noch nicht eingerichtet.
`/opt/www` ist leer. Auf den Webports 80, 443 und 8080 lauscht kein Dienst.
UFW ist inaktiv, das nftables-Regelwerk ist leer. Eine zusätzliche
Provider-Firewall wurde nicht geprüft.

Die Systemzeit ist synchronisiert. Es stehen etwa 3,1 GiB RAM und 288 GiB
Plattenplatz zur Verfügung. Beide Domains haben den passenden A-Record und
keinen AAAA-Record. Die VM hat zusätzlich die IPv6-Adresse `2a01:239:42e:ea00::1`.
Anwendungsspezifische Sicherung und Wiederherstellung sind noch festzulegen;
etwaige Sicherungen beim Provider wurden nicht geprüft.

## Pakete und Programmcode

Benoetigt werden Git, Nginx, sudo, Certbot samt Nginx-Plugin, Python ab 3.15,
uv und Node.js samt npm. Die Admin-App verwendet Python 3.15. Das bisher
auf pepperz vorhandene System-Python 3.14.4 reicht dafür nicht. Einen passenden
Interpreter als root unter `/opt/python` installieren und dieses Verzeichnis
auch beim `uv sync` angeben. Vorspiel-Abrechnung benötigt mindestens Python
3.14.6; dessen eigene Laufzeit und Abhängigkeiten separat prüfen.
Die Installation samt Elternverzeichnissen muss fuer den App-Nutzer lesbar
und ausfuehrbar sein.
uv wird nach den [offiziellen Installer-Optionen](https://docs.astral.sh/uv/reference/installer/)
nach `/usr/local/bin` installiert, ohne Shellprofile zu verändern.

Die Admin-App verwendet Uvicorn ohne optionale Extras. YAML-Logging,
WebSockets und Dateiüberwachung werden im Betrieb nicht benötigt.
Für die Produktionsabhängigkeiten sind keine zusätzlichen Build-Werkzeuge
oder LibYAML vorgesehen. Der Linux-Trockenlauf für Python 3.15 auf x86_64
mit `--no-build` kommt ausschließlich mit fertigen Wheels aus.
PyYAML bleibt nur über pre-commit in den Entwicklungsabhängigkeiten;
der dokumentierte Produktionsaufruf installiert es nicht.
Der echte `uv sync` und anschließend der Import der App werden vor dem
Dienststart auf pepperz geprüft.

```bash
sudo apt update
sudo apt install git nginx sudo certbot python3-certbot-nginx python3-venv nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_UNMANAGED_INSTALL=/usr/local/bin sh
sudo npm install -g pm2 sass
```

Die App erwartet PM2 unter `/usr/bin/pm2`. Falls npm es unter
`/usr/local/bin/pm2` installiert, dort einen root-eigenen Symlink anlegen,
ohne eine bestehende Datei zu ueberschreiben. Node und Sass muessen im
Service-PATH liegen, nicht in persoenlichen fnm-/nvm-Verzeichnissen.

Den freigegebenen Git-Stand nach `/opt/nginx-conf` uebertragen. Code,
virtuelle Umgebung, Helper und Elternverzeichnisse bleiben root-owned und
fuer `nginx-config` nicht schreibbar. Keine lokale Datenbank uebertragen.
Der Code kann als Archiv des freigegebenen lokalen Git-Commits per SSH
übertragen werden. Dafür braucht die Admin-App keinen GitHub-Zugang auf dem
Server. Nur beim direkten Clone eines privaten Repositories auf dem Server
ist ein separat eingerichteter Deploy-Key erforderlich.

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

Die erste Installation verwendet nach der Entscheidung vom 19.10.2026 keine
Admin-IP-Allowlist. Die Anmeldung ist öffentlich erreichbar und durch Passwort,
TOTP und die Anmeldebegrenzung geschützt. Die Nginx-Vorlage benötigt deshalb
keine zusätzliche Allowlist-Datei. Eine spätere Begrenzung auf feste Quell-IPs
ist optional unter `docs/admin-allowlist.md` beschrieben.

`deploy/nginx-conf.nginx` root-owned nach
`/etc/nginx/sites-available/nginx-conf` kopieren und von `sites-enabled`
verlinken. Das ist eine initiale HTTP-Config. Erst nach erfolgreicher
Zertifikatsausstellung ueber den Domainnamen anmelden.

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx --non-interactive --agree-tos --email andy@freilandkiwis.de --no-eff-email --keep-until-expiring -d config.pepperz.de --redirect
```

Danach HTTPS und Redirect prüfen. Falls später eine IP-Allowlist aktiviert
wird, muss sie auch im HTTPS-vhost enthalten bleiben. Das ist ein manueller
Bootstrap außerhalb der noch nicht gestarteten App.

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

## Server-Adressen für DNS-Prüfungen

Die App benötigt ihre öffentlichen Server-Adressen als technische Einstellung
für DNS-Preflight und die davon abhängigen Zertifikatsaktionen. Das ist
unabhängig von einer Admin-IP-Allowlist. Bis zum späteren Einstellungsdialog
werden die beim Servercheck bestätigten Adressen bei der Installation
einmalig hinterlegt:

```bash
sudo -u nginx-config /opt/nginx-conf/.venv/bin/python -I - <<'PY'
import sys
sys.path.insert(0, "/opt/nginx-conf/src")
from nginx_conf.store import AppDatabase

db = AppDatabase()
db.initialize()
db.upsert_server_ip("81.169.200.21", label="pepperz IPv4")
db.upsert_server_ip("2a01:239:42e:ea00::1", label="pepperz IPv6")
PY
```

Vor der Verwendung dieser Werte auf einem anderen Server erneut prüfen.

## Passwort und TOTP

Die Anwendung hat genau einen Zugang ohne Nutzernamen. Ein Web-Setup und
Login-Tokens entfallen. Bei der Installation ein vorläufiges Installationspasswort
festlegen. Dafür einmalig im Terminal ausführen:

```bash
sudo -u nginx-config /opt/nginx-conf/.venv/bin/python -I /opt/nginx-conf/scripts/nginx-conf-auth
```

Der Befehl fragt ausschließlich das Installationspasswort zweimal verdeckt ab und speichert
es als Argon2id-Hash. Das Passwort hat mindestens 12 Zeichen. Keine Passwörter
oder TOTP-Geheimnisse als Kommandozeilenargumente übergeben.

Anschließend `https://config.pepperz.de/login` öffnen und das Installationspasswort
eingeben. Im Dialog „Setze dein Passwort“ ein eigenes Passwort zweimal eingeben.
Es muss mindestens 12 Zeichen lang sein und sich vom Installationspasswort
unterscheiden. Die App ersetzt den bisherigen Hash durch den Argon2id-Hash
des neuen Passworts. Das Installationspasswort ist ab diesem Moment ungültig.
Es gibt keine separate Passwortdatei; die Datenbank liegt unter
`/opt/nginx-config/nginx-conf.sqlite3`.

Danach erscheint der QR-Code für die Authenticator-App. Scannen
oder den Schlüssel manuell eintragen und den aktuellen Code bestätigen.
Erst danach ist die App zugänglich. Die vorläufige Berechtigung für Passwortwechsel
und Authenticator-Einrichtung läuft insgesamt nach zehn Minuten ab. Dann erneut
mit dem aktuell gültigen Passwort beginnen. Ein bereits gesetztes eigenes Passwort
bleibt erhalten. QR-Code und Schlüssel werden
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
mit `--replace` ausführen. Das setzt ein neues Installationspasswort, entfernt den bisherigen Authenticator und meldet
alle bestehenden Sessions ab. Auch laufende Einrichtungen werden ungültig.
Beim nächsten Login ein eigenes Passwort setzen und den Authenticator erneut im Browser einrichten.
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
