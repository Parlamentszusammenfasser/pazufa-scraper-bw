[← Index](../design_decisions.md)

# DD-014: PARLIS-Suchergebnisse — primär JSON-Kommentare, HTML als Fallback

**Datum:** 01.04.2026

**Kontext:** Die PARLIS-Such-API liefert Antworten als HTML-Seiten. Bei der
Analyse der Antworten wurde festgestellt, dass jede Ergebniszeile zusätzlich
als JSON-Objekt in einem HTML-Kommentar (`<!-- {...} -->`) eingebettet ist.
Diese JSON-Objekte enthalten strukturierte Felder mit stabilen Feldcodes
(z. B. `EWBV10` für Titel, `EWBV02` für Vorgangs-ID), während das HTML
fragmentiertes, XPath-abhängiges Parsen erfordert.

Das JSON-Format ist nicht dokumentiert und erscheint als Debug- oder
Integrationsfeature der PARLIS-Oberfläche. Es kann daher in einer zukünftigen
PARLIS-Version entfernt werden.

**Entscheidung:** Die Funktion `parse_results()` versucht zunächst, alle
JSON-Kommentare aus dem HTML zu extrahieren und daraus `RawVorgang`-Objekte
zu bauen. Nur wenn keine JSON-Kommentare gefunden werden oder diese keine
verwertbaren Vorgänge liefern, wird auf das HTML/XPath-Parsing zurückgefallen.

Das HTML-Parsing bleibt vollständig erhalten und dient als Fallback, um
Regressionssicherheit zu gewährleisten, falls das JSON-Format künftig entfernt
wird.

**Implementierung:** `parlis_parser.py`, Funktion `parse_results()` —
ruft `_extract_json_comments()` und `_parse_results_from_json()` auf,
fällt bei leerem Ergebnis auf `_parse_results_from_html()` zurück.
