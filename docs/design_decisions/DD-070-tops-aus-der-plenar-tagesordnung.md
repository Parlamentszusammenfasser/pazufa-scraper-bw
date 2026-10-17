[← Index](../design_decisions.md)

# DD-070: TOPs aus der Plenar-Tagesordnung (GitHub Issue #66)

**Datum:** 17.10.2026

**Kontext:** `Sitzung.tops` war immer `[]`. Geplante Lesungen sollen als TOP im Kalender stehen
(DD-068, DD-069). Einzige Quelle ist `/de/aktuelles/tagesordnungen`: das PDF der **nächsten**
Plenarsitzung, Dateiname mit Datum und Sitzungsnummer. Ausschüsse veröffentlichen keine (DD-064).

Das Backend verknüpft einen TOP mit einem Vorgang nur über ein gemeinsames Dokument. Trifft das
TOP-Dokument keine bestehende Zeile, legt es eine neue an. Das ist gefährlich: Neben der
Platzhalter-Zeile eines Vorgangs (DD-067) macht sie dessen nächsten Upload mehrdeutig
(`AmbiguousMatch`, der PUT scheitert bei jedem Lauf).

**Entscheidung:**

1. Die Tagesordnung ergänzt die Plenarsitzung desselben Tages im selben `PUT`. Fehlt im Feed die
   `nummer`, kommt sie aus dem Dateinamen.
2. **TOP-Titel:** vom Punktbeginn bis zur ersten Zeile „– Drucksache …“, sonst die erste Zeile.
   Nur so ist er unter Linux und macOS gleich (kreuzberg setzt unter Linux keine Leerzeilen).
3. **Dokumente nur für Lesungen eines Gesetzentwurfs**: Nur sie haben einen Vorgang.
4. **Nur hochgeladene Zeilen referenzieren:** Der Vorgänge-Scraper registriert nach jedem Upload
   den Hash jeder gelesenen Drucksache in Redis (`drucks-hash:<drucksnr>`). Der TOP verwendet nur
   diesen Hash. Fehlt er, fehlt das Dokument, und der Tag wird morgen neu versucht. Ohne LLM
   bleibt das Register leer (Platzhalter-Hashes, DD-048).
5. **Cache (DD-066):** Der `sz:`-Wert ist `<Termine>:<Tagesordnung>`. Eine geänderte Tagesordnung
   lädt den Tag neu. Verschwindet sie von der Seite, bleibt der Tag gecacht, sonst gingen seine
   TOPs verloren.
6. **Fail-soft:** Ist die Tagesordnung nicht lesbar, geht der Kalender ohne TOPs hoch.

**Verworfen:**

- *Drucksache selbst laden und hashen:* erzeugt die gefährliche neue Zeile (Kontext).
- *Sitzungen erst nach den Vorgängen laufen lassen:* Mit dem Register unnötig; der Kalender
  hinge an einem langen Vorgänge-Lauf.
- *`drucksnr` + `typ` + `zp_referenz` oder `api_id`:* Die Tagesordnung nennt weder Typ noch
  Datum, BW-Dokumente haben keine `api_id`.

**Nicht abgedeckt:**

- Vor dem Deploy gecachte Vorgänge registrieren ihre Drucksachen erst beim nächsten Upload.
- Ausschüsse bekommen keine TOPs.
- Ändert sich ein Termin, nachdem die Tagesordnung verschwunden ist, gehen seine TOPs verloren.

**Code:** `bawue/tagesordnung.py`, `BawueSitzungenScraper` (`_fetch_agendas`, `_build_tops`,
`_drucksache_dokument`, `get_cached_result`), `BawueVorgaengeScraper.store_extracted_result`,
`cache.drucksache_hash_key`

**Tests:** `tests/unit/test_tagesordnung.py`, `test_bawue_sitzungen_scraper.py::TestIssue66Tops`,
`test_bawue_scraper.py::TestIssue66DrucksacheHashRegistry`
