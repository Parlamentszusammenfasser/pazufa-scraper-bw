[← Index](../design_decisions.md)

# DD-070: TOPs aus der Plenar-Tagesordnung, Drucksachen über den PDF-Hash verknüpft (GitHub Issue #66)

**Datum:** 17.10.2026

**Kontext:** `Sitzung.tops` war immer `[]`. Seit DD-068 erscheinen geplante Lesungen nicht mehr
als Station; sie sollen als TOP der Plenarsitzung im Kalender stehen (DD-069, Entscheidung 2).
Geprüft am 17.10.2026:

- `/de/aktuelles/tagesordnungen` verlinkt nur die Tagesordnung der **nächsten** Plenarsitzung,
  etwa `/resource/blob/…/2026-10-21_14_Plenarsitzung.pdf`. Der Dateiname nennt Datum und
  Sitzungsnummer. Ausschuss-Tagesordnungen werden nicht veröffentlicht (DD-064).
- Das PDF ist zweispaltig (Titel | Redezeiten). kreuzberg (`extract_pdf_text`) gibt die Spalten
  nacheinander aus, ein TOP beginnt mit „N.“ am Zeilenanfang.
- Das Backend verknüpft einen TOP nur über ein gemeinsames Dokument mit einem Vorgang
  (`top_by_id`, `tops_doks`). `insert_top` legt jedes TOP-Dokument über `insert_dokument` an:
  genau ein Treffer über Hash, `api_id` oder `drucksnr` + `typ` + `zp_referenz` → verknüpft,
  **nichts überschrieben**; sonst neue Zeile (DD-069, Befund 8). Erst ein späterer Merge des
  Vorgangs (`execute_merge_dokument`) überschreibt die Felder.
- Der SHA-256 der Drucksache über `_construct_drucksache_pdf_url` gleicht dem Hash, den der
  Vorgänge-Scraper auf Staging hochgeladen hat (18/369, 18/659, 18/692, 18/700).

**Entscheidung:**

1. **Quelle:** Die Tagesordnungen-Seite, einmal je Lauf in `listing_page_extractor`. Eine
   Tagesordnung zählt nur, wenn ihr Datum im ICS-Feed eine Plenarsitzung hat. Sie ergänzt deren
   Sitzung im selben `PUT` (DD-069). Liefert der Feed keine `nummer`, kommt sie aus dem
   Dateinamen (schließt DD-064, „Nicht umgesetzt“).
2. **TOP:** `nummer` = Punkt der Tagesordnung, `titel` = sein erster Absatz, Silbentrennung
   behoben (`normalize_volltext`). Ein Punkt beginnt nur mit der nächsten erwarteten Nummer, so
   startet „3. Oktober“ im Text keinen neuen.
3. **Dokumente nur für Gesetzentwurf-TOPs** (Text enthält „Gesetzentw“), dann alle ihre
   Drucksachen, also auch die Beschlussempfehlung. Anträge u. a. scrapt der Vorgänge-Scraper
   nicht (`enabled-vorgangstypen`); ihr Dokument hätte keinen Vorgang. „Drucksache 18/........“
   (noch nicht gedruckt) entfällt.
4. **Verknüpfung über den PDF-Hash:** Die Drucksache wird geladen und gehasht wie in
   `extract_pdf_text`. Die übrigen Felder sind ein Platzhalter (`titel` „Drucksache N“,
   `typ` `sonstig`, Datum = Sitzungstermin); sie zählen nur ohne Treffer.
5. **Nur mit LLM-Anreicherung:** Ohne sie tragen die Dokumente des Vorgänge-Scrapers
   Platzhalter-Hashes (DD-048), ein TOP-Dokument träfe nichts. Ohne `LLM_PROVIDER_KEY` gibt es
   TOPs ohne Dokumente.
6. **Sitzungen nach Vorgängen:** Im parallelen Lauf startet der Sitzungen-Scraper erst nach dem
   Ende des Vorgänge-Scrapers (`__main__._sitzungen_after_vorgaenge`). Sonst könnte das
   Platzhalter-Dokument zuerst ankommen und bliebe beim Neuanlegen des Vorgangs stehen.
   Beteiligung läuft weiter parallel. `linearize` lief schon in dieser Reihenfolge.
7. **Cache (DD-066):** Der `sz:`-Wert wird `<Termine>:<Tagesordnung>`; Tage ohne Tagesordnung
   behalten ihren Wert, es gibt keinen Massen-Neu-Upload. Eine geänderte Tagesordnung lädt den
   Tag neu. Eine Tagesordnung, die von der Seite verschwindet (die nächste ist online), lädt den
   Tag **nicht** neu, sonst verlöre er seine TOPs.
8. **Fail-soft:** Ist die Seite oder das PDF nicht lesbar, geht der Kalender ohne TOPs hoch
   (WARNING). Ist eine Drucksache nicht ladbar, fehlt nur ihr Dokument, und der Tag wird nicht
   gecacht (`_retry_next_cycle`).

**Verworfen:**

- *`drucksnr` + `typ` + `zp_referenz`:* Die Tagesordnung nennt weder Typ noch Datum der
  Drucksache. Beides läge nur in PARLIS; der Sitzungen-Scraper müsste PARLIS abfragen.
- *Dokument per `api_id` referenzieren* (`DokumentOrApiId`): Die Dokumente des Vorgänge-Scrapers
  haben keine `api_id` (DD-067), und ein Fehltreffer (`fetch_one`) ließe den ganzen Tag scheitern.
- *Alle Drucksachen verknüpfen:* erzeugt Dokumente ohne Vorgang (Akzeptanzkriterium #66).
- *`Top.vorgang_id` setzen:* Das Backend ignoriert es beim Upload.

**Nicht abgedeckt:**

- Ausschusssitzungen bekommen keine TOPs (keine Quelle, DD-064).
- Ändert sich ein Termin, nachdem die Tagesordnung von der Seite verschwunden ist, lädt der Tag
  ohne TOPs neu. Dasselbe gilt für einen Tag mit nicht ladbarer Drucksache, wenn die Tagesordnung
  vor dem Retry verschwindet.
- Hat der Vorgang die Drucksache noch nicht oder nur mit Platzhalter-Hash (Download-Fehler,
  DD-048), legt das Backend das Platzhalter-Dokument als eigene Zeile an.
- Ein in Vormittag und Nachmittag geteilter Plenartag bekäme die TOPs nur an der ersten Sitzung
  (`shortcut:`; der Feed hat einen Termin je Plenartag).
- Die Seite zeigt nur die aktuelle Fassung; `tops-aend`/`tops-ergz` sind eine geänderte
  Tagesordnung und laden den Tag neu.

**Code:** `bawue/tagesordnung.py` (`agenda_links`, `parse_tops`),
`BawueSitzungenScraper._fetch_agendas`, `_build_tops`, `_drucksache_dokument`, `item_extractor`,
`get_cached_result`, `store_extracted_result`, `_agenda_fingerprint`,
`__main__._sitzungen_after_vorgaenge`

**Tests:** `tests/unit/test_tagesordnung.py`,
`tests/unit/test_bawue_sitzungen_scraper.py::TestIssue66Tops`,
`tests/unit/test_main.py::TestIssue66SitzungenAfterVorgaenge`; Fixture: Tagesordnung der
14. Sitzung vom 21.10.2026 (`tests/fixtures/tagesordnung/`)
