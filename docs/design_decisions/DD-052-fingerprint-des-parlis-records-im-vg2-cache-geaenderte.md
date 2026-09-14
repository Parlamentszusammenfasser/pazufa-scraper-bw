[← Index](../design_decisions.md)

# DD-052: Fingerprint des PARLIS-Records im `vg2:`-Cache — geänderte Vorgänge werden neu hochgeladen (GitHub Issue #46)

**Datum:** 13.09.2026

**Kontext:** Der Cache-Schlüssel war nur die Vorgangs-ID (`vg2:V-247603`), jeder
vorhandene Eintrag galt als Treffer, und Einträge laufen nie ab. Ein einmal
hochgeladener Vorgang blieb damit auf dem Stand des ersten Uploads eingefroren: neue
Plenarsitzungen, Beschlussempfehlungen, Gesetzblatt-Einträge oder ein geänderter
„Aktueller Stand" erreichten das Backend nie. Der Scraper läuft täglich und soll stets
den aktuellen Stand zeigen. Der PARLIS-Record liegt bei jedem Lauf ohnehin vollständig
vor (die Suche liefert alle Fundstellen), der Vergleich kostet also keinen Request.

**Entscheidung:**

1. **`vg2:<vorgangs_id>` speichert den Fingerprint statt des Vorgang-JSON.** Ein Treffer
   zählt nur, wenn der gespeicherte Wert dem Fingerprint des aktuellen Records gleicht;
   sonst wird der Vorgang neu gebaut und mit denselben stabilen IDs (DD-028/DD-034)
   erneut per `PUT` hochgeladen. Das JSON wurde nirgends gelesen. Ein Schlüssel je
   Vorgang (statt Fingerprint im Schlüssel) verhindert, dass sich ohne TTL veraltete
   Einträge ansammeln.
2. **Gehasht wird nur, was Fortschritt anzeigt:** je Fundstelle Rohtext + `pdf_url`
   (sortiert) und „Aktueller Stand". Neue Sitzung/Drucksache → neue Fundstelle;
   nachgereichtes Protokoll → neuer Link; Beschluss → neuer Stand.
3. **Bewusst nicht gehasht:**
   - *Parser-abgeleitete Felder* (`datum`, `station_typ`, `ausschuss`, `gesetzblatt_*` …):
     sonst würde jede Parser-Verbesserung den gesamten Cache invalidieren.
   - *Reihenfolge der Fundstellen*: eine Umsortierung in PARLIS ist keine Änderung.
   - *Titel, Initiative, Sachgebiet, Vorgangstyp*: ändern sich praktisch nie; eine reine
     Titelkorrektur in PARLIS wird in Kauf genommen.
   - *Scraper-Output* (PDF-Text, LLM-Ergebnisse): nicht deterministisch und erst nach
     einem vollen Rebuild bekannt.
4. **Keine Versionskonstante.** Soll nach einer Mapping-Änderung doch alles neu gebaut
   werden, reicht es, die `vg2:`-Einträge zu löschen (oder das Präfix zu erhöhen) — ein
   eingebauter Versionszähler würde den Cache dagegen routinemäßig obsolet machen.

Die Regel aus Issue #66 (PDF noch nicht veröffentlicht → nicht cachen) bleibt
unverändert. Ein Rebuild lädt die PDFs erneut, LLM-Calls fallen aber nur für neue
Dokumente an (Semantik-Cache auf Datei-Hash + Prompt, DD-020/DD-049).

**Migrationshinweis:** Bestehende `vg2:`-Einträge enthalten JSON, keinen Fingerprint,
und gelten daher als geändert — jeder Vorgang wird nach dem Deploy genau einmal neu
hochgeladen. Kein manuelles Aufräumen nötig.

**Nicht abgedeckt:** Ein unter derselben URL ausgetauschtes PDF ohne Änderung am
PARLIS-Record. Beteiligung (Schlüssel nur Slug) und Sitzungen (nur Datum) haben dasselbe
Muster und werden separat behandelt.

**Code:** `bawue_vorgaenge_scraper._vorgang_fingerprint`, `listing_page_extractor`
(`_fingerprints`), `get_cached_result`, `store_extracted_result`

**Tests:** `tests/unit/test_bawue_scraper.py::TestVorgangRefreshIssue46` (unveränderter
Record → übersprungen; neue Fundstelle / neuer PDF-Link / geänderter Stand → erneuter
Upload; abgeleitete Felder und Reihenfolge → übersprungen; Alt-Eintrag → einmaliger
Rebuild) und `TestBuildVorgang::test_store_extracted_result_skips_caching_on_pending_pdf_download`
