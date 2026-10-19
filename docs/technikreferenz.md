# Nginx-Verwaltungstool: Technikreferenz

Diese Datei ist Referenzmaterial, keine Aufgabenliste. Aktuelle Arbeit wird in
Beads (`bd`) geplant und priorisiert.

## Zielbild

Die Anwendung ist ein kleines Webtool fuer genau einen Nutzer. Sie verwaltet
Nginx-Projektconfigs, SSL-Zertifikate und Webapp-Prozesse auf einem
Ubuntu-/Debian-Server mit Standard-Nginx-Struktur.

Die erste Seite soll als zentrale Systemuebersicht funktionieren. Dort sollen
alle wichtigen Betriebsinformationen sichtbar sein:

- Projekte unter `/opt/www`
- zugehoerige Nginx-Config-Dateien
- Domains aus `server_name`
- Aktivstatus der Configs
- Symlink-Ziel in `/etc/nginx/sites-enabled`
- Konflikte wie doppelte Domains, doppelte Ports oder kaputte Symlinks
- SSL-Zertifikatsstatus pro Domain
- Ablaufdatum und relative Ablaufnaehe, z.B. "in zwei Monaten"
- laufende App-Prozesse und ihr Status
- bei Django-Projekten: fehlende statisch lesbare `ALLOWED_HOSTS` nur als
  Warnung an der betroffenen Domain, nicht als eigene Dauerspalte
- passende sichere Aktionen wie Zertifikat ausstellen, Config aktivieren oder
  Prozess neu starten

Projekte koennen mehrere Nginx-Config-Dateien und mehrere Shell-Startskripte
enthalten, z.B. fuer Staging und Produktion. Die App muss deshalb pro Projekt
speichern, welche Nginx-Config und welches PM2-Startscript gelten sollen. Wenn
mehrere Kandidaten gefunden werden, darf die App nicht still anhand des
Dateinamens oder Ordners raten; sie muss eine Auswahl verlangen oder eine
bereits gespeicherte Auswahl verwenden.

## Grundsatzentscheidungen

- Projekt-Configs werden direkt aus `/opt/www` nach
  `/etc/nginx/sites-enabled` gesymlinkt.
- `/etc/nginx/sites-available` ist nicht der primaere Speicherort fuer
  Projekt-Configs.
- Nginx bleibt ein Systemdienst.
- Nginx wird bevorzugt per `systemctl reload nginx` neu geladen.
- Restart ist nicht der Normalfall, weil er aktive Verbindungen unnoetig
  beendet.
- PM2 ist initial der Prozessmanager fuer verwaltete Webapps unter `/opt/www`.
- Nginx selbst wird nicht ueber PM2 betrieben.
- Die App ist der zentrale Hub fuer Config-Aenderungen. Im MVP wird davon
  ausgegangen, dass Nginx-Config-Aenderungen, Aktivierungen, Certbot-Aktionen
  und PM2-Verwaltungsaktionen nicht parallel ausserhalb der App angestossen
  werden.
- Es gibt keinen automatischen Startup-Reconcile, der externe Aenderungen als
  normalen Arbeitsmodus sucht und uebernimmt. Gespeicherte Projektauswahl,
  App-Aktionen, Git-Workflow und Audit-/Aktionsstatus sind massgeblich.
- Es gibt im MVP auch keinen manuellen "Status aktualisieren"-Button als
  Reconcile-Ersatz.
- Nach Abschluss einer von der App gestarteten Aktion darf das Dashboard
  automatisch neu gerendert werden, um Ergebnisbanner, App-Aktionsstatus und
  die daraus folgende App-Sicht zu zeigen. Das ist UI-Aktualisierung nach
  eigener Aktion, kein Reconcile externer Aenderungen.
- Read-only Statusabfragen fuer Dashboard, Logs, SSL-Ablauf, PM2-Laufstatus
  oder Git-Dirty sind weiterhin erlaubt, sollen aber keine stille Uebernahme
  externer Aenderungen ausloesen.
- Das Webfrontend darf keine freie Shell anbieten.
- Sicherheitsrelevante Aktionen brauchen Allowlist, Pfadvalidierung und
  minimale Rechte.
- Features werden im ersten Schritt mit dem kleinsten sinnvollen Aufwand
  umgesetzt. Generische Ausbaustufen, Komfortvarianten und Sonderfaelle werden
  erst modelliert, wenn sie fuer den konkreten Workflow gebraucht werden.

## Technischer Stack

- Backend: Python 3.15, lokal über `.python-version` ausgewählt
- Projekt- und Dependency-Verwaltung: `uv`
- Webframework: FastAPI
- ASGI-Server: Uvicorn ohne optionale Extras; keine YAML-Logging-Konfiguration
  und keine WebSocket-Endpunkte. Der Produktionsbetrieb nutzt asyncio und h11.
- Templates: Jinja2
- Frontend-Interaktion: HTMX, wenn es konkrete Workflows vereinfacht
- Gestaltungsgrundlage: eigener Styleguide aus Vorspiel-Abrechnung, dokumentiert
  in `docs/styleguide.md` und unter `/styleguide` als geschuetzte Referenz.
  Ausschliesslich Desktop und Dunkelmodus, Source Code Pro, Farben aus der
  Ghostty-Shell-Palette, eckige Elemente ohne Rundungen, Schatten oder Verlaeufe.
  Hellmodus und mobile Gestaltung gehoeren nicht zum Projektumfang.
  Anmeldung, Authenticator-Einrichtung, Projektübersicht, Config-Editor und
  Logs verwenden die gemeinsamen Komponenten des Styleguides.
- Config-Editor: zuerst normales `<textarea>`, spaeter optional CodeMirror
- Betrieb der Verwaltungs-App: systemd als eigener unprivilegierter Systemuser
- Betrieb von Nginx: systemd
- Betrieb verwalteter Webapps: initial PM2
- Speicherung: SQLite unterhalb von `/opt/nginx-config/`
- Hintergrundaktionen: allowlisted Systemaktionen mit Laufstatus und Ergebnis
  werden in SQLite nachvollziehbar gemacht

## Dateisystem und Pfade

- Projektwurzel fuer verwaltete Webapps: `/opt/www`
- Nginx enabled Configs: `/etc/nginx/sites-enabled`
- Erwartete Standardstruktur: `/etc/nginx/sites-available` und
  `/etc/nginx/sites-enabled`
- Nginx-Config-Kandidaten in Projekten: ausschliesslich Dateien, deren Name
  auf `.nginx` endet
- Datenablage der Verwaltungs-App: `/opt/nginx-config/`
- SQLite liegt unterhalb der Datenablage.

Datei- und Symlinkoperationen duerfen nur auf erlaubten Pfaden arbeiten. Symlink
Traversal und Pfadfluchten muessen abgefangen werden.
Der App-Systemuser bekommt keinen pauschalen Schreibzugriff auf Projektordner
unter `/opt/www`. Schreibende Datei- und Git-Operationen an Projektdateien
laufen ueber einen kontrollierten Helper, der Projekt, gespeicherte
Nginx-Config-Auswahl, Realpath und Git-Scope validiert. Der Helper ist im MVP
eine root-eigene CLI mit festen Subcommands, kein dauerhaft laufender Daemon.

## Erlaubte Systemaktionen

Systemaktionen sollen zentral gekapselt werden. Erlaubt sind nur explizit
modellierte Kommandos und Dateioperationen, unter anderem:

- `nginx -t`
- `systemctl reload nginx`
- fest modellierte, nicht-interaktive `certbot`-Kommandos
- ausgewaehlte `pm2`-Kommandos
- ausgewaehlte `git`-Kommandos innerhalb des jeweiligen Projektordners
- Symlink- und Dateioperationen auf erlaubten Pfaden

Die App soll Exitcode, stdout und stderr strukturiert anzeigen und auditierbar
machen. Die Webapp selbst laeuft nicht als `root`, sondern als eigener
unprivilegierter Systemuser, z.B. `nginx-config`. Privilegierte Operationen
laufen nur ueber `sudo`-Regeln fuer absolute, explizit erlaubte Kommandos oder
eng kontrollierte root-eigene Helper fuer genau diese Aktionstypen. Sudoers
darf keine freie Shell, keine frei zusammengesetzten Argumentlisten und keine
breiten Wildcards erlauben. Erlaubt werden nur die benoetigten Aktionen wie
`nginx -t`, `systemctl reload nginx`, fest modellierte `certbot`-Aufrufe,
begrenzte Git-Kommandos fuer erlaubte Projektdateien, erlaubte PM2-Aktionen
und erlaubte Datei-/Symlinkoperationen auf validierten Pfaden.
Die Systemaktions-Schicht erzeugt dafuer feste argv-Listen und ruft Prozesse
ohne Shell auf. Die erwarteten absoluten Programme sind:

- `/usr/sbin/nginx -t`
- `/bin/systemctl reload nginx`
- `/usr/bin/certbot` nur ueber das feste nicht-interaktive
  `--nginx`-Template mit validierten `-d`-Domains
- `/usr/bin/certbot renew --dry-run --non-interactive`
- `/usr/bin/pm2` nur fuer die modellierten Operationen `jlist`, `start`,
  `restart`, `reload`, `stop`, `delete`, `logs --nostream`
- `/usr/local/lib/nginx-conf/nginx-conf-helper` nur mit festen Subcommands
  wie `write-config`, `reset-config-to-head`, `commit-config`,
  `activate-config`, `deactivate-config`, `clone-project` und `certbot-issue`

Der sudoers-Ausschnitt darf daraus keine Shell und keine Wildcards bauen. Die
App selbst laeuft als unprivilegierter User `nginx-config`; sudo wird nur fuer
die explizit benoetigten Programme bzw. den Helper gewaehrt. Variable Nutzdaten
fuer den Helper laufen als JSON ueber stdin und werden gegen `action_id`,
Projekt, gespeicherte Auswahl und erwartete Datei validiert.
Der Repo-Wrapper `scripts/nginx-conf-helper` ist dafuer das installierbare
CLI-Artefakt und soll im Betrieb root-owned an den obigen Helper-Pfad kopiert
werden. Der installierte Wrapper verwendet den festen Interpreter
`/opt/nginx-conf/.venv/bin/python -I` und den festen Quellpfad
`/opt/nginx-conf/src`. Der App-Runner stellt nur den explizit privilegierten
Kommandos `sudo -n` voran. Zertifikatsausstellung laeuft ueber das feste
Helper-Subcommand `certbot-issue`; dessen JSON-Request und gespeicherte Aktion
muessen dieselben Domains und dieselbe aktive Projekt-Config enthalten.
Konkrete Installationsartefakte liegen in `deploy/nginx-conf.service`,
`deploy/nginx-conf.nginx` und `deploy/nginx-conf.sudoers`; die Installationsschritte stehen in
`docs/deployment.md`.
Die Unit behaelt `ProtectSystem=strict` und `ProtectHome=true`, erlaubt aber
gezielte beschreibbare Betriebs-Mounts fuer den sudo-Helper.
Für `nginx -t` ist auch die bestehende `/run/nginx.pid` beschreibbar
eingebunden. Die Unit startet deshalb nach `nginx.service`; eine Freigabe
für das gesamte `/run` gibt es nicht. `PartOf=nginx.service` erneuert bei einem
Nginx-Neustart auch den PID-Datei-Mount der Admin-App. Dateirechte
schuetzen root-eigenen Code und Configs weiterhin vor direkten App-Zugriffen.
`NoNewPrivileges=false` erlaubt die durch sudoers begrenzte Rechteerhoehung;
`PrivateTmp=false` haelt Gunicorn-Sockets fuer Host-Nginx sichtbar.
Das gemeinsame `/tmp` ist ausdrücklich beschreibbar, damit unter anderem
Certbot sein temporäres Startprotokoll erstellen kann.
Fuer Schreibzugriffe in `/opt/www` und lokale Git-Commits ist der bevorzugte
Weg eine root-eigene Helper-CLI mit festen Subcommands, z.B. `write-config`,
`reset-config-to-head` und `commit-config`. Sie wird nur bei Bedarf per `sudo`
gestartet und laeuft nicht als dauerhafter Helper-Daemon. Der Helper darf keine
frei uebergebenen Pfade akzeptieren, sondern muss Projekt-ID bzw. gespeicherte
Projektauswahl, erlaubte Config-Datei, Realpath unter `/opt/www`, Git-Repo und
genau eine betroffene Datei pruefen. Das Subcommand bleibt ein festes
CLI-Argument; alle variablen Eingaben kommen als JSON auf `stdin`. Der Helper
antwortet auf `stdout` mit JSON, damit `action_id`, Exitcode, Fehler,
geaenderte Datei und Git-Commit-Hash eindeutig von der App ausgewertet und ins
Audit-Log uebernommen werden koennen. Vor jedem Helper-Aufruf legt die App
einen Aktionsdatensatz in SQLite an und uebergibt dessen `action_id` im
JSON-Request; die Helper-Antwort muss dieselbe `action_id` wieder enthalten.
Bei `write-config` enthaelt dieser JSON-Request auch den neuen Config-Inhalt
direkt. Die App schreibt dafuer keine temporaere Datei, die der Helper dann
lesen muesste. Dieser Config-Inhalt darf nicht in SQLite gespeichert werden:
nicht im Aktionsdatensatz, nicht im Ergebnisbanner-Datensatz und nicht im
Audit-Log.
Fehlt die `action_id`, ist sie unbekannt oder passt sie nicht zum erwarteten
Aktionsdatensatz, bricht der Helper vor jeder Datei- oder Git-Aenderung mit
Fehler-JSON und nicht erfolgreichem Exitcode ab.
Die Pruefung erfolgt im Helper selbst gegen SQLite: Die `action_id` muss
existieren, zu einer noch laufenden bzw. gestarteten Aktion gehoeren und
Subcommand, Projekt, gespeicherte Config-Auswahl sowie erwartete Datei muessen
zum Aktionsdatensatz passen. Der Helper oeffnet SQLite dafuer nur read-only und
schreibt keine Aktionsstatus-, Banner- oder Audit-Daten. Statusupdates,
Ergebnisbanner und Audit-Log bleiben Verantwortung der App, die das
Helper-Ergebnis auswertet.

Alle allowlisted Aktionen laufen im MVP exklusiv. Solange eine Aktion laeuft,
duerfen keine weiteren Aktionen gestartet werden; die UI soll Aktionsbuttons
sperren und API-Aufrufe mit einem klaren "Aktion laeuft bereits"-Status
ablehnen. Es gibt keine automatische Queue im MVP. Read-only Ansichten wie
Dashboard, Statusanzeige und Logs duerfen weiterhin geladen werden.
Bei laufender Aktion zeigt die UI oben eine globale Statusleiste, z.B. "Aktion
laeuft: Certbot fuer example.com". Die Leiste zeigt den wichtigsten Status
direkt an und erklaert, warum andere Aktionsbuttons gerade deaktiviert sind.
Waehrend eine Hintergrundaktion laeuft, darf die UI den Aktionsstatus eng
begrenzt pollen, z.B. alle 2 Sekunden. Dieses Polling aktualisiert nur
Statusleiste bzw. Ergebnisbanner und stoppt nach Abschluss der Aktion. Es ist
kein Dashboard-weiter Status-Refresh und kein Reconcile externer Aenderungen.
Eine eigene Aktionsdetailseite ist im MVP nicht vorgesehen; vorhandene
read-only Logs duerfen verlinkt werden, wenn sie fuer die Aktion relevant sind.
Nach Abschluss einer Hintergrundaktion bleibt ein Ergebnisbanner sichtbar, bis
er bestaetigt wird. Der ganze Banner ist klickbar und quittiert die Meldung; es
gibt im MVP keinen separaten kleinen Schliessen-Button. Der Banner zeigt
Erfolg oder Fehler, relevante Folgeschritte wie `nginx -t`, Reload oder
Commit-erforderlich direkt an und kann bei Bedarf auf vorhandene Logs
verweisen.
Nach `write-config` bleibt der Ergebnisbanner kompakt: Datei, Ergebnis von
`nginx -t`, Diff-Statistik und der naechste sinnvolle Schritt, z.B. weiter
bearbeiten, Commit anbieten oder Reload nach Commit. Vollstaendige Diffs,
Config-Inhalte und lange Diagnoseausgaben gehoeren nicht in den Banner, sondern
in die Config-/Git-Ansicht bzw. vorhandene read-only Logs.
stdout-/stderr-Auszüge werden im Ergebnisbanner nur aufklappbar angezeigt,
nicht in der Standardansicht. So bleibt der Banner kurz, erlaubt bei Fehlern
aber unmittelbare Diagnose ohne eigene Aktionsdetailseite.
Banner-Schweregrade: Fehler deutlich rot, Erfolg dezent gruen oder neutral.
Gelb/orange bleibt fuer separat modellierte Warnungen reserviert; ein eigener
Aktionsstatus "manuelle Klaerung erforderlich" ist im MVP nicht vorgesehen.
Nicht alle Ergebnisse sollen gleich laut wirken.
Nicht quittierte Ergebnisbanner verfallen nicht automatisch. Aktiv sichtbar ist
im MVP nur das letzte nicht quittierte Ergebnis; der vollstaendige Verlauf
bleibt ueber gespeicherte Aktionsdaten und Audit-Log erhalten.

## Nginx-Config-Workflow

Ein Projekt kann mehrere `*.nginx`-Dateien enthalten. Vor Aktivierung,
Bearbeitung oder Dashboard-Bewertung muss klar sein, welche Config fuer den
aktuellen Projekt-Datensatz gilt. Eindeutige Einzelfaelle duerfen automatisch
verwendet werden; mehrdeutige Faelle bleiben als "Auswahl erforderlich"
sichtbar. Die Auswahl wird in SQLite gespeichert, nicht im Projektordner.

Die Kandidatenerkennung bleibt bewusst simpel: Eine Nginx-Config ist im MVP
eine Datei, deren Name auf `.nginx` endet und die irgendwo unterhalb des
Projektordners liegt. Dateien wie `nginx.conf`, `nginx.config`,
`*.nginx-plesk-config` oder alte Plesk-/Snippet-Konfigurationen werden nicht
als Projekt-Config erkannt. Wenn keine `*.nginx`-Datei gefunden wird, zeigt die
App das Projekt als unvollstaendig, z.B. "Keine Nginx-Konfiguration gefunden.
Bitte stelle sicher, dass eine `*.nginx`-Datei im Projektordner liegt." Das
Projekt muss dann angepasst bzw. die Config umbenannt werden.

Mehrere `server`-Bloecke pro `*.nginx`-Datei sind normal und werden erwartet,
insbesondere HTTP-/HTTPS-Bloecke nach Certbot-Aenderungen. Historische
Sonderpfade wie alte Home-Verzeichnisse bekommen im MVP keine eigene
Kompatibilitaetslogik; solche Projekte werden bei Bedarf direkt im Projekt
vereinfacht.

Schreibende Config-Aenderungen folgen diesem Ablauf:

1. erlaubte Config und Pfad validieren
2. Aenderung speichern
3. Git-Status und Diff fuer erlaubte Projektdateien anzeigen
4. `nginx -t` ausfuehren
5. Ergebnis im UI anzeigen
6. nur nach erfolgreichem Test `systemctl reload nginx` anbieten oder ausfuehren
7. Commit fuer die geaenderte Config-Datei anbieten
8. Audit-Log schreiben

Im MVP gibt es kein Draft-System fuer Config-Edits. Speichern schreibt die
erlaubte Config-Datei direkt auf dem Server; Git-Diff, Test, Commit und Reload
folgen danach im kontrollierten Workflow.
Das Speichern selbst erfolgt nicht durch breite Schreibrechte des App-Users auf
`/opt/www`, sondern ueber den validierenden Helper fuer genau die erlaubte
Config-Datei. Der neue Config-Inhalt wird dabei im `write-config`-JSON ueber
`stdin` uebergeben; es gibt keinen Tempfile-Zwischenschritt zwischen App und
Helper.
Config-Anzeige und Bearbeiten im Textfeld bleiben auch waehrend einer laufenden
Aktion moeglich. Speichern ist aber selbst eine Aktion und wird durch den
globalen Aktions-Lock gesperrt; dasselbe gilt fuer Zuruecksetzen-auf-HEAD,
Commit und Reload.

Fuer manuelle Config-Edits aus der App gilt: Reload erst nach erfolgreichem
Git-Commit. `nginx -t` allein reicht fuer den Reload nicht aus, weil sonst ein
Serverzustand aktiv werden kann, der nicht im Projekt-Git steht.
Nach jedem Speichern laeuft `nginx -t` automatisch. Commit ist nur erlaubt,
wenn `nginx -t` fehlerfrei ist; kaputte Nginx-Configs werden aus der Admin-App
nicht als WIP committet. Reload ist nur nach fehlerfreiem Test und erfolgreichem
Commit erlaubt.
Wenn das Projekt als Django-Projekt erkannt wurde und die Domains der
geanderten Nginx-Config in statisch lesbaren Django-Settings nicht vollstaendig
in `ALLOWED_HOSTS` enthalten sind, zeigt die Startseite nur an der betroffenen
Domain bzw. `server_name`-Gruppe eine Warnung. Es gibt dafuer keine eigene
dauerhaft sichtbare Dashboard-Spalte. Nach einer App-gemachten
Nginx-Domainaenderung blockiert ein fehlender `ALLOWED_HOSTS`-Eintrag
ausserdem Commit und Reload, bis der Check sauber ist. Dynamische oder nicht
eindeutig statisch lesbare `ALLOWED_HOSTS`-Definitionen werden im MVP ignoriert
und blockieren nicht. Es werden keine `.env`-Settings ausgewertet und keine
fremden Django-Settings importiert oder ausgefuehrt.
Wenn ein Speichern eine fehlerhafte Config erzeugt, bleibt die Datei gespeichert
und die Textarea bleibt editierbar, damit direkt korrigiert und erneut
gespeichert/getestet werden kann. Zusaetzlich soll eine gezielte
Zuruecksetzen-auf-HEAD-Aktion fuer genau diese Config-Datei angeboten werden;
das gesamte Repo oder fremde Aenderungen duerfen dadurch nicht zurueckgesetzt
werden. Dieses Zuruecksetzen braucht eine Bestaetigung mit betroffener Datei
und Diff-Hinweis. Danach laeuft automatisch erneut `nginx -t`.

Separate Backup-Dateien sind im MVP nicht vorgesehen. Die Projektordner sind
git-verwaltet; Recovery und Nachvollziehbarkeit fuer Config-Dateien laufen ueber
Git-Historie und Commits.

Das Tool soll Commit-Messages anhand der Aktion vorschlagen, z.B. Config
aktualisiert, SSL via Certbot ergaenzt, Projektauswahl gesetzt oder
Static-Location eingefuegt. Die vorgeschlagene Message muss vor dem Commit im UI
editierbar sein.

Commits aus dem Tool duerfen genau eine aktionsbezogene erlaubte Config-Datei
umfassen. Andere vorhandene Aenderungen im Projekt werden nicht committet; das
UI zeigt nur, dass das Repo dirty ist. Wenn eine Aktion mehrere Dateien
aendert, bietet das Tool keinen automatischen Commit an und markiert den
Commit-Scope als unklar bzw. manuelle Nacharbeit erforderlich.
Auch Commit und gezieltes Zuruecksetzen auf HEAD laufen ueber den Helper; er
committet oder setzt nur die eine validierte Config-Datei zurueck und darf
keine fremden Aenderungen in den Commit aufnehmen.

Git Push ist nicht Teil des MVP. Das Tool darf lokale Commits auf dem Server
erstellen und anzeigen, dass ein lokaler Commit vorhanden ist. Push bleibt
manuell per SSH/Git oder ein spaeteres separates Feature nach Auth- und
Remote-Klaerung.

Ein dirty Projekt-Repo durch fremde Aenderungen blockiert das Editieren nicht
grundsaetzlich. Das UI warnt, committet aber weiterhin nur die
aktionsbezogenen erlaubten Dateien. Blockierend ist ein Dirty-Konflikt nur,
wenn die konkret betroffene Datei bereits fremd geaendert ist oder der sichere
Commit-Scope nicht eindeutig bestimmt werden kann.
Die Live-Diff-Ansicht im MVP ist auf die aktuell erlaubte, aktionsbezogene
Config-Datei begrenzt. Repo-weite Diffs sind kein MVP-Workflow; fremde
Aenderungen werden nur als "Repo dirty" sichtbar gemacht.

Das Audit-Log ist ein schlankes App-internes Ereignisprotokoll in SQLite. Es
ersetzt nicht Git und nicht Server-Logs. Es speichert Metadaten wie Zeitpunkt,
Aktion, Projekt, Datei, Domain oder Prozess, Ergebnis, Git-Commit-Hash,
Exitcode und begrenzte Fehlermeldungen. Secrets und komplette Config-Inhalte
werden nicht ins Audit-Log geschrieben.
Schreibende Aktionen erhalten eine eindeutige `action_id`, die Statusleiste,
Helper-Request, Helper-Response, Ergebnisbanner und Audit-Eintrag miteinander
verbindet. Aktionen ohne gueltige `action_id` duerfen keine Schreibwirkung
haben. Der Aktionsdatensatz speichert genug validierte Parameter, damit der
Helper `action_id`, Subcommand, Projekt, Config-Bezug und erwartete Datei
read-only gegen SQLite pruefen kann. Der Helper schreibt nicht in SQLite;
Status- und Audit-Aenderungen erfolgen ausschliesslich durch die App.
Der neue oder alte Config-Inhalt wird auch nicht im Aktionsdatensatz oder
Ergebnisbanner-Datensatz gespeichert. Erlaubt sind Metadaten wie Datei,
Git-Diff-Statistik, Git-Commit-Hash, Exitcode und begrenzte Fehlerauszuege;
der vollstaendige Inhalt bleibt in der Projektdatei und in Git.
Vollstaendige Git-Diffs werden nicht in SQLite, Audit-Log oder Ergebnisbanner-
Daten gespeichert. Wenn die UI einen Diff braucht, liest sie ihn live aus dem
Projekt-Git, aber nur fuer die aktuell erlaubte Config-Datei der laufenden oder
zuletzt abgeschlossenen Aktion.
Zusaetzliche Dateiinhalt-Hashes wie `before_hash` oder `after_hash` sind kein
MVP-Bestandteil und koennen spaeter als eigenes Diagnose-/Integritaetsfeature
bewertet werden.

Laenger laufende Systemaktionen, insbesondere Certbot, sollen zusaetzlich als
Hintergrundaktionen mit Status gespeichert werden: gestartet, laeuft,
erfolgreich oder fehlgeschlagen. Der Eintrag enthaelt die validierten
Parameter, Projekt-/Config-Bezug, Start-/Endzeit, Exitcode und begrenzte
stdout-/stderr-Auszüge. Dadurch bleibt das Ergebnis auch nach Seitenwechsel
oder Reload sichtbar.
Abgeschlossene Hintergrundaktionen koennen als "quittiert" markiert werden,
damit Ergebnisbanner nach einem Klick auf den ganzen Banner verschwinden.
Quittieren veraendert nur diesen UI-Status; Audit-Log, gespeicherte Aktionsdaten,
Exitcode und stdout-/stderr-Auszüge bleiben erhalten.
Der Hintergrundaktions-Status dient zugleich als globaler Aktions-Lock:
Nur eine Aktion darf den Status `laeuft` haben.
Ein eigener Lock-Reset- oder Recovery-Workflow ist nicht Teil des MVP. Aktionen
sollen sauber abgeschlossen und Fehler als Status gespeichert werden; falls ein
stale Lock real auftritt, wird das spaeter als konkretes Problem behandelt.

Aktivieren und Deaktivieren erfolgt ueber verwaltete Symlinks nach
`/etc/nginx/sites-enabled`.

Die App geht im MVP nicht davon aus, dass ausserhalb laufend Nginx-Sites
aktiviert oder geaendert werden. Falls bei Projektimport, expliziter Pruefung
oder einer notwendigen Statusabfrage trotzdem Symlinks in
`/etc/nginx/sites-enabled` auftauchen, die auf unbekannte Ziele zeigen, werden
sie als fremde Nginx-Sites gewarnt. Symlinks auf bekannte Projekt-Configs, die
nicht zur gespeicherten Projektauswahl passen, werden als abweichende aktive
Config gewarnt. Im MVP wird kein fremder oder abweichender Nginx-Zustand still
uebernommen.

Rollback wird spaeter als Git-basierter Revert- oder Ruecksprung-Workflow fuer
genau eine gespeicherte Projekt-Config umgesetzt, nicht ueber separate
Backup-Dateien. Im MVP reicht das vorhandene gezielte
Zuruecksetzen-auf-HEAD fuer fehlerhafte aktuelle Edits; ein historischer
Rollback auf aeltere Commits bleibt nachgelagert.
Ein spaeterer App-Workflow darf nur lokale Git-Historie der gespeicherten
Config-Datei anzeigen und auswaehlen. Revert/Ruecksprung muss den Ziel-Commit,
die betroffene Config-Datei und den entstehenden Diff sichtbar machen, laeuft
ueber den Helper mit globalem Aktions-Lock, schreibt Audit-Metadaten, fuehrt
anschliessend `nginx -t` aus und erlaubt Reload erst nach erfolgreichem Test
und sauberem Commit- bzw. Revert-Scope. Repo-weite Resets, freie Git-Kommandos,
Backup-Datei-Rollbacks und stilles Uebernehmen fremder Aenderungen bleiben
ausgeschlossen.

Static-Asset-Location-Bloecke werden aus einer erlaubten Kandidatenliste im
Projekt eingefuegt, nicht aus freien Pfaden. Im MVP erkennt die App
Verzeichnisse namens `static`, `staticfiles`, `assets`, `public`, `dist` oder
`build` in geringer Tiefe unterhalb des Projektordners und ignoriert Symlinks
sowie Cache-/Dependency-Verzeichnisse wie `.git`, `.venv`, `venv`,
`node_modules` und `__pycache__`. Die Location wird fest aus dem Verzeichnis
abgeleitet: `assets` wird `/assets/`, alle anderen Kandidaten werden
`/static/`. Der Block nutzt `alias <absoluter-projektpfad>/;` und wird in den
ersten `server`-Block der gespeicherten Config geschrieben.
Ein bereits vorhandener gleicher `location`-/`alias`-Block wird nicht doppelt
eingefuegt; ein vorhandener gleicher `location`-Pfad mit anderem Alias wird als
Konflikt abgelehnt. Das Schreiben nutzt denselben Helper-Workflow wie der
Config-Editor und laeuft danach durch `nginx -t`, Git-Diff/-Status und den
normalen Commit-Workflow.

Ein allgemeines Nginx-Snippet- oder Template-System wird nicht als freie
Texttemplate-Engine umgesetzt. Entwurf: Die App bekommt eine kleine Registry
fest modellierter Snippet-Typen. Jeder Typ definiert selbst:

- erlaubte Eingabeparameter
- Pfad- und Wertvalidierung
- Render-Funktion fuer den Nginx-Block
- Erkennungs-/Idempotenzregel fuer bereits vorhandene Bloecke
- Konfliktregel, wenn derselbe Zielkontext mit anderem Inhalt existiert
- Zielkontext in der Config, z.B. erster `server`-Block oder konkret
  ausgewaehlter `server`-Block
- Folgeworkflow mit Helper-Write, `nginx -t`, Git-Diff/-Status und Commit

Der Static-Asset-Location-Block ist der erste umgesetzte Registry-Typ. Weitere
Snippets duerfen nur hinzukommen, wenn ihre Parameter strukturiert validierbar
sind. Freie Jinja-/Nginx-Texttemplates, beliebige Include-Pfade,
benutzerdefinierte Raw-Bloecke und repo-weite automatische Formatierungen sind
kein MVP-Verhalten.

## Projektanlage

Im MVP werden vorhandene Projektordner unter `/opt/www` inventarisiert. Ein
spaeterer "Projekt hinzufuegen"-Workflow kann ein Projekt ueber eine GitHub-
Clone-URL auf den Server holen, damit der Ordner nicht manuell angelegt werden
muss.

Auch dieser Workflow darf keine freie Shell anbieten. Im ersten Schritt reicht
eine eng begrenzte Git-Clone-Aktion: erlaubte GitHub-URL validieren, Zielordner
unter `/opt/www` bestimmen, bereits vorhandene Ziele ablehnen, `git clone` als
fest modellierte Aktion ausfuehren und danach das Projektinventar neu lesen.
Weitere Setup-Schritte bleiben separate Aktionen.
Der Clone laeuft ueber das root-owned Helper-Subcommand `clone-project` mit
globalem Aktions-Lock. Erlaubt sind nur GitHub-HTTPS- oder GitHub-SSH-URLs im
Format `https://github.com/<owner>/<repo>.git` bzw.
`git@github.com:<owner>/<repo>.git`. Credentials, Query-Strings, Fragmente,
nicht-GitHub-Hosts, mehrstufige Pfade und unklare Repo-Namen werden abgelehnt.
Der Zielordner wird ausschliesslich aus dem Repo-Namen abgeleitet, muss direkt
unter `/opt/www` liegen und darf noch nicht existieren. Die Aktion fuehrt nur
`git clone <normalisierte-url> <zielordner>` aus; keine freien Git-Kommandos,
keine beliebigen Zielpfade und keine kombinierten Setup-Schritte.

Initiale Projekteinrichtung wie Virtualenv anlegen, Secrets eintragen,
projektbezogene Setup-Kommandos ausfuehren oder alte Dateinamen auf den
aktuellen Standard bringen ist im MVP Handarbeit bzw. spaeter ein eigenes
Feature. Docker ist keine Voraussetzung fuer dieses Tool.

## Certbot und SSL

Im MVP darf `certbot --nginx` verwendet werden, weil es fuer den ersten
Ausbauschritt einfacher ist. Wenn Certbot die aktive Projekt-Config aendert,
muss das Tool danach `nginx -t`, Reload und den Git-Commit-Workflow fuer die
geaenderte Config sichtbar machen.

Certbot wird aus der App nicht interaktiv gestartet. Die App verwendet ein fest
modelliertes Command-Template; die `server_name`-Gruppe muss vorher im UI
feststehen. Wenn ein Nginx-`server`-Block mehrere Namen enthaelt,
z.B. `example.com www.example.com`, werden diese Namen gemeinsam mit mehreren
`-d`-Argumenten in ein Zertifikat aufgenommen. Wildcard-Namen wie
`*.example.com` sind im MVP nicht erlaubt; die App nutzt kein DNS-01-Verfahren
und keine Provider-Credentials. Ebenso nicht certbot-faehig sind
`server_name`-Werte wie `_`, `localhost`, IP-Adressen oder interne
Einzelnamen. Solche Namen bleiben in der Startseite sichtbar, bekommen aber
keine Zertifikat-ausstellen-Aktion. Wenn eine `server_name`-Gruppe nicht
certbot-faehige Namen enthaelt, startet die App fuer diese Gruppe keinen
Certbot-Aufruf, statt einzelne Namen still herauszufiltern. HTTPS-Redirect ist
im MVP immer aktiv. Die Kontakt-E-Mail ist fest `andy@freilandkiwis.de`; es
gibt keine projektbezogenen oder dialogbezogenen Ausnahmen. Der erlaubte Aufruf
enthaelt explizite Argumente wie:

```bash
certbot --nginx --non-interactive --agree-tos --email andy@freilandkiwis.de --no-eff-email --keep-until-expiring -d <domain> [-d <domain> ...] --redirect
```

`--redirect` ist im MVP fest gesetzt; es gibt keinen UI-Schalter fuer
`--no-redirect`. Implizite Certbot-Fragen, freie Zusatzargumente oder
nachtraegliche Terminal-Prompts sind kein erlaubter Web-App-Workflow. Alle
Certbot-Parameter, die sonst Rueckfragen ausloesen wuerden, muessen im
Command-Template festgelegt sein. `--keep-until-expiring` ist im MVP gesetzt,
damit vorhandene passende Zertifikate nicht unnoetig ersetzt werden und Certbot
nicht interaktiv nachfragt. Force-Renew ist kein MVP-Verhalten und muesste
spaeter als eigenes bewusstes Feature modelliert werden.
`--cert-name` wird im MVP nicht gesetzt; Certbot soll seine Standardbenennung
verwenden, also den ersten angegebenen Domainnamen oder ein bereits vorhandenes
passendes Zertifikat.

Der Certbot-Aufruf soll aus Sicht der Web-App als Hintergrundaktion laufen. Das
UI zeigt laufenden Status, Exitcode, stdout/stderr-Auszug, vorherigen und
anschliessenden `nginx -t`-Status sowie die daraus folgenden naechsten
Schritte, ohne dass ein Terminal-Dialog offen bleibt oder Benutzereingaben
erwartet werden.

Vor Certbot muss die `server_name`-Gruppe eindeutig zur gespeicherten
Projekt-Config und zu genau einem Nginx-`server`-Block passen. Wenn ein Name
aus dieser Gruppe in mehreren aktiven oder bekannten Nginx-Configs vorkommt,
ist das kein normaler Certbot-Workflow, sondern ein zu klaerender
Konfigurationskonflikt. Die App mischt im MVP keine Namen aus mehreren
`server`-Bloecken zu einem Zertifikat. Direkt vor Certbot fuehrt die App als
Teil derselben Hintergrundaktion `nginx -t` aus. Wenn dieser Vorabtest
fehlschlaegt, wird Certbot nicht gestartet und das Ergebnis im Banner angezeigt.
Vor Certbot prueft die App ausserdem read-only DNS fuer alle Namen der
`server_name`-Gruppe. Die aufgeloesten A-/AAAA-Records muessen zu den in der
App konfigurierten oeffentlichen Server-IP-Adressen passen; die App soll diese
IP-Adressen nicht blind aus lokalen Interfaces erraten. Wenn fuer einen Namen
AAAA-Records existieren, muessen auch diese auf eine konfigurierte Server-IPv6
zeigen; vorhandene IPv6-Aufloesung wird nicht ignoriert. Bei fehlender,
widerspruechlicher oder nicht passender DNS-Aufloesung startet Certbot nicht
und zeigt einen Fehlerbanner. Nach Certbot laeuft erneut `nginx -t`; danach
prueft die App den Git-Diff: Reload und automatischer Commit bleiben nur
erlaubt, wenn genau die erwartete, projektbezogene Config-Datei geaendert
wurde. Aenderungen an anderen oder mehreren Config-Dateien werden als
unkontrollierter Zustand angezeigt und muessen manuell geklaert werden.

Certbot ist eine Ausnahme zur Commit-vor-Reload-Regel fuer manuelle Edits: Nach
von der App gestartetem `certbot --nginx` und erfolgreichem `nginx -t` darf
Reload ausgefuehrt werden. Danach muss die App prominent anzeigen, dass fuer die
Certbot-Aenderung noch ein Git-Commit erforderlich ist, bis die betroffene
Config committet wurde.

Wenn Certbot zu unkontrollierbare Config-Aenderungen erzeugt, soll spaeter auf
manuell verwaltete SSL-Bloecke gewechselt werden.

Die App soll keine eigene automatische Erneuerung erzwingen. Certbot bringt je
nach Installation bereits Cron oder systemd Timer mit. Die App soll diese
Mechanismen erkennen und per `certbot renew --dry-run` pruefbar machen.
Die Startseite warnt frueh anhand der Restlaufzeit: gelb bei weniger als
30 Tagen, rot bei weniger als 14 Tagen und kritisch bei weniger als 7 Tagen.
Diese Warnungen sollen Risiko sichtbar machen; sie ersetzen nicht den
Certbot-Renewal-Mechanismus.
Die Erkennung der automatischen Erneuerung ist read-only: bekannte Certbot-
systemd-Timer wie `certbot.timer` und `snap.certbot.renew.timer` werden als
installiert bzw. enabled erkannt, bekannte Cron-Dateien nur bei sichtbarem
`certbot renew`-Eintrag als konfiguriert. Die App legt keinen Cronjob und
keinen systemd Timer an. Der Dry-Run ist eine eigene allowlisted Systemaktion
mit globalem Aktions-Lock, Exitcode, begrenzten stdout-/stderr-Auszügen und
Audit-Eintrag.

## PM2-Prozessverwaltung

Initialer Provider ist PM2. Geplante Daten pro Job:

- Name
- Projektordner
- Startscript
- Status
- Restart-Zaehler
- letzte Log-Zeilen

Wenn mehrere Shell-Skripte als Startkandidaten im Projekt liegen, muss fuer den
Projekt-Datensatz ein PM2-Startscript ausgewaehlt werden. Diese Auswahl wird in
SQLite gespeichert und bei Statusanzeige und spaeterer Prozessanlage verwendet.
Im MVP werden keine freien Startkommandos eingegeben. Kandidaten sind
Shell-Dateien im Projektbaum, primaer `*.sh`. Bekannte Namen wie `start.sh`,
`run.sh`, `serve.sh` oder der Projektname duerfen die Sortierung beeinflussen,
ersetzen bei mehreren Kandidaten aber keine explizite Auswahl. `node_modules`,
`.venv`, `venv`, `.git`, `__pycache__`, Build- und Cache-Verzeichnisse werden
ignoriert; Symlinks werden zunaechst nicht verfolgt.

Die App interessiert sich nicht dafuer, ob dieses Startscript intern uWSGI,
Gunicorn, Uvicorn, Node, ein Virtualenv oder sonstige Projektdetails nutzt. Das
ist Domaene des jeweiligen Projekts. Ohne passendes Startscript ist das Projekt
fuer PM2-Verwaltung in der App unvollstaendig und muss im Projekt nachgeschaerft
werden. Worker-, Wartungs- oder Initialisierungsskripte werden im MVP nicht als
eigene Prozessrollen modelliert.

PM2-Prozesse, die nicht zur gespeicherten Projektauswahl passen oder ausserhalb
der verwalteten Projektstruktur laufen, sollen als fremd oder unverwaltet
gewarnt werden. Verwaltet ist ein PM2-Prozess nur, wenn die App ihn in SQLite
als verwalteten Prozess registriert hat, z.B. mit Projekt, PM2-Name oder ID,
`cwd`, ausgewaehltem Startscript und Zeitstempel. `cwd` unter `/opt/www` reicht
nur fuer Zuordnung und Warnkontext, nicht fuer den Status "verwaltet". Ziel ist,
manuell installierte oder gestartete Prozesse sichtbar zu machen, nicht sie
still als normalen App-Zustand zu akzeptieren.

Unverwaltete PM2-Prozesse werden im MVP nicht importiert oder uebernommen. Das
UI soll warnen und darauf hinweisen, den Prozess manuell zu stoppen und danach
ueber die App neu anzulegen.

Geplante Provider-Operationen:

- `list()`
- `create(job)`
- `start(id)`
- `stop(id)`
- `restart(id)`
- `reload(id)`
- `delete(id)`
- `logs(id, lines=100)`

PM2-Schreibaktionen fallen unter denselben globalen Aktions-Lock wie Nginx-
und Certbot-Aktionen. Im MVP laeuft also auch kein PM2-Start, Stop, Restart,
Reload, Delete oder Create parallel zu irgendeiner anderen Aktion. Read-only
Status- und Logansichten bleiben weiterhin erlaubt.
PM2-Create nutzt ausschliesslich das in der Projektauswahl gespeicherte
Startscript und registriert den Prozess danach in SQLite. Lifecycle-Aktionen
werden nur fuer solche registrierten Prozesse ausgefuehrt; fremde Prozesse
bleiben read-only Warnkontext. `reload` bleibt als Provider-Operation
modelliert, wird im UI aber nicht pauschal angeboten, solange kein sinnvoller
Reload-Modus aus PM2-Daten abgeleitet wird.

## Logs

Logansichten sind read-only und fallen nicht unter den globalen Aktions-Lock.
Sie duerfen auch geladen werden, waehrend eine schreibende Aktion laeuft. Im
MVP sind die Systemlogs fest allowlisted:

- `/var/log/nginx/error.log`
- `/var/log/nginx/access.log`
- `/var/log/letsencrypt/letsencrypt.log`

Die App liest nur begrenzte Tail-Auszüge und akzeptiert keine freien Logpfade.
PM2-Logs laufen ueber das modellierte Kommando
`pm2 logs <name> --lines <n> --nostream`; `<name>` muss vorher in der aktuellen
PM2-Prozessliste vorkommen. Die Zeilenzahl ist auf 1 bis 500 begrenzt.
Fehlende Leserechte oder fehlende Dateien werden als Log-Warnung angezeigt,
nicht als schreibende Aktion behandelt.

Erlaubte PM2-Kommandos:

- `pm2 jlist`
- `pm2 start ... --name ... --cwd ...`
- `pm2 restart <name>`
- `pm2 reload <name>`
- `pm2 stop <name>`
- `pm2 delete <name>`
- `pm2 logs <name> --lines 100 --nostream`
- `pm2 save --force`

Erfolgreiche PM2-Schreibaktionen speichern anschliessend die Prozessliste.
Ein Speicherfehler wird als Fehler gemeldet. Fuer den Wiederanlauf wird der
PM2-Systemdienst vor der Verwaltungs-App unter demselben Nutzer und mit
`PM2_HOME=/opt/nginx-config/.pm2` eingerichtet.

Projektordner muessen unter `/opt/www` liegen. `reload` soll nur angeboten
werden, wenn es fuer den Prozess sinnvoll ist; sonst `restart`. Freie
Port-/Kommando-Templates sind kein MVP-Verhalten; solche Details gehoeren ins
projektseitige Startscript.

Eine optionale zweite Provider-Variante fuer verwaltete Webapps ueber systemd
wird nicht im MVP umgesetzt. Bewertung: sinnvoll nur, wenn konkrete Projekte
dauerhaft als eigene systemd Units statt PM2-Prozessen betrieben werden sollen.
Der Provider muesste dieselbe abstrakte Funktionsflaeche wie PM2 bedienen:
`list`, `create`, `start`, `stop`, `restart`, `reload`, `delete` und `logs`.
Dabei waeren zusaetzlich Unit-Namen, Unit-Dateipfade, erlaubte Service-
Templates, `systemctl`-Kommandos, `journalctl`-Logzugriff, Reload-Faehigkeit
und Ownership der Unit-Dateien zu modellieren. Create/Delete waeren besonders
sicherheitsrelevant, weil sie Dateien unter `/etc/systemd/system` schreiben,
`systemctl daemon-reload` ausfuehren und klare Loesch-/Disable-Regeln brauchen.

Entscheidung fuer den aktuellen Stand: PM2 bleibt einziger verwalteter
Webapp-Prozessprovider. systemd fuer Webapps bleibt ein spaeterer separater
Provider hinter derselben Provider-Schnittstelle; Nginx selbst bleibt davon
unabhaengig immer systemd-Dienst.

## Django-Betriebsaktionen

Nach dem MVP sollen fuer Django-Projekte typische Deploy-Schritte als eigene
allowlisted Aktionen bewertet werden:

- `manage.py migrate`
- `manage.py collectstatic --noinput`
- `manage.py compress`

Diese Aktionen sind keine freie `manage.py`-Konsole. Sie brauchen denselben
globalen Aktions-Lock, Audit-/Ergebnisstatus und Projektpfadschutz wie Nginx-,
Certbot- und PM2-Aktionen.
Die App fuehrt sie ueber die Projekt-Python-Binary aus
`.venv/bin/python`, `.venv/bin/python3`, `venv/bin/python` oder
`venv/bin/python3` und die feste `manage.py` im Projektordner aus. Projektpfad
und `manage.py` muessen unter `/opt/www` validiert sein. Freie
`manage.py`-Subcommands, freie Zusatzargumente, Shell-Ausfuehrung und sudo sind
nicht vorgesehen.
`collectstatic` nutzt immer `--noinput`. `compress` wird nur angeboten, wenn in
statisch lesbaren Dependency-Dateien ein `django-compressor`-Hinweis steht;
andernfalls bleibt die Aktion deaktiviert. Fehlt `manage.py` oder die
Projekt-Python-Umgebung, zeigt die UI die Django-Aktionen nicht bzw. deaktiviert
sie mit klarem Blocker.

## Auth und Netzwerkbegrenzung

Ziel-Origin fuer die Verwaltungs-App auf pepperz ist `config.pepperz.de`.
Das erste verwaltete Projekt vorspiel-abrechnung nutzt `va.pepperz.de`.

Die Anwendung hat genau einen Zugang ohne Nutzername oder Nutzerverwaltung.
Die reguläre Anmeldung im Browser verlangt Passwort und TOTP-Code.
Web-Setup, Setup-Token und per SSH erzeugte Login-Tokens entfallen.

Bei der Installation legt `scripts/nginx-conf-auth` ein vorläufiges
Installationspasswort fest. Es wird verdeckt zweimal eingegeben und bereits
als Argon2id-Hash in `/opt/nginx-config/nginx-conf.sqlite3` gespeichert.
Beim ersten Login prüft die App dieses Passwort und verlangt im Dialog
„Setze dein Passwort“ ein eigenes Passwort samt Wiederholung. Dieses muss
mindestens 12 Zeichen haben und vom Installationspasswort abweichen.
Der Hash wird atomar ersetzt; das Installationspasswort, bestehende Sessions
und andere laufende Einrichtungen werden dabei ungültig. Die vorläufige
Browserberechtigung wird rotiert und bleibt auf ihre ursprüngliche Ablaufzeit
beschränkt. Ein erneutes Anmelden mit dem eigenen Passwort setzt eine
abgebrochene Einrichtung beim Authenticator fort.
Anschließend zeigt die App einen lokal erzeugten QR-Code für die
Authenticator-App. Alternativ lässt sich der Schlüssel manuell eintragen.
Erst die Bestätigung mit einem aktuellen TOTP-Code aktiviert den Authenticator
und erzeugt die erste App-Sitzung. Der bestätigte Code ist damit verbraucht.

Die vorläufige Einrichtungsberechtigung für beide Schritte läuft insgesamt
nach zehn Minuten ab. Sie ist
an den aktuellen Passwort-Hash gebunden und erlaubt keinen Zugriff auf die
App oder deren APIs. Ihr Zufallstoken wird serverseitig nur gehasht gespeichert;
das HTTPS-Cookie ist Secure, HttpOnly und SameSite=Strict. Der QR-Code bleibt
beim Neuladen und bei falscher Code-Eingabe gleich. Die Authentifizierungsseiten
werden mit `Cache-Control: no-store` und `Referrer-Policy: same-origin` ausgeliefert.
Damit bleiben externe Referrer unterdrückt und der Browser sendet bei eigenen
Formularen weiterhin den prüfbaren Origin.
Einrichtung und Sitzung werden atomar gespeichert. Eine parallele Einrichtung
kann einen bereits bestätigten Authenticator nicht überschreiben. Nach der
Bestätigung werden alle vorläufigen Einrichtungsberechtigungen gelöscht.

Zum Wiederherstellen nach Verlust dient derselbe lokale Installationsbefehl
mit `--replace`. Er setzt ein neues Installationspasswort, entfernt den Authenticator und
invalidiert alle Sessions und laufenden Einrichtungen. Beim nächsten Login
wird zuerst das eigene Passwort gesetzt und danach der Authenticator wieder
im Browser eingerichtet. Bestehende Zugänge mit bestätigtem Authenticator
behalten beim Datenbank-Update ihren bisherigen Ablauf. Noch nicht abgeschlossene
Einrichtungen verlangen vor der TOTP-Bestätigung den Passwortwechsel.

`argon2-cffi` speichert das Passwort als Argon2id-Hash mit eigenem Salt,
64 MiB Speicher, drei Durchläufen und Parallelität 1. `PyOTP` prüft sechsstellige
Codes in 30-Sekunden-Intervallen mit einer Toleranz von einem Intervall in
beide Richtungen. Der zuletzt verwendete Zeitschritt und die Session werden
in einer SQLite-Transaktion geschrieben. Ein Code kann auch bei parallelen
Anmeldungen nur einmal verwendet werden; ein laufender Login mit zwischenzeitlich
ersetzten Zugangsdaten wird verworfen.

Vor der Passwortprüfung wird jeder Versuch atomar reserviert. Fünf Versuche
ohne erfolgreiche Anmeldung sperren weitere Versuche für 60 Sekunden.
Der Zähler liegt in SQLite und bleibt bei einem Prozessneustart erhalten.
Passwortprüfung und Code-Bestätigung teilen diesen Zähler. Erst eine vollständig
erfolgreiche Anmeldung setzt ihn zurück. Sperren antworten mit HTTP 429 und
`Retry-After`. Andere fehlgeschlagene Anmeldungen verraten nicht, welcher
Faktor falsch war.

Die SQLite-Datei enthält einen einzelnen Zugang und dessen TOTP-Geheimnis
und ist nur für den App-Systemnutzer les- und schreibbar (`0600`). Backups
enthalten ebenfalls dieses Geheimnis. Session-Tokens werden ausschließlich
gehasht gespeichert. HTTPS-Cookies sind Secure, HttpOnly und SameSite=Lax;
Sessions laufen nach 30 Tagen ab. Abmelden invalidiert die serverseitige Session.

Die Migration entfernt alte Nutzer- und Session-Tabellen sowie alte
Setup-/Login-Tokens. Produktdaten, Audit-Daten und gespeicherte Server-IPs
bleiben erhalten. IPs sind keine Anmeldeangaben und sollen später über einen
separaten Dialog gepflegt werden. Keine automatische Ableitung öffentlicher
IPs aus lokalen Netzwerkschnittstellen.

Quellen: [OWASP zur Passwortspeicherung](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
[PyOTP](https://pyauth.github.io/pyotp/) und
[argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/howto.html).

Die erste Installation von `config.pepperz.de` ist nach der Entscheidung vom
19.10.2026 ohne IP-Allowlist öffentlich erreichbar. Passwort, TOTP und die
persistente Begrenzung der Anmeldeversuche bleiben verpflichtend. Eine spätere
IP-Allowlist sowie Tailscale oder VPN bleiben mögliche Netzwerkbegrenzungen.

Kontext der optionalen IP-Allowlist:

- Allowlist-Datei: `/etc/nginx/snippets/config-admin-allowlist.conf`
- Nginx-Include fuer erlaubte Admin-IPs
- SSH-Fallback ist in `docs/admin-allowlist.md` dokumentiert und rendert das
  Snippet mit `python -m nginx_conf.admin_allowlist`
- vor dem Ueberschreiben wird die bestehende Datei mit Zeitstempel gesichert
- danach `nginx -t`, `systemctl reload nginx` und ein Syslog-Audit-Eintrag
- Fallback bei ausgesperrter IP: per SSH auf den Server, Allowlist-Datei
  aktualisieren, Nginx testen und reloaden

Die Web-App schreibt diese Allowlist-Datei im MVP nicht direkt. Falls spaeter
eine App-Aktion dafuer eingefuehrt wird, muss sie denselben Inhalt rendern,
Backup und Audit-Log schreiben, `nginx -t` ausfuehren und Nginx nur nach
erfolgreichem Test reloaden.

## Qualitaetssicherung

- Ruff ist Linter und Formatter.
- pre-commit ist als Dev-Dependency vorgesehen.
- Pre-commit-Hooks fuer `ruff check --fix`, `ruff format` und `pytest` sind
  eingerichtet.
- Normaler lokaler Flow vor Commit: `uv run pre-commit run --all-files` oder
  direkt committen und die Hooks laufen lassen.
- Der Test-Hook laeuft mit `pass_filenames: false`, damit die komplette
  relevante Testsuite ausgefuehrt wird statt nur dateibezogen zu testen.
- Lokale Hooks koennen mit `git commit --no-verify` bewusst umgangen werden;
  das ist eine explizite Ausnahme und kein Standardweg.

## Getroffene Produktentscheidungen

- PM2-Prozessanlage nutzt im MVP ausschliesslich das pro Projekt ausgewaehlte
  Shell-Startscript. Freie Startkommandos, PM2-Ecosystem-Dateien, YAML-
  Interpretation, Framework-Erkennung und Port-Templates sind kein MVP.
