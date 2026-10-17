# AGENTS.md

## Kommunikation

- Antworte direkt und kompakt.
- Keine Hoeflichkeitsfloskeln.
- Keine Wiederholung der Frage.
- Nutze Stichpunkte, wenn sie die Antwort klarer machen.
- Widersprich aktiv, wenn Annahmen riskant, falsch oder unvollstaendig sind.
- Stelle bei fachlichen Einwaenden Rueckfragen, bevor du riskante Aenderungen umsetzt.
- Wenn eine vorgeschlagene Umsetzung funktioniert, aber eine naheliegende Standard- oder Best-Practice-Variante leicht besser ist, weise vor der Umsetzung darauf hin und frage nach, sofern nicht explizit direktes Umsetzen verlangt wurde.

## Projektkontext

- Nutze Beads (`bd`) als aktuelle Feature-, Architektur- und MVP-Planung fuer dieses Projekt.
- Beads-Hooks liefern den allgemeinen Workflow-Kontext; falls Kontext fehlt oder stale wirkt, fuehre `bd prime` aus.
- Lies vor inhaltlichen oder technischen Aenderungen das relevante Bead mit `bd show <id>`.
- Lies bei Architektur-, Betriebs- oder Sicherheitsfragen zusaetzlich `docs/technikreferenz.md`.
- Wenn Anforderungen oder Entscheidungen von der Technikreferenz abweichen, aktualisiere `docs/technikreferenz.md` und die betroffenen Beads.
- Fuehre keine groesseren Architekturentscheidungen ein, ohne sie mit Beads und `docs/technikreferenz.md` abzugleichen.

## Arbeitsweise

- Die Oberflaeche ist ausschliesslich fuer Desktop im Dunkelmodus vorgesehen. Keine Hellmodus- oder Mobile-Varianten planen, implementieren oder zur Abnahme verlangen.
- Der eigene Styleguide nutzt Source Code Pro, die Ghostty-Shell-Palette und eckige, minimale Elemente ohne Rundungen, Schatten oder Verlaeufe. Details stehen in `docs/styleguide.md`.

- Halte Aenderungen klein und nachvollziehbar.
- Bevorzuge vorhandene Projektentscheidungen gegenueber neuen Abstraktionen.
- Keine freie Shell-Ausfuehrung als Webfeature planen oder implementieren.
- Sicherheitsrelevante Funktionen muessen mit klaren Allowlists, Pfadvalidierung und minimalen Rechten umgesetzt werden.
- Nginx bleibt ein Systemdienst; PM2 ist nur fuer verwaltete Webapps vorgesehen.
