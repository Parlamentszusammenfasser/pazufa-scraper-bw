[← Index](../design_decisions.md)

# DD-070: TOPs aus der Plenar-Tagesordnung, Drucksachen über den hochgeladenen Hash verknüpft (GitHub Issue #66)

**Datum:** 17.10.2026

**Kontext:** `Sitzung.tops` war immer `[]`. Seit DD-068 erscheinen geplante Lesungen nicht mehr
als Station; sie sollen als TOP der Plenarsitzung im Kalender stehen (DD-069, Entscheidung 2).
Geprüft am 17.10.2026 (Backend `main` `f87fd72`):

- `/de/aktuelles/tagesordnungen` verlinkt nur die Tagesordnung der **nächsten** Plenarsitzung,
  etwa `/resource/blob/…/2026-10-21_14_Plenarsitzung.pdf`. Der Dateiname nennt Datum und
  Sitzungsnummer. Ausschuss-Tagesordnungen werden nicht veröffentlicht (DD-064).
- Das PDF ist zweispaltig (Titel | Redezeiten). kreuzberg (`extract_pdf_text`) gibt die Spalten
  nacheinander aus. Unter Linux (CI, Docker, Prod) stehen zwischen den Absätzen **keine**
  Leerzeilen, unter macOS schon.
- Das Backend verknüpft einen TOP nur über ein gemeinsames Dokument mit einem Vorgang
  (`top_by_id`, `tops_doks`). `insert_top` legt jedes TOP-Dokument über `insert_dokument` an:
  genau ein Treffer über Hash, `api_id` oder `drucksnr` + `typ` + `zp_referenz` → verknüpft,
  **nichts überschrieben**; sonst eine neue Zeile.
- Eine neue Zeile ist gefährlich. Hat der Vorgang die Drucksache nach einem Download-Fehler mit
  Platzhalter-Hash hochgeladen (DD-067), trifft sein nächster Upload mit echtem Hash beide Zeilen:
  `AmbiguousMatch` (`insert_or_merge_dok`), der Vorgang-PUT scheitert bei jedem Lauf. Ohne Zeile
  des Vorgangs übernimmt ein neuer Vorgang die TOP-Zeile; ein späterer Merge überschreibt `typ`
  und `zp_referenz` nie (`execute_merge_dokument`). Gefunden im Review von PR #124.

**Entscheidung:**

1. **Quelle:** Die Tagesordnungen-Seite, einmal je Lauf in `listing_page_extractor`. Eine
   Tagesordnung zählt nur, wenn ihr Datum im ICS-Feed eine Plenarsitzung hat. Sie ergänzt deren
   Sitzung im selben `PUT` (DD-069). Liefert der Feed keine `nummer`, kommt sie aus dem
   Dateinamen (schließt DD-064, „Nicht umgesetzt“); sonst gilt die des Feeds.
2. **TOP:** `nummer` = Punkt der Tagesordnung. `titel` = von Punktbeginn bis zur ersten Zeile
   „– Drucksache …“, sonst die erste Zeile; Silbentrennung behoben (`normalize_volltext`). Das ist
   auf beiden Plattformen gleich. Ein Punkt beginnt nur mit der nächsten erwarteten Nummer ohne
   folgenden Monatsnamen, so startet ein umbrochenes „2. Mai 2020“ keinen neuen.
3. **Dokumente nur für Lesungen eines Gesetzentwurfs** (Titel beginnt mit „Erste/Zweite/Dritte
   Beratung des/der Gesetzentw…“), dann alle ihre Drucksachen, also auch die Beschlussempfehlung.
   Anträge u. a. scrapt der Vorgänge-Scraper nicht (`enabled-vorgangstypen`). „Drucksache
   18/........“ (noch nicht gedruckt) entfällt.
4. **Nur hochgeladene Zeilen referenzieren:** Nach jedem erfolgreichen Upload registriert der
   Vorgänge-Scraper je gelesener Drucksache (`volltext` ≠ `TODO`) ihren Hash in Redis
   (`drucks-hash:<drucksnr>`, `cache.drucksache_hash_key`). Der TOP verwendet nur diesen Hash; das
   Backend trifft damit genau die Zeile des Vorgangs. Die übrigen Felder sind ein Platzhalter.
   Ohne Eintrag fehlt das Dokument, und der Tag wird nicht gecacht (`_retry_next_cycle`). Ohne
   LLM-Anreicherung ist das Register leer (Platzhalter-Hashes, DD-048): TOPs ohne Dokumente.
5. **Keine feste Reihenfolge der Scraper:** Sie laufen weiter parallel. Lädt der Vorgänge-Scraper
   eine Drucksache im selben Lauf erst nach dem TOP hoch, folgt ihr Dokument im nächsten Lauf
   (Retry, Punkt 4). Eine Wartepflicht ließe den Kalender an einem langen oder hängenden
   Vorgänge-Lauf hängen (Review von PR #124).
6. **Cache (DD-066):** Der `sz:`-Wert wird `<Termine>:<Tagesordnung>`; Tage ohne Tagesordnung
   behalten ihren Wert, es gibt keinen Massen-Neu-Upload. Eine geänderte Tagesordnung lädt den
   Tag neu. Eine Tagesordnung, die von der Seite verschwindet (die nächste ist online), lädt den
   Tag **nicht** neu, sonst verlöre er seine TOPs.
7. **Fail-soft:** Ist die Seite oder das PDF nicht lesbar, geht der Kalender ohne TOPs hoch
   (WARNING). Ein Link mit ungültigem Datum wird übersprungen.

**Verworfen:**

- *Drucksache im Sitzungen-Scraper laden und hashen:* erzeugt die gefährliche neue Zeile, sobald
  der Vorgang die Drucksache nicht mit genau diesem Hash hat (Kontext).
- *`drucksnr` + `typ` + `zp_referenz`:* Die Tagesordnung nennt weder Typ noch Datum der
  Drucksache. Beides läge nur in PARLIS.
- *Dokument per `api_id` referenzieren* (`DokumentOrApiId`): Die Dokumente des Vorgänge-Scrapers
  haben keine `api_id` (DD-067), und ein Fehltreffer (`fetch_one`) ließe den ganzen Tag scheitern.
- *Alle Drucksachen verknüpfen:* erzeugt Dokumente ohne Vorgang (Akzeptanzkriterium #66).
- *`Top.vorgang_id` setzen:* Das Backend ignoriert es beim Upload.

**Nicht abgedeckt:**

- Vorgänge, die vor dem Deploy gecacht wurden, registrieren ihre Drucksachen erst beim nächsten
  Upload (neue Fundstelle). Bis dahin fehlen ihre TOP-Dokumente; der Tag wird bis zur Sitzung
  täglich neu versucht.
- Ausschusssitzungen bekommen keine TOPs (keine Quelle, DD-064).
- Ändert sich ein Termin, nachdem die Tagesordnung von der Seite verschwunden ist, lädt der Tag
  ohne TOPs neu. Dasselbe gilt für einen Tag mit fehlender Drucksache, wenn die Tagesordnung vor
  dem Retry verschwindet.
- Ein in Vormittag und Nachmittag geteilter Plenartag bekäme die TOPs nur an der ersten Sitzung
  (`shortcut:`; der Feed hat einen Termin je Plenartag).
- Die Seite zeigt nur die aktuelle Fassung; `tops-aend`/`tops-ergz` sind eine geänderte
  Tagesordnung und laden den Tag neu.

**Code:** `bawue/tagesordnung.py` (`agenda_links`, `parse_tops`),
`BawueSitzungenScraper._fetch_agendas`, `_build_tops`, `_drucksache_dokument`, `item_extractor`,
`get_cached_result`, `store_extracted_result`, `_agenda_fingerprint`,
`BawueVorgaengeScraper.store_extracted_result`, `cache.drucksache_hash_key`

**Tests:** `tests/unit/test_tagesordnung.py` (Linux- und macOS-Extraktion),
`tests/unit/test_bawue_sitzungen_scraper.py::TestIssue66Tops`,
`tests/unit/test_bawue_scraper.py::TestIssue66DrucksacheHashRegistry`; Fixtures: Tagesordnung der
14. Sitzung vom 21.10.2026 (`tests/fixtures/tagesordnung/`)
