[← Index](../design_decisions.md)

# DD-047: Gesetzblatt als Datums-Lookup statt eigener Vorgangsquelle (löst DD-044 ab)

**Datum:** 31.07.2026

**Kontext:** Ein Staging-Lauf gegen WP18 lehnte **alle 15** vom
`BawueGesetzblattScraper` erzeugten Vorgänge mit HTTP 400 Track validation ab:

```
station ordering: ["(2026-02-27 00:00:00 UTC / postparl-gsblt)"] has at least one
station that is not adhering to the track for parliament BW and vgtyp gg-land-parl
```

Der maßgebliche BW-Track (DD-040) lautet
`((E*R+)?S)?I((LA*(Z|LJGA*KA*|LN|LA*(Z|LJGA*KA*|LN)))|Z)`. Das `I`
(`parl-initiativ`) ist **obligatorisch**; ein `G` allein ist kein zulässiges Präfix.
Ein Vorgang, der nur aus einer `postparl-gsblt`-Station besteht, ist damit
**strukturell nicht validierbar** — nicht in Einzelfällen, sondern immer. Die
Kernannahme von [DD-044](DD-044-dedizierter-gesetzblatt-scraper-korrektes-ausgabedatum-der.md) („eigenständige, via `kurztitel` gekennzeichnete
Vorgänge sind akzeptabel") ist unhaltbar. Gegen den lokalen Backend-Stand 0.2.15
(identischer Commit wie Staging) wurde die Ablehnung wortgleich reproduziert.

**Auch der in DD-044 skizzierte Phase-2-Ausweg trägt nicht.** DD-044 verschob den
Cross-Source-Merge mit der Begründung, die Drucksachennummer stehe „nur im
PDF-Volltext". Eine Volltextextraktion zweier realer Gesetz-PDFs (GBl 2026 Nr. 11
und Nr. 14, 39.526 bzw. 16.273 Zeichen) findet **keinerlei** Drucksachenbezug —
weder „Drucksache" noch ein `NN/NNNN`-Muster. Ein `initdrucks` ist aus der
Gesetzblatt-Quelle schlicht nicht gewinnbar.

**Ein `vg_ident` auf Basis der Gesetzblatt-Fundstelle wäre aktiv schädlich.**
Naheliegend wäre, `(Jahr, Nr.)` beidseitig als `VgIdent` zu emittieren. Eine
Auswertung aller 235 WP17-Vorgänge (170 Gesetzblatt-Fundstellen) zeigt jedoch:
**18 `(Jahr, Nr.)`-Schlüssel werden von mehreren Vorgängen zitiert** — GBl 2023
Nr. 21 allein von fünf Gesetzen, da eine Gesetzblatt-Ausgabe mehrere Gesetze
enthalten kann. Der Schlüssel ist also **nicht** 1:1. Ein Experiment gegen
Backend 0.2.15 bestätigt die Folge: Zwei verschiedene Gesetze mit demselben
`VgIdent` führen dazu, dass der zweite PUT mit HTTP 400 scheitert (verdoppelte
Stationsliste `I I L L …`) und der zweite Vorgang anschließend **nicht mehr
existiert** (HTTP 404) — er wurde in den ersten hineingemerged. Das ist exakt der
in [DD-041](DD-041-workaround-initiativdrucksache-standardmaessig-nicht-als-vg.md) beschriebene Schaden.

**Der geteilte Gesetzblatt-PDF ist dagegen unkritisch** (ebenfalls gegen 0.2.15
verifiziert): Zwei Vorgänge mit identischem `hash_` *und* identischem Link
(inkl. `#page=`-Anker, DD-043) werden beide mit HTTP 201 angenommen und behalten
je eine eigene `postparl-gsblt`-Station — mit *und* ohne stabile Stations-`api_id`.
Vorgangs-Matching läuft über geteilte `vg_ident`s (`vorgang_merge_candidates`);
der Dokument-Hash führt Stationen nur **innerhalb** eines bereits gematchten
Vorgangs zusammen (`station_merge_candidates`, DD-034). Zwei fremde Vorgänge
werden also nie über ein gemeinsames PDF verglichen. Die drei real geteilten
GBl-PDFs in WP17 sind damit ungefährlich.

**Entscheidung:** Das Gesetzblatt ist **keine eigene Vorgangsquelle**, sondern ein
`(Jahr, Nr.) → Publikationsdatum`-Lookup für den PARLIS-Scraper.

- `BawueGesetzblattScraper` wird aus `SCRAPERS` entfernt; das Modul und seine
  Tests entfallen. Es werden keine `postparl-*`-Only-Vorgänge mehr emittiert.
- `parse_fundstelle_text` hält den Gesetzblatt-Bezug als `gesetzblatt_jahr` /
  `gesetzblatt_nr` strukturiert fest (bisher wurde er nur als Jahres-Fallback für
  `datum` verwendet und danach verworfen). Die Regex ist die bereits vorhandene
  des Fallbacks; sie greift auf **allen 170** WP17-Gesetzblatt-Fundstellen.
- `GesetzblattDateLookup` löst diesen Bezug bei Bedarf gegen die Detailseite auf.
  Treffer **und Fehlschläge** werden gecacht, da eine Ausgabe von mehreren
  Vorgängen zitiert wird.
- `_build_station` datiert eine `postparl-gsblt`-Station mit dem gefundenen
  Ausgabedatum. Das Dokument behält bewusst die PARLIS-Datierung als
  `zp_referenz` (das Ausfertigungsdatum) — nur die Station wandert.

Damit wird **Issue #9 an seiner Wurzel** gelöst: Der PARLIS-Scraper baut die
`postparl-gsblt`-Station ohnehin aus seiner eigenen Fundstelle, er datierte sie nur
falsch. An 12 real geprüften Gesetzen datiert PARLIS durchgehend auf den
10.02.2026 (Ausfertigung), während die Ausgabe am 27.02.2026 erfolgte.

**Fällt ersatzlos weg:** Der Wahlperioden-Bezug des alten Scrapers. `[gesetzblatt]
wahlperiode` stempelte eine feste WP auf jeden Eintrag — im Staging-Lauf WP18 auf
Gesetze, die im Februar 2026 und damit **vor** der Konstituierung des 18. Landtags
(01.05.2026) verkündet wurden, also WP17-Gesetze waren. Da die Station nun am
PARLIS-Vorgang hängt, erbt sie dessen korrekte Wahlperiode; `wahlperiode` und
`start-year` entfallen aus der `[gesetzblatt]`-Sektion.

**Bewusst nicht gelöst:** Gesetzblatt-Einträge **ohne** PARLIS-Vorgang (z. B.
Verordnungen) erscheinen nicht mehr — sie wurden zuvor gefiltert bzw. abgelehnt und
waren nie im Bestand. Vor 2024 ist das elektronische Gesetzblatt nicht verfügbar;
solche Zitate liefern `None`, die PARLIS-Datierung bleibt dann unverändert stehen.

**Implementierung:**
- `parlis_parser.py` — `_GESETZBLATT_REF_RE`, `parse_fundstelle_text`
- `gesetzblatt_lookup.py` (neu) — `GesetzblattDateLookup`, `parse_german_date`
- `gesetzblatt_client.py` — `fetch_detail_for`, `base_url`; die nur für die
  Jahres-Enumeration nötigen `entry_exists`/`find_max_number`/`_head` entfallen
- `bawue_vorgaenge_scraper.py` — `_gsblt_dates`, `_gesetzblatt_ausgabedatum`,
  `_build_station`
- `__main__.py` — `SCRAPERS` ohne `BawueGesetzblattScraper`
- entfernt: `bawue_gesetzblatt_scraper.py`, `tests/unit/test_gesetzblatt_scraper.py`

**Tests:**
- `tests/unit/test_parlis_parser.py::TestParseFundstelleGesetzblattReference` —
  `(Jahr, Nr.)` wird erfasst, auch ohne explizites Datum; nicht-Gesetzblatt-
  Fundstellen setzen die Felder nicht.
- `tests/unit/test_gesetzblatt_lookup.py` — Publikations- statt Ausfertigungsdatum,
  Caching von Treffern und Fehlschlägen (GBl 2023 Nr. 21 = 5 Zitate → 1 Fetch),
  nicht auflösbare Einträge liefern `None`.
- `tests/unit/test_bawue_scraper.py::TestIssue9GesetzblattAusgabedatum` —
  Regression am realen V-244420: Station trägt den 27.02.2026, das Dokument
  behält den 10.02.2026; ohne Lookup bzw. bei nicht auflösbarem Eintrag bleibt die
  PARLIS-Datierung; Nicht-Gesetzblatt-Stationen lösen keinen Lookup aus.
