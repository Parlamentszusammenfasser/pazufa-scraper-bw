[← Index](../design_decisions.md)

# DD-007: Beteiligungsportal — nur Prozesse mit Entwurf-PDFs

**Datum:** 27.03.2026

**Kontext:** Das Beteiligungsportal Baden-Württemberg enthält neben
Gesetzgebungsverfahren auch rein informatorische Inhalte (z. B.
„Klima-Maßnahmen-Register 2026"), die keine vorparlamentarischen Initiativen
darstellen.

**Entscheidung:** Es werden nur Beteiligungsprozesse übernommen, die mindestens
einen PDF-Link enthalten. Das Vorhandensein eines PDFs ist ein hinreichendes
Indiz für einen tatsächlichen Gesetzentwurf. Prozesse ohne PDFs werden mit
Info-Log übersprungen.

Das Portal rendert PDF-Downloads in zwei Markups: `a.link-download-block` und
`a.link-list__link` (Link-Liste). Beide werden ausgewertet, Duplikate per URL
entfernt. Gezählt werden nur PDFs, die auf dem Portal selbst gehostet sind:
Extern verlinkte PDFs (z. B. EU-Verordnungsvorschläge auf `esf-bw.de` bei
„Europäischer Sozialfonds") sind Hintergrundinformationen, keine Landesentwürfe
(GitHub-Issue #45).

**Nachtrag (GitHub Issue #71, 10.10.2026):** Ein PDF allein belegt keinen
Gesetzentwurf. Rechtsverordnungen der Landesregierung (z. B. „Mietpreisbegrenzung",
„Rettungsdienstplanverordnung") gehen nie durch den Landtag und wurden trotzdem als
`gg-land-parl` hochgeladen. Übersprungen (gezählt unter „Skipped") wird ein Prozess,
wenn ein PDF-Titel mit „Verordnung…" bzw. „Entwurf einer Verordnung" beginnt und kein
anderer PDF-Titel „Gesetz" enthält. Ohne Verordnungs-Titel bleibt der Prozess erhalten,
damit ein unklarer Titel nie einen Gesetzentwurf verwirft. Der Volltext wird nicht
geprüft: er liegt nur mit LLM vor, und die Titel genügten auf allen LP-18-Seiten.

**Implementierung:** `beteiligung_parser.py`, Funktion `parse_process_detail()` —
Link-Extraktion und Host-Filter; `bawue_beteiligung_scraper.py`, Methode
`_build_vorgang()` — Prüfung auf `detail.pdf_links`; `_is_verordnung_only()` (Issue #71).
