# Styleguide für nginx-conf

Die eigenständige Referenz liegt unter `/styleguide` und braucht dieselbe
Anmeldung wie die übrige Verwaltungs-App.

## Verbindliche Projektvorgaben

- Ausschließlich Dunkelmodus.
- Ausschließlich Desktop. Keine mobile Gestaltung oder mobile Abnahme.
- Source Code Pro für sämtliche Texte und Bedienelemente.
- Alle Elemente eckig, Radius immer 0 px.
- Keine Schatten, Verläufe, dekorativen Karten oder Statuskapseln.
- Reduzierte Gestaltung mit Typografie, Abständen und feinen Linien.
- Beschriftungen bleiben sichtbar, Fehler stehen am betroffenen Feld.
- Zustände werden benannt und zusätzlich durch Farbe gekennzeichnet.
- Aktionen stehen rechts; die Hauptaktion rechts von der Alternative.

Eingabefelder verwenden ausschließlich eine Unterlinie, keinen umlaufenden
Rahmen. Die Referenz zeigt leere, befüllte, ungültige und deaktivierte Felder.
Der Tastaturfokus bleibt sichtbar.

Der aktuelle Auftrag betrifft den Styleguide. Setup, Anmeldung und andere
vorhandene App-Seiten werden erst nach Abnahme darauf umgestellt.

## Farben aus der Shell

Die gelesene Ghostty-Konfiguration setzt keine eigene Palette. Die Farben
stammen aus dem mit der installierten App ausgelieferten Theme
`Ghostty Default Style Dark` und entsprechen dem bereitgestellten Screenshot.
Die Shell-Konfiguration selbst bleibt unverändert.

| Rolle | Wert |
|---|---|
| Hintergrund | `#282C34` |
| Schrift | `#FFFFFF` |
| Nebenzeile | `#C4C8C6` |
| Linie | `#666666` |
| Aktion und Fokus | `#82A2BE` |
| Erfolg | `#B6BD68` |
| Warnung | `#F0C674` |
| Fehlerlinie | `#CC6566` |
| Fehlertext | `#E18B8C` |
| Information | `#B294BB` |

Die Farbrollen liegen zentral in `_tokens.sass`. Aktionsknöpfe verwenden
blaue Flächen mit dunkler Schrift. Farbige Statuswörter stehen direkt auf
dem dunklen Hintergrund. Das Shell-Rot erreicht als kleine Schrift nur
3,75:1 Kontrast. Fehlertexte verwenden deshalb ein aufgehelltes Rot aus
derselben Farbfamilie. Fehlerstatus, Feldfehler und Fehlerhinweise werden
rot geschrieben; die Linien behalten das Shell-Rot.
Aktive Eingaben und alternative Knöpfe haben gut erkennbare Konturen;
das dunklere Grau bleibt für dekorative Trennlinien und deaktivierte Elemente.

## Typografie und Layout

Source Code Pro wird als WOFF2 von derselben App ausgeliefert, ohne externe
Font-Dienste. Verwendet werden Regular und Bold, ohne kursive Titel.

| Größe | Einsatz |
|---|---|
| 28 px, fett | Seitentitel |
| 18 px, fett | Abschnitt |
| 15 px | Lesetext, Eingaben und Aktionen |
| 10 px | Nebenzeilen, Beschriftungen und Tabellen |

Die Referenz nutzt höchstens 1120 px Inhaltsbreite und behält auch bei
geöffneten Entwicklerwerkzeugen 32 px Außenabstand. Abstände folgen dem
4-Punkte-Raster. Es gibt weder Hellmodus noch mobile Breakpoints.

Listen werden als gruppierte Zeilen sowie mit Aufzählungszeichen und
Nummerierung gezeigt. Logausgabe und breite Projekttabelle demonstrieren
vertikales und horizontales Scrollen, ohne die ganze Seite zu verbreitern.
Scrollbereiche sind benannt und über die Tastatur erreichbar.

## Herkunft und Dateien

Die ursprüngliche Kopie von Moniques Vorspiel-Styleguide diente als Ausgangspunkt
für die Bausteinbeispiele. Dessen Vereinsgestaltung und fachliche Sonderbausteine
sind durch die oben festgelegte Gestaltung ersetzt. Das Quellprojekt bleibt
unverändert; es gibt keine Laufzeitabhängigkeit davon.

Source Code Pro stammt aus [Adobes offiziellem Repository](https://github.com/adobe-fonts/source-code-pro),
Stand `803b7e23ec97ae58b6232ea76519a76d428ba268`.
Die unveränderte Lizenz liegt in `src/nginx_conf/static/fonts/OFL.txt`.

- `src/nginx_conf/styles/_tokens.sass`: Farbrollen und Schrift.
- `src/nginx_conf/styles/_components.sass`: allgemeine Bausteine.
- `src/nginx_conf/styles/styleguide.sass`: Layout der Referenz.
- `src/nginx_conf/static/styleguide.css`: kompilierte CSS-Datei.
- `src/nginx_conf/static/fonts/`: lokal ausgelieferte Schrift und Lizenz.
- `src/nginx_conf/templates/styleguide.html`: Beispiele ohne Serveraktionen.

Nach Sass-Änderungen mit dem lokal installierten Dart Sass kompilieren:

```bash
sass --no-source-map --style=compressed src/nginx_conf/styles/styleguide.sass src/nginx_conf/static/styleguide.css
```

Quellen und erzeugte CSS-Datei gemeinsam prüfen und versionieren.
Zur Laufzeit wird kein Sass benötigt. Öffentlich abrufbar sind ausschließlich
die CSS-Datei und die beiden Schriftdateien. Die Referenzseite bleibt geschützt.
