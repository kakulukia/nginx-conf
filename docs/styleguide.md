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

Anmeldung, Projektübersicht, Config-Editor und Logs verwenden dieselben
Komponenten wie die Referenz. Das gemeinsame Seitenlayout liegt in `base.html`,
das App-Layout in `app.sass`. Beide Sass-Einstiegspunkte laden `_components.sass`.
Die reguläre Anmeldung enthält Passwort und TOTP-Code. Beim ersten Login
folgt nach der Passwortprüfung die Authenticator-Einrichtung mit QR-Code,
optionalem manuellen Schlüssel und Code-Bestätigung. Beide Ansichten sind
ohne Kopfzeile vertikal zentriert. Fehlversuche werden rot angezeigt;
die Eingaben bleiben nach dem Absenden leer.
Nutzername, Setup- und Login-Token entfallen.

Login, Authenticator-Einrichtung und Styleguide teilen denselben TOTP-Baustein.
Sechs sichtbare Stellen mit jeweils 44 × 48 px und 8 px Abstand liegen hinter einem nativen
Eingabefeld. Die Ziffern sind 24 px groß, die Beschriftung bleibt 10 px.
Unterlinien und eckige Fokusflächen folgen den übrigen Eingaben. Leere Stellen
haben keinen Platzhalter. Fehler färben die Unterlinien und den Hinweis rot;
deaktivierte Ziffern sind dunkelgrau, ihre Unterlinien gestrichelt.

Der Code bleibt ein zusammenhängender Wert mit einem Tab-Stopp. Ein Klick auf
eine befüllte Stelle setzt die Einfügeposition hinter deren Ziffer, damit
Backspace genau diese Ziffer löscht. Die stärkere Hintergrundfläche markiert
die Stelle vor der Einfügeposition und bleibt auch an der sechsten Stelle
sichtbar. Der Strichcursor wird bei aktiver JavaScript-Erweiterung ausgeblendet.
Textauswahl, Pfeiltasten und Löschen behalten ihr natives Verhalten; eine
Auswahl hebt die betroffenen Stellen gemeinsam hervor. Die Beispiele
akzeptieren sechs ASCII-Ziffern, erhalten führende Nullen und bereinigen beim
Einfügen beispielsweise `012 345` zu `012345`. `autocomplete="one-time-code"`
erlaubt die Code-Übernahme durch den Browser. Das Feld übermittelt genau einen
Wert namens `totp_code`; die sechs sichtbaren Stellen sind dekorativ.
In beiden Anmeldeformularen ist ein vollständiger Code erforderlich.

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
| Leere Dropdown-Auswahl | 60 % Nebenzeilengrau, 40 % Hintergrund |
| Linie | `#666666` |
| Aktion | `#82A2BE` |
| Fokusfläche für Eingaben | 8 % Aktionsblau, 92 % Hintergrund |
| Tastaturrahmen anderer Bedienelemente | 67 % Aktionsblau, 33 % Hintergrund |
| Erfolg | `#B6BD68` |
| Warnung | `#F0C674` |
| Fehlerlinie | `#CC6566` |
| Fehlertext | `#E18B8C` |
| Information | `#B294BB` |

Die Farbrollen liegen zentral in `_tokens.sass`. Aktionsknöpfe verwenden
farbige Schrift auf transparentem Grund. Farbige Statuswörter stehen direkt
auf dem dunklen Hintergrund. Das Shell-Rot erreicht als kleine Schrift nur
3,75:1 Kontrast. Fehlertexte verwenden deshalb ein aufgehelltes Rot aus
derselben Farbfamilie. Fehlerstatus, Feldfehler und Fehlerhinweise werden
rot geschrieben; die Linien behalten das Shell-Rot.
Aktive Eingaben haben eine gut erkennbare Unterlinie; das dunklere Grau
bleibt für dekorative Trennlinien und deaktivierte Elemente.

Für Eingabefelder und Textareas ist Variante D festgelegt: eine 1 px breite
Unterlinie in Aktionsblau und eine dezent gefärbte Fläche bei Fokus, ohne
äußeren Fokusrahmen. Die Fokusfläche erhält links und rechts 12 px
Innenabstand, auch bei Dropdowns und Autocomplete. Ohne Fokus bleiben die
Texte bündig zum Fließtext. Innenabstand, Fokusfläche und Linienfarbe wechseln
mit einer 180 ms langen Animation. Bei reduzierter Bewegung entfällt sie.
Bei ungültigen Eingaben bleibt die Unterlinie rot.
Andere Bedienelemente behalten für die Tastaturbedienung den dünnen,
gedämpften Fokusrahmen.

Die Textarea im Styleguide ist bearbeitbar, vertikal vergrößerbar und auch
per Tastatur erreichbar. Ihr Beispieltext wird nicht gespeichert und löst
keine Serveraktion aus. Lange Inhalte scrollen innerhalb des Feldes.

Dropdowns verwenden native `select`-Felder mit gestaltbarem Auswahlmenü,
sofern der Browser `base-select` unterstützt. Sonst bleibt das native Menü.
Solange das Dropdown geöffnet ist, bleiben Fokusfläche, Innenabstand und
blaue Unterlinie erhalten, auch wenn der Fokus in der Optionsliste liegt.
Die Liste öffnet direkt darunter und weicht nur bei Platzmangel nach oben aus.
„Bitte auswählen“ erscheint dunkelgrau, solange kein Wert gewählt ist.
Ausgewählte Werte in aktiven Feldern bleiben weiß. Deaktivierte Dropdowns
verwenden dasselbe Dunkelgrau für Schrift und Ausklapppfeil.
Das Autocomplete filtert lokale Projektvorschläge und erlaubt freie Eingaben.
Der erste passende Vorschlag ist beim Öffnen und nach jeder Eingabe markiert.
Enter übernimmt ihn direkt, auch bei mehreren Treffern. Mit Pfeiltasten und
Maus lassen sich andere Vorschläge wählen. Escape und Verlassen des Feldes
schließen die Liste. Ohne Treffer erscheint ein
Hinweis. Ohne JavaScript bleibt das native `datalist` als Rückfall erhalten.
Beide Felder übernehmen Unterlinie, Fokusfläche und Schrift der übrigen
Felder. Die Referenz zeigt leere, ausgewählte und deaktivierte Zustände.
Es werden keine zusätzlichen Bibliotheken oder Serveranfragen benötigt.

Für Buttons ist Variante C mit einheitlich transparentem Grund festgelegt.
Speichern erscheint grün, Abbrechen weiß und Deaktivieren rot, jeweils ohne
sichtbaren Rahmen. Bei Hover erhalten aktive Buttons eine dezente Fläche;
Speichern bleibt dabei grün und gefährliche Aktionen behalten die rote Schrift. Deaktivierte Buttons
zeigen eine schwache graue Fläche und normale Schriftstärke. Der dünne
Tastaturfokus bleibt erhalten. Die Beispiele lösen keine Serveraktionen aus.

## Typografie und Layout

Source Code Pro wird als WOFF2 von derselben App ausgeliefert, ohne externe
Font-Dienste. Verwendet werden Regular und Bold, ohne kursive Titel.

| Größe | Einsatz |
|---|---|
| 28 px, fett | Seitentitel |
| 18 px, fett | Abschnitt |
| 15 px | Lesetext, Eingaben, Aktionen und Tabellen |
| 15 px, fett, weiß (`#FFF`) | Tabellenüberschriften |
| 10 px | Nebenzeilen und Beschriftungen |

Die Referenz nutzt höchstens 1120 px Inhaltsbreite und behält auch bei
geöffneten Entwicklerwerkzeugen 32 px Außenabstand. Abstände folgen dem
4-Punkte-Raster. Es gibt weder Hellmodus noch mobile Breakpoints.

Listen werden als gruppierte Zeilen sowie mit Aufzählungszeichen und
Nummerierung gezeigt. Logausgabe und breite Projekttabelle demonstrieren
vertikales und horizontales Scrollen, ohne die ganze Seite zu verbreitern.
Scrollbereiche sind benannt und über die Tastatur erreichbar.
Browserfenster und innere Scrollbereiche verwenden dieselbe 8 px schmale,
eckige Scrollbar mit hellem Schieber auf dunklem Grund.

Aufklappelemente öffnen und schließen mit einer 180 ms langen Höhen- und
Deckkraftanimation. Der Pfeil dreht sich gleichzeitig um 90 Grad.
Die Abstände liegen im inneren Inhaltswrapper. Bei reduzierter Bewegung
entfallen die Animationen. Browser ohne die erforderliche CSS-Unterstützung
zeigen den Inhalt ohne Höhenanimation; die native Bedienung bleibt erhalten.
Die Umsetzung nutzt [`::details-content`](https://developer.chrome.com/blog/styling-details)
ohne zusätzliches JavaScript.

## Abläufe und ergänzende Bausteine

Die Referenz zeigt laufende Aktionen sowie erfolgreiche und fehlgeschlagene
Ergebnisse. Ergebnisbanner bleiben bis zum Quittieren sichtbar. Der ganze
Banner ist per Maus oder Tastatur quittierbar; seine Ausgabe lässt sich
unabhängig davon aufklappen. Die Wiederherstellung betrifft nur die Beispiele.
Der Hover hinterlegt den ganzen Ergebnisbanner, auch über der Ausgabefläche.

Diffs zeigen Hinzufügungen grün mit `+` und entfernte Zeilen rot mit `-`.
Die Zusammenfassung verwendet ein Zeichen pro geänderter Zeile.
Für Screenreader bleibt die Anzahl als Text hinterlegt.
Leere Projekt- und Logansichten bieten einen passenden nächsten Schritt.
Eine Beispielnavigation zeigt aktive Einträge und einen Rückweg vom Editor.
Normale Links nutzen Warnungsgelb (`#F0C674`) und werden beim Darüberfahren
unterstrichen. Die gelbe Farbe bleibt dabei erhalten.
Ein Kettensymbol zeigt einen Direktlink mit zugänglicher Beschriftung und
32 px Klickfläche. Beide Linkformen nutzen den dezenten Tastaturfokus.
Domain, Pfad und der einmalige Installationsbefehl lassen sich kopieren; Erfolg und Fehler werden
als Text zurückgemeldet. Der Befehl wird dabei ausschließlich kopiert.

Der Bestätigungsdialog nutzt das native `dialog`-Element. Er nennt genau die
betroffene Datei, Folgen und Änderungsumfang. Abbrechen erhält beim Öffnen
den Fokus und steht links von der roten Bestätigung; beide Aktionen stehen
rechts. Escape schließt den Dialog. Die darunterliegende Seite bleibt
währenddessen gesperrt; nach dem Schließen kehrt der Fokus zum Auslöser zurück. Die Bestätigung im
Styleguide ändert ausschließlich die lokale Rückmeldung, keine Config-Datei.

## Favicon

Das Favicon verwendet die gewählten geschweiften Klammern in Aktionsblau
(`#82A2BE`), mit transparentem Hintergrund und 2,5 px Strichstärke im
24-Punkte-Raster. Es ist als SVG auf allen Seiten eingebunden, einschließlich
Anmeldung. Die Datei wird lokal und ohne Anmeldung ausgeliefert.

## Herkunft und Dateien

Die ursprüngliche Kopie von Moniques Vorspiel-Styleguide diente als Ausgangspunkt
für die Bausteinbeispiele. Dessen Vereinsgestaltung und fachliche Sonderbausteine
sind durch die oben festgelegte Gestaltung ersetzt. Das Quellprojekt bleibt
unverändert; es gibt keine Laufzeitabhängigkeit davon.

Source Code Pro stammt aus [Adobes offiziellem Repository](https://github.com/adobe-fonts/source-code-pro),
Stand `803b7e23ec97ae58b6232ea76519a76d428ba268`.
Die unveränderte Lizenz liegt in `src/nginx_conf/static/fonts/OFL.txt`.

Das Symbol basiert auf [Lucide „braces“](https://lucide.dev/icons/braces).
Die ISC-Lizenz ist direkt in `src/nginx_conf/static/favicon.svg` enthalten.

- `src/nginx_conf/static/favicon.svg`: geschweifte Klammern als Favicon.
- `src/nginx_conf/styles/_tokens.sass`: Farbrollen und Schrift.
- `src/nginx_conf/styles/_components.sass`: allgemeine Bausteine.
- `src/nginx_conf/styles/styleguide.sass`: Layout der Referenz.
- `src/nginx_conf/styles/app.sass`: Layout der App und Anmeldung.
- `src/nginx_conf/static/app.css`: kompilierte App-CSS-Datei.
- `src/nginx_conf/static/styleguide.css`: kompilierte CSS-Datei.
- `src/nginx_conf/static/fonts/`: lokal ausgelieferte Schrift und Lizenz.
- `src/nginx_conf/templates/styleguide.html`: Beispiele ohne Serveraktionen.
- `src/nginx_conf/templates/_otp.html`: wiederverwendbarer TOTP-Baustein.
- `src/nginx_conf/static/otp.js`: gemeinsame Zifferneingabe und Positionsanzeige.

Nach Sass-Änderungen mit dem lokal installierten Dart Sass kompilieren:

```bash
sass --no-source-map --style=compressed src/nginx_conf/styles/styleguide.sass src/nginx_conf/static/styleguide.css
sass --no-source-map --style=compressed src/nginx_conf/styles/app.sass src/nginx_conf/static/app.css
```

Quellen und erzeugte CSS-Datei gemeinsam prüfen und versionieren.
Zur Laufzeit wird kein Sass benötigt. Öffentlich abrufbar sind ausschließlich
das Favicon, beide CSS-Dateien, die TOTP-JavaScript-Datei und die beiden Schriftdateien. Die Referenzseite
bleibt geschützt.
