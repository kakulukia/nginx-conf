# nginx-conf

Weboberfläche zum Verwalten von Nginx-Konfigurationen und Webapps auf einem
Ubuntu-/Debian-Server. Nginx bleibt ein Systemdienst, verwaltete Webapps laufen
unter PM2. Die Oberfläche ist für Desktop im Dunkelmodus gestaltet.

Die Installation auf pepperz ist unter [config.pepperz.de](https://config.pepperz.de)
erreichbar. Das erste vorgesehene Projekt ist Vorspiel-Abrechnung unter
`va.pepperz.de`.

## Installation

Benötigt werden Python ab 3.15, uv, Nginx, Certbot, Node.js, npm, PM2 und Sass.
Die vollständigen Schritte für Pakete, Dateirechte, Dienste und HTTPS stehen
in der [Installationsanleitung](docs/deployment.md).

Programmcode und virtuelle Umgebung liegen unter `/opt/nginx-conf` und gehören
root. Die App läuft als `nginx-config`; ihre Datenbank liegt unter
`/opt/nginx-config/nginx-conf.sqlite3`. Port 8080 ist nur lokal erreichbar,
Nginx übernimmt den öffentlichen HTTPS-Zugang.

## Installationspasswort und erster Login

Bei einer neuen Installation das vorläufige Passwort als Klartext in genau
dieser Datei hinterlegen:

```text
/opt/nginx-config/installation-password
```

Die Datei gehört `nginx-config`, hat Modus `0600` und enthält genau eine
UTF-8-Zeile mit 12 bis 1024 Zeichen. Ein abschließender Zeilenumbruch ist erlaubt.
Datei anlegen, mit dem Editor befüllen und anschließend die App starten:

```bash
sudo install -o nginx-config -g nginx-config -m 0600 /dev/null /opt/nginx-config/installation-password
sudoedit /opt/nginx-config/installation-password
sudo systemctl restart nginx-conf.service
```

Beim Start liest die App die Datei einmalig ein, speichert nur den Argon2id-Hash
und löscht anschließend die Klartextdatei. Eine fehlende Datei lässt den Zugang
gesperrt. Bei ungültigem Inhalt oder falschen Rechten startet die App nicht;
die Datei bleibt zur Korrektur erhalten. Symlinks sind nicht erlaubt.

1. [Anmelden](https://config.pepperz.de/login) öffnen und das Installationspasswort eingeben.
2. Im Dialog „Setze dein Passwort“ ein eigenes Passwort zweimal eingeben.
   Es muss mindestens 12 Zeichen lang sein und vom Installationspasswort abweichen.
3. Den QR-Code in der Authenticator-App scannen oder den Schlüssel manuell übernehmen.
4. Den aktuellen sechsstelligen Code eingeben und die Einrichtung bestätigen.

Erst danach ist die App zugänglich. Die automatische Übernahme des Einmalcodes
aus 1Password wurde auf pepperz erfolgreich geprüft.

Das Installationspasswort wird durch den Passwortwechsel ungültig, nicht durch
einen festen Zeitablauf. Für Passwortwechsel und Authenticator-Einrichtung bleiben
nach dem ersten Login insgesamt zehn Minuten. Danach erneut mit dem aktuell
gültigen Passwort anmelden. Spätere Logins verlangen Passwort und TOTP-Code.

Ein bereits eingerichteter Zugang wird durch eine neue Passwortdatei niemals
ersetzt. In diesem Fall wird die Datei ignoriert und nicht gelöscht.
Eine versehentlich erneut angelegte Datei selbst entfernen.
Passwörter und Authenticator-Schlüssel gehören nicht ins Repository.

## Zugang zurücksetzen

Bei Verlust des Passworts oder Authenticators in einer SSH-Sitzung ausführen:

```bash
sudo -u nginx-config /opt/nginx-conf/.venv/bin/python -I /opt/nginx-conf/scripts/nginx-conf-auth --replace
```

Der Befehl fragt ein neues Installationspasswort zweimal verdeckt ab. Er meldet
alle Sitzungen ab und setzt auch die Authenticator-Zuordnung zurück. Danach den
ersten Login erneut durchlaufen. Ohne `--replace` bleibt ein bestehender Zugang
geschützt; bei einer neuen Installation ist dieser interaktive Weg ebenfalls möglich.

## Entwicklung und weitere Dokumentation

```bash
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check src tests
```

- [Technikreferenz](docs/technikreferenz.md): Funktionen und Sicherheitsgrenzen.
- [Styleguide](docs/styleguide.md): Gestaltung und Bedienelemente.
- [Installationsanleitung](docs/deployment.md): Servereinrichtung und Betriebsprüfung.
- [Optionale IP-Allowlist](docs/admin-allowlist.md): spätere Zugangsbeschränkung.

Backups und Wiederherstellung sind auf pepperz noch einzurichten. Die Datenbank
enthält neben dem Passwort-Hash auch das TOTP-Geheimnis und muss entsprechend
geschützt gesichert werden.
