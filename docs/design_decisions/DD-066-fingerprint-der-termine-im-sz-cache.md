[← Index](../design_decisions.md)

# DD-066: Fingerprint der Termine im `sz:`-Cache — geänderte Sitzungstage werden neu hochgeladen

**Datum:** 10.10.2026

**Kontext:** Der Sitzungen-Cache hatte als Schlüssel nur das Datum (`sz:sha256(datum)`), ohne
TTL. Jeder vorhandene Eintrag galt als Treffer. Der ICS-Feed reicht etwa 14 Monate voraus,
nach dem ersten Lauf ist also jeder Termin gecacht. Danach erreichte keine Änderung mehr das
Backend: weder eine verlegte Sitzung noch ein neuer Termin am selben Tag, ein geänderter
SUMMARY oder eine wieder gelieferte Sitzungsnummer (DD-064). Das ist die erste Beobachtung in
GitHub Issue #85; DD-052 hat dasselbe Problem für `vg2:` gelöst.

**Entscheidung:**

1. **Wie DD-052:** Der Schlüssel bleibt `sz:sha256(datum)`, der Wert ist der Fingerprint der
   Termine dieses Tages. Ein Treffer zählt nur, wenn er dem aktuellen Fingerprint gleicht;
   sonst wird der Tag neu gebaut und per `PUT` hochgeladen.
2. **Gehasht wird, was hochgeladen wird:** je Termin `UID` (→ `api_id`), `DTSTART` (→ `termin`)
   und SUMMARY (→ `titel`, Gremium, `nummer`), sortiert, die Reihenfolge zählt also nicht.
   Nicht gehasht: `DTEND` (nicht hochgeladen), `DTSTAMP` (der Downloadzeitpunkt, würde jeden
   Lauf neu hochladen) und nach DD-006 verworfene Termine.
3. `PUT kalender` ersetzt alle Sitzungen des Tages. Ein neu hochgeladener Tag verliert damit
   auch einen entfallenen Termin, solange der Tag noch mindestens einen behält.

**Migrationshinweis:** Bestehende `sz:`-Einträge enthalten das hochgeladene Objekt, keinen
Fingerprint, und gelten daher als geändert. Nach dem Deploy wird jeder Tag im Feed genau
einmal neu hochgeladen (derzeit 47 Termine an 47 Tagen). Das prüft nebenbei `PUT kalender`
gegen das echte Backend (Issue #85, Verifikation von 2.1.1).

**Nicht abgedeckt:**

- Ein Tag, dessen Termine alle entfallen, verschwindet aus dem Feed und wird nicht mehr
  hochgeladen; die Sitzung bleibt im Backend. Vom normalen Herausfallen vergangener Tage ist
  das nicht zu unterscheiden (Issue #85).
- Der Run-Report zählt geänderte Tage unter „new or retried“.

**Code:** `bawue_sitzungen_scraper._events_fingerprint`, `BawueSitzungenScraper.listing_page_extractor`
(`_fingerprints`), `get_cached_result`, `store_extracted_result`

**Tests:** `tests/unit/test_bawue_sitzungen_scraper.py::TestSitzungenRefresh` (unveränderter
Feed → übersprungen; geänderter SUMMARY, verlegte Sitzung, neuer Termin am selben Tag → nur
dieser Tag neu; Alt-Eintrag → einmal neu)
