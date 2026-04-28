# Nginx-Verwaltungstool

## Feature-Liste v0

- Nginx-Configs anzeigen.
- Nginx-Configs im Webfrontend editieren.
- `/opt/www` nach Nginx-Config-Dateien durchsuchen.
- Gefundene Configs als aktivierbare Einträge anbieten.
- Aktivierung über Symlink aus dem Projektordner nach `/etc/nginx/sites-enabled`.
- `nginx -t` ausführen und Ergebnis anzeigen.
- Nginx reloaden, bevorzugt `systemctl reload nginx` statt Restart.
- SSL-Zertifikate per Certbot ausstellen.
- Zertifikatsstatus anzeigen: Domains, Ablaufdatum, Pfade.
- Renewal prüfen/testen: `certbot renew --dry-run`.
- Automatische Erneuerung über vorhandenen Certbot Cronjob oder systemd Timer erkennen und validieren.
- Static-Assets-Ordner per Klick auswählen.
- Passenden `location`-Block automatisch in die Nginx-Config schreiben.
- Vor jeder Änderung Backup/Snapshot der Config anlegen.
- Änderung erst aktivieren, wenn `nginx -t` erfolgreich ist.
- App-Prozesse anzeigen und verwalten, initial über PM2.
- App-Prozesse anlegen, starten, stoppen, restarten, reloaden und löschen.
- Letzte Log-Zeilen von App-Prozessen anzeigen.

## Korrekturen und Grundsatzentscheidungen

- Projekt-Configs werden direkt aus `/opt/www` nach `/etc/nginx/sites-enabled` gesymlinkt.
- `/etc/nginx/sites-available` ist nicht der primäre Speicherort fuer Projekt-Configs.
- Reload statt Restart verwenden, solange möglich. Restart beendet aktive Verbindungen unnötig.
- Keinen eigenen Certbot-Cronjob erzwingen. Certbot bringt je nach Installation bereits Cron oder systemd Timer mit.
- Certbot darf im MVP die Nginx-Config mit dem Nginx-Plugin selbst ändern, aber nur mit Backup vorher und `nginx -t` danach.
- Keine freie Shell aus dem Webfrontend anbieten.
- SSH-Secret in der Web-App ist wahrscheinlich nicht sauber genug. Besser: Passkey/WebAuthn plus Zugriff nur über VPN, Tailscale oder localhost.
- Nginx selbst nicht über PM2 betreiben. Nginx bleibt ein Systemdienst und wird über `systemctl reload nginx` gesteuert.
- PM2 ist der initiale Prozessmanager für Webapps unter `/opt/www`.

## Zusätzlich einplanen

- Rollback pro Config.
- Audit-Log: wer hat was wann geändert.
- Rechtekonzept mit minimal erlaubten Befehlen.
- Domain-Erkennung aus `server_name`.
- Konflikterkennung: doppelte Domains, doppelte Ports, kaputte Symlinks.
- Dry-Run-Modus für Änderungen.
- Template/Snippet-System für wiederkehrende Config-Blöcke.
- Backup vor Certbot- und Nginx-Änderungen.
- Anzeige von Logs: Nginx error/access log, Certbot log.
- Optionaler zweiter App-Prozess-Provider für systemd.

## Technik-Vorschlag

- Backend: Python.
- Projekt- und Dependency-Verwaltung: `uv`.
- Webframework: FastAPI.
- Templates: Jinja2.
- Frontend-Interaktion: HTMX.
- Styling: Bulma.
- Editor: zunächst normales `<textarea>`, später optional CodeMirror.
- Betrieb der Verwaltungs-App: systemd.
- Betrieb von Nginx: systemd.
- Betrieb verwalteter Webapps: initial PM2.
- Speicherung: SQLite für Audit-Log, bekannte Projekte, UI-State und Backup-Metadaten.
- Rechte: eigener Systemuser plus enges `sudoers`.
- Erlaubte Systembefehle explizit whitelisten:
  - `nginx -t`
  - `systemctl reload nginx`
  - ausgewählte `certbot`-Kommandos
  - ausgewählte `pm2`-Kommandos
  - Symlink-/Dateioperationen nur auf erlaubten Pfaden
- Auth MVP: lokaler Setup-Token plus Session-Cookie.
- Auth Zielbild: Passkey/WebAuthn.
- Netzwerk: bevorzugt `config.mamasystems.de` mit strikter IP-Allowlist; Tailscale/VPN bleibt als Alternative zu testen.
- Passkey/WebAuthn ist zusätzliche Authentifizierung, aber kein Ersatz für Netzwerkbegrenzung und minimale Systemrechte.
- Ziel-Origin für die Verwaltungs-App: `config.mamasystems.de`.
- `config.mamasystems.de` kann auf einem separaten System laufen, bevor `mamasystems.de` und die gehosteten Anwendungen migriert werden.
- Die Verwaltungs-App soll den schrittweisen Serverumzug unterstützen, nicht davon abhängen, dass alle Domains bereits umgezogen sind.
- Das Tool ist fuer genau einen Nutzer ausgelegt.
- Zielsystem: Ubuntu/Debian mit Standard-Nginx-Struktur.
- Erwartete Nginx-Pfade:
  - `/etc/nginx/sites-available`
  - `/etc/nginx/sites-enabled`
- Projektpfad ist fest: `/opt/www`.
- Nginx-Config-Dateimuster in Projekten: `+.nginx`.
- Datenablage: `/opt/nginx-config/`.
- SQLite und Backups liegen unterhalb der Datenablage.
- Certbot-Verhalten: bevorzugt `certbot --nginx`, weil es fuer den MVP einfacher ist.
- Wenn Certbot zu unkontrollierbare Config-Aenderungen erzeugt, wechseln wir spaeter auf manuell verwaltete SSL-Bloecke.

## App-Prozessverwaltung

- Initialer Provider: PM2.
- Provider-Schnittstelle:
  - `list()`
  - `create(job)`
  - `start(id)`
  - `stop(id)`
  - `restart(id)`
  - `reload(id)`
  - `delete(id)`
  - `logs(id, lines=100)`
- Basisdaten pro Job:
  - Name.
  - Projektordner.
  - Startkommando.
  - Status.
  - Port, falls bekannt.
  - Restart-Zähler.
  - Letzte Log-Zeilen.
- Erlaubte PM2-Aktionen:
  - `pm2 jlist`
  - `pm2 start ... --name ... --cwd ...`
  - `pm2 restart <name>`
  - `pm2 reload <name>`
  - `pm2 stop <name>`
  - `pm2 delete <name>`
  - `pm2 logs <name> --lines 100 --nostream`
  - `pm2 save`
- Kein freies Shell-Kommando im UI.
- Projektordner müssen unter `/opt/www` liegen.
- Startkommando-/PM2-Templates werden spaeter entschieden.
- Bisher bringt jede App ihr eigenes Startscript mit.
- Ports nur aus erlaubtem Bereich, z.B. `3000-9999`.
- `reload` nur anbieten, wenn es für den Prozess sinnvoll ist. Sonst `restart` verwenden.

## Auth und Initial-Setup

- Initialer Nutzer wird im Setup-Modus angelegt.
- Setup-Modus ist nur aktiv, solange keine Nutzer in SQLite existieren.
- Setup-Zugriff nur über localhost, erlaubte IP oder Tailscale/VPN erlauben.
- Beim ersten Start erzeugt die App ein einmaliges Setup-Token auf dem Server.
- Das Setup-Token wird nur lokal ausgegeben, z.B. in der Konsole, im PM2-Log oder in einer root-lesbaren Datei.
- Nach erfolgreicher Passkey-Registrierung wird das Setup-Token invalidiert.
- Passkeys muessen auf der spaeter genutzten WebAuthn-Origin registriert werden.
- Ein lokal fuer `localhost` registrierter Passkey funktioniert nicht automatisch auf der Server-Domain.
- SQLite-Datenbank mit lokal registriertem `localhost`-Passkey auf den Server kopieren ist daher kein belastbarer Bootstrap-Weg.
- Empfohlener Bootstrap:
  - App auf dem Server starten.
  - Zugriff ueber die finale interne Admin-Adresse: `config.mamasystems.de`.
  - Setup-Token aus Server-Log holen.
  - Passkey im Browser fuer genau diese Adresse registrieren.
- `config.mamasystems.de` darf fuer die WebAuthn-RP-ID stabil bleiben, auch wenn `mamasystems.de` selbst erst spaeter migriert wird.
- Netzwerkbegrenzung fuer `config.mamasystems.de`:
  - bevorzugt oeffentliche DNS-Domain mit strikter IP-Allowlist.
  - Tailscale/VPN als Alternative evaluieren.
  - nicht nur durch Passkey schuetzen, wenn die App im oeffentlichen Internet steht.
- IP-Allowlist muss ausserhalb der Web-App einfach aktualisierbar sein, z.B. per SSH:
  - Allowlist-Datei: `/etc/nginx/snippets/config-admin-allowlist.conf`.
  - Nginx-Include fuer erlaubte Admin-IPs.
  - Helper-Kommando zum Setzen der aktuellen IP.
  - Danach `nginx -t` und `systemctl reload nginx`.
- IP-Allowlist-Aenderungen muessen mit Backup und Audit-Log erfasst werden.
- Fallback bei ausgesperrter IP:
  - per SSH auf den Server.
  - Allowlist-Datei aktualisieren.
  - Nginx testen und reloaden.
- Hosts-Datei-Trick ist nur eine Notloesung:
  - Domain muss exakt zur spaeteren WebAuthn RP-ID passen.
  - HTTPS-Zertifikat muss fuer den Browser gueltig sein.
  - Fehleranfaellig und fuer den normalen Setup-Pfad nicht noetig.

## MVP-Reihenfolge

1. Projekte und Configs unter `/opt/www` finden.
2. Configs anzeigen und editieren.
3. Vor Config-Änderungen Backups erstellen.
4. `nginx -t` ausführen und Ergebnis im UI anzeigen.
5. Configs per Symlink aus dem Projektordner nach `/etc/nginx/sites-enabled` aktivieren/deaktivieren.
6. Nginx nach erfolgreichem Test reloaden.
7. PM2-Prozesse anzeigen.
8. PM2-Prozesse anlegen, restarten, stoppen und löschen.
9. Letzte PM2-Log-Zeilen anzeigen.
10. Certbot-Zertifikate ausstellen.
11. Certbot-Renewal und Zertifikatsstatus anzeigen.
12. Static-Assets-Block automatisch einfügen.
13. Auth, Audit-Log und Rollback härten.

## Offene Fragen

- Welche PM2-/Startscript-Templates brauchen wir spaeter zuerst?
