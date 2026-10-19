# Admin-IP-Allowlist

Die erste Installation von `config.pepperz.de` verwendet nach der Entscheidung
vom 19.10.2026 keine IP-Allowlist. Passwort, TOTP und die Anmeldebegrenzung
schützen den öffentlich erreichbaren Zugang.

Diese Anleitung beschreibt die optionale spätere Begrenzung auf feste
Quell-IPs. Erst nach deren Einrichtung den Include in die Nginx-Config aufnehmen.

Zielpfad auf dem Server:

```text
/etc/nginx/snippets/config-admin-allowlist.conf
```

Das Snippet enthaelt nur `allow`-Regeln und danach `deny all;`. Beispiel:

```nginx
# Managed by nginx-conf admin allowlist workflow.
allow 203.0.113.10/32;
allow 2001:db8::10/128;
deny all;
```

## SSH-Update

Eintrag lokal rendern und auf dem Server installieren:

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNIPPET="/etc/nginx/snippets/config-admin-allowlist.conf"

sudo install -d -m 0755 /etc/nginx/snippets
if sudo test -f "$SNIPPET"; then
  sudo cp -a "$SNIPPET" "$SNIPPET.$STAMP.bak"
fi

uv run python -m nginx_conf.admin_allowlist 203.0.113.10 2001:db8::10 \
  | sudo tee "$SNIPPET" >/dev/null

sudo nginx -t
sudo systemctl reload nginx
logger -t nginx-conf-admin-allowlist "updated $SNIPPET backup=$SNIPPET.$STAMP.bak"
```

Wenn kein Backup existierte, bleibt der `logger`-Eintrag trotzdem nuetzlich;
der Backup-Pfad zeigt dann auf eine nicht angelegte Datei. Bei ausgesperrter IP
per SSH verbinden, die erlaubte IP neu rendern, `nginx -t` ausfuehren und erst
danach reloaden.

## Nginx-Include

Bei aktivierter IP-Allowlist muss die Admin-vhost-Config das Snippet im passenden `server`- oder
`location`-Block einbinden:

```nginx
include /etc/nginx/snippets/config-admin-allowlist.conf;
```

## App-Pfad

Die Web-App schreibt diese Datei im MVP nicht direkt. Falls spaeter eine
App-Aktion eingefuehrt wird, muss sie denselben Inhalt rendern, vorher ein
Backup erzeugen, die Aenderung im Audit-Log erfassen, danach `nginx -t`
ausfuehren und Nginx nur nach erfolgreichem Test reloaden.
