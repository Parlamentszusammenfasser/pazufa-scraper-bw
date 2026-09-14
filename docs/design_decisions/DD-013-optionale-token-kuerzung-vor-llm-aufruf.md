[← Index](../design_decisions.md)

# DD-013: Optionale Token-Kürzung vor LLM-Aufruf

> **Status: AUFGEHOBEN (13.07.2026, ersetzt durch [DD-029](DD-029-keine-token-kuerzung-dokumentkontext-im-llm-prompt.md)).
**
> Die Token-Kürzung war eine Mitursache von Issue #32: Bei einem Plenarprotokoll
> mit mehreren Tagesordnungspunkten schnitt das 12 000-Token-Limit die relevante
> Debatte (Fischereigesetz, Drucksache 17/529) mitten heraus und behielt eine
> unbeteiligte Debatte vollständig — die Zusammenfassung beschrieb daraufhin das
> falsche Thema. Der Parameter `truncate-tokens`, die Funktion `truncate_text()`
> und die zugehörige Konfiguration wurden vollständig entfernt. Der folgende Text
> dokumentiert nur noch die historische Entscheidung.

**Datum:** 01.04.2026

**Kontext:** Der Scraper extrahiert Volltext aus PDFs (via OCR oder direkter
Textextraktion) und sendet diesen an ein LLM, um Metadaten wie den Dokumententyp
zu bestimmen. Gesetzgebungsdokumente können mehrere zehntausend Tokens umfassen,
was die Kosten pro Aufruf erheblich erhöht. Da Titel, Typ und Zweck eines
Dokuments in der Regel im Kopfbereich stehen, ist der vollständige Textkörper für
die Klassifikation meist nicht erforderlich.

**Entscheidung:** Das `[llm]`-Konfigurationsabschnitt unterstützt einen optionalen
Parameter `truncate-tokens`. Ist er auf einen Wert > 0 gesetzt, wird der Volltext
vor dem LLM-Aufruf auf maximal diese Anzahl Tokens gekürzt. Der Wert 0 deaktiviert
die Kürzung (kein Limit). Standardwert: 12 000 Tokens.

Die Kürzung erfolgt durch echtes Token-Encoding (via `litellm`) für das jeweilige
Modell, nicht durch zeichenbasiertes Abschneiden — der Grenzwert ist damit
modellgenau.

Auf der Staging-Umgebung und in Entwicklungsläufen ist die Kürzung aktiviert
(12 000 Tokens), um Kosten zu begrenzen. Für die Produktionsumgebung ist vorgesehen,
die Kürzung zu deaktivieren (`truncate-tokens = 0`), um die volle Textqualität
zu nutzen.

**Implementierung:** `bawue_dok.py`, Funktion `truncate_text()`. Aufgerufen in
`enrich_document()` vor der Prompt-Zusammenstellung. Konfiguration über
`llm_config.get("truncate-tokens", 12000)` in `bawue_vorgaenge_scraper.py` und
`bawue_beteiligung_scraper.py`.
