[← Index](../design_decisions.md)

# DD-007: Beteiligungsportal — nur Prozesse mit Entwurf-PDFs

**Datum:** 27.03.2026

**Kontext:** Das Beteiligungsportal Baden-Württemberg enthält neben
Gesetzgebungsverfahren auch rein informatorische Inhalte (z. B.
„Klima-Maßnahmen-Register 2026"), die keine vorparlamentarischen Initiativen
darstellen.

**Entscheidung:** Es werden nur Beteiligungsprozesse übernommen, die mindestens
einen PDF-Link enthalten und keine Verordnung sind (siehe Nachtrag). Prozesse
ohne PDFs werden mit Info-Log übersprungen.

Das Portal rendert PDF-Downloads in zwei Markups: `a.link-download-block` und
`a.link-list__link` (Link-Liste). Beide werden ausgewertet, Duplikate per URL
entfernt. Gezählt werden nur PDFs, die auf dem Portal selbst gehostet sind:
Extern verlinkte PDFs (z. B. EU-Verordnungsvorschläge auf `esf-bw.de` bei
„Europäischer Sozialfonds") sind Hintergrundinformationen, keine Landesentwürfe
(GitHub-Issue #45).

**Nachtrag (GitHub Issue #71, 10.10.2026):** Ein PDF allein belegt keinen
Gesetzentwurf. Rechtsverordnungen der Landesregierung gehen nie durch den Landtag,
wurden aber als `gg-land-parl` hochgeladen („Mietpreisbegrenzung", PROD und Staging).
Übersprungen (Summary: „Skipped") wird ein Prozess, dessen Phasen-Timeline
„Beschluss der geltenden Verordnung(en)" enthält; Gesetz-Seiten tragen diese Phase
nie. Auf 135 Portalseiten (LP 17 + 18) traf das alle 17 Verordnungen und keinen
Gesetzentwurf. PDF-Titel taugen nicht: 5 der 17 beginnen nicht mit „Verordnung…",
und ein Gesetz-PDF ohne „Gesetz" im Titel wäre verloren gegangen. Ohne Timeline
bleibt der Prozess erhalten.

**Implementierung:** `beteiligung_parser.py`, Funktion `parse_process_detail()` —
Link-Extraktion und Host-Filter; `bawue_beteiligung_scraper.py`, Methode
`_build_vorgang()` — Prüfung auf `detail.pdf_links` und `detail.is_verordnung` (Issue #71,
auch im Dry-Run).
