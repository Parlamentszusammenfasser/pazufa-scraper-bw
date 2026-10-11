[← Index](../design_decisions.md)

# DD-067: Kein Platzhalter-Dokument ohne `drucksnr` nach einem Download-Fehler

**Datum:** 10.10.2026

**Kontext:** Scheitert der PDF-Download, ging das Dokument bisher mit Platzhalter-`hash_` hoch
([DD-048](DD-048-link-abgeleiteter-platzhalter-hash-statt-geteiltem-todo.md)). Typischer Fall: ein Plenarprotokoll, das erst Wochen nach der Sitzung
erscheint und bis dahin HTTP 404 liefert. Backend v0.3.3 matcht Dokumente über Hash +
`subdoc_id`, `api_id` oder (`drucksnr`, `typ`, `zp_referenz` ±12 h). Plenarprotokolle und
Gesetzblätter haben keine `drucksnr`, eine Dokument-`api_id` senden wir nicht. Gelingt der
Download später, trifft der Inhalts-Hash keinen Row, und das Backend legt einen neuen an.
`execute_merge_station` fügt Dokumente nur hinzu und entfernt keine; der Platzhalter bleibt an
der Station.

**Nachweis (10.10.2026):** Staging V-247045, Erste Beratung 23.07.2026 (Plenarprotokoll
18/10): drei Rows, `TODO` ohne Anker, Platzhalter mit `#page=31`, echtes Dokument. PROD
V-225520 (17/66) und V-225432 (17/63): je Platzhalter und echtes Dokument mit gleichem Link.
Dort war das PDF längst veröffentlicht, der Download scheiterte vorübergehend.

**Entscheidung:** Scheitert der Download und hat das Dokument keine `drucksnr`, gibt
`_build_dokumente` kein Dokument zurück. Der Vorgang bleibt wie bisher unge-cacht (Issue #66);
der nächste Lauf versucht es erneut und legt dann den einzigen Row an. Hatte die Station das
Dokument schon, bleibt es verlinkt. Drucksachen behalten den Platzhalter: Der `drucksnr`-Zweig
aktualisiert ihren Row in place, solange nur ein Row passt.

**Verworfen — Dokument-`api_id` je Vorgang und Fundstelle:** Gemeinsam beratene Vorgänge
zitieren dieselbe Protokollseite (PROD: über 40 geteilte Protokoll-Rows) und senden nach der
Veröffentlichung denselben Inhalts-Hash. Der zweite träfe dann seinen Row über die `api_id`
und den des ersten über den Hash: `AmbiguousMatch`, HTTP 409 für den ganzen Vorgang, in jedem
Lauf. Außerdem speichert das Backend eine gesendete `api_id` nur beim Einfügen.

**Nicht abgedeckt:**

- Gemeinsame Beratung: Der zweite Vorgang bekommt das Protokoll nicht verlinkt. Das Backend
  merged das Dokument in den Row des ersten, verlinkt einen Match aber nur beim Einfügen einer
  Station (`insert_or_merge_dok` gibt dann keine Id zurück). Backend-Fehler; vorher blieb dort
  der veraltete Platzhalter.
- Andere Hash-Wechsel ohne `drucksnr` lassen den alten Row weiter stehen: leere Extraktion
  oder Ausnahme in der Anreicherung (Platzhalter, kein erneuter Versuch), LLM-Anreicherung
  nachträglich eingeschaltet, Anker erst nach der Veröffentlichung (nicht beobachtet; bei 18/10
  kam `S. 287–291` noch vor dem PDF).
- Der Beteiligungsportal-Scraper wertet `download_failed` nicht aus; seine Dokumente haben
  ebenfalls keine `drucksnr`.
- Bis zur Veröffentlichung hat die Station kein Dokument (vorher: `TODO`-Volltext, toter Link).

**Migrationshinweis:** Bestehende Platzhalter-Rows bleiben verlinkt und werden einmalig von
Hand gelöscht (`DELETE /api/v2/dokument/{api_id}`, entfernt auch die Verlinkung). Erst nach dem
Deploy: Der alte Scraper lädt Vorgänge mit fehlendem PDF in jedem Lauf neu hoch und legt einen
gelöschten Platzhalter wieder an. Liste im PR.

**Code:** `_build_dokumente`

**Tests:** `tests/unit/test_bawue_scraper.py::TestBuildVorgang::test_unpublished_protocol_is_not_uploaded`
