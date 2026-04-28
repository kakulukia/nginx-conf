# AGENTS.md

## Kommunikation

- Antworte direkt und kompakt.
- Keine Hoeflichkeitsfloskeln.
- Keine Wiederholung der Frage.
- Nutze Stichpunkte, wenn sie die Antwort klarer machen.
- Widersprich aktiv, wenn Annahmen riskant, falsch oder unvollstaendig sind.
- Stelle bei fachlichen Einwaenden Rueckfragen, bevor du riskante Aenderungen umsetzt.

## Projektkontext

- Lies vor inhaltlichen oder technischen Aenderungen die Datei `TODO.md`.
- Behandle `TODO.md` als aktuelle Feature-, Architektur- und MVP-Planung fuer dieses Projekt.
- Wenn Anforderungen oder Entscheidungen vom aktuellen `TODO.md` abweichen, aktualisiere `TODO.md` mit.
- Fuehre keine groesseren Architekturentscheidungen ein, ohne sie mit `TODO.md` abzugleichen.

## Arbeitsweise

- Halte Aenderungen klein und nachvollziehbar.
- Bevorzuge vorhandene Projektentscheidungen gegenueber neuen Abstraktionen.
- Keine freie Shell-Ausfuehrung als Webfeature planen oder implementieren.
- Sicherheitsrelevante Funktionen muessen mit klaren Allowlists, Pfadvalidierung und minimalen Rechten umgesetzt werden.
- Nginx bleibt ein Systemdienst; PM2 ist nur fuer verwaltete Webapps vorgesehen.
