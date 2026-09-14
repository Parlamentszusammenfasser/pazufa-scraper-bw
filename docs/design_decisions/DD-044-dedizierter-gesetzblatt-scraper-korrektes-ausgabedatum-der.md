[← Index](../design_decisions.md)

# DD-044: Dedizierter Gesetzblatt-Scraper — korrektes Ausgabedatum der `postparl-gsblt`-Station

> ♻️ **Abgelöst durch [DD-047](DD-047-gesetzblatt-als-datums-lookup-statt-eigener-vorgangsquelle.md).** Der hier beschlossene eigenständige
> Gesetzblatt-Vorgang besteht nur aus einer `postparl-gsblt`-Station und kann die
> BW-Track-Validierung (obligatorisches `parl-initiativ`) grundsätzlich nie
> bestehen; der als Phase 2 vorgesehene `initdrucks`-Merge ist zudem nicht
> umsetzbar, da die Drucksachennummer auch im PDF-Volltext nicht vorkommt. Der
> Abschnitt bleibt als historischer Verlauf stehen. Die Aussagen zu
> Publikations- vs. Ausfertigungsdatum gelten unverändert und leben in DD-047 fort.

**Kontext (Issue #9):** Die `postparl-gsblt`-Station, die der PARLIS-Scraper baut, wird
mit dem **Gesetzesbeschluss-/Ausfertigungsdatum** datiert, nicht mit dem tatsächlichen
Ausgabedatum des Gesetzblatts. Ursache: Die PARLIS-Fundstelle einer Gesetzblatt-Zeile
enthält nur das „Gesetz vom <Datum>"-Datum (z. B. „Gesetz vom 21. Dezember 2022 …
Gesetzblatt … 2022 Nr. 41"), das `parse_fundstelle_text` korrekt extrahiert und als
`zp_start`/`zp_referenz` übernimmt. Das echte **Ausgabedatum** (Betreuungsgesetz,
GBl 2022 Nr. 41: 29.12.2022) steht in keinem PARLIS-Feld — es existiert nur in der
Gesetzblatt-Quelle bzw. auf dem PDF-Deckblatt.

**Entscheidung:** Ein eigener `BawueGesetzblattScraper` erschließt das elektronische
Gesetzblatt Baden-Württemberg (`baden-wuerttemberg.de/.../gesetzblatt`) als dritte
Datenquelle. Pro Eintrag mit Gbl-Typ **`Gesetz`** (Verordnungen, Bekanntmachungen,
Berichtigungen werden gefiltert, Konstante `_GSBLT_ACCEPTED_TYPES`) entsteht ein
Vorgang mit **einer** `postparl-gsblt`-Station:

- `Station.zp_start` = **Publikationsdatum** (`<time datetime>` der Detailseite) — das
  gesuchte Ausgabedatum.
- `Dokument.zp_referenz` = **Ausfertigungsdatum** (`Ausfertigung: DD.MM.YYYY`), Fallback
  auf das Publikationsdatum, wenn nicht vorhanden.
- `Gremium.name` = `ReservedGremium.GESETZESBLATT` (`gesetzesblatt`), konsistent zur
  PARLIS-Seite (DD-021), damit ein späterer Merge kollisionsfrei bleibt.
- `api_id` = `uuid5(NAMESPACE_URL, "gsblt-{jahr}-{nummer}")`, `kurztitel` =
  `gsblt-{jahr}-{nummer}`, `typ` = `Vorgangstyp.GG_LAND_PARL`.

**Bewusst NICHT enthalten — Cross-Source-Merge via `initdrucks`:** Der MVP emittiert
**eigenständige** Gesetzblatt-Vorgänge und verknüpft sie nicht mit dem PARLIS-Vorgang.
Ein automatischer Backend-Merge bräuchte auf beiden Seiten ein
`VgIdent(typ="initdrucks")` mit der initialen Drucksachennummer. Das ist hier aus zwei
Gründen nicht umgesetzt: (1) die Drucksachennummer steht nur im PDF-Volltext (Phase 2),
(2) das Emittieren von `initdrucks` auf der PARLIS-Seite ist bewusst deaktiviert
(`emit-initdrucks-ident`, Default `false`, DD-041), weil geteilte `initdrucks` im
Backend fremde Vorgänge zusammenführen → HTTP500 `rel_station_dokument_pkey` (DD-034).
Eigenständige, eindeutig via `kurztitel` gekennzeichnete Vorgänge sind bei geringem
Volumen (~50 Gesetze/Jahr) akzeptabel.

**Bekannte Grenze:** Das elektronische Gesetzblatt ist erst **ab 2024** digital
verfügbar (`start-year`-Default 2024). Der Issue-#9-Beispielfall von 2022 wird daher
noch nicht abgedeckt; das Archiv vor 2024 (LEO-BW/Staatsanzeiger) bleibt Roadmap-Phase 4.
Details und Roadmap: `docs/gesetzblatt_scraping.md`.

**Implementierung:**
- `src/bawue/gesetzblatt_client.py`, `src/bawue/gesetzblatt_parser.py`,
  `src/bawue/bawue_gesetzblatt_scraper.py` (neu)
- Registrierung in `src/bawue/__main__.py` (`SCRAPERS`)
- `[gesetzblatt]`-Sektion in `config.sample.toml`

**Tests:**
- Unit `tests/unit/test_gesetzblatt_parser.py` (Detailseiten-Parsing: Gesetz/Verordnung/
  Bekanntmachung, Publikations-/Ausfertigungsdatum, PDF-Link).
- Unit `tests/unit/test_gesetzblatt_client.py` (HEAD-Binärsuche `find_max_number`,
  429-Retry, UTF-8).
- Unit `tests/unit/test_gesetzblatt_scraper.py` — u. a.
  `test_station_zp_start_is_publikationsdatum`,
  `test_dokument_zp_referenz_is_ausfertigungsdatum` und die Issue-#9-Regression
  `test_issue_9_station_dated_with_publication_not_gesetzesbeschluss`
  (Publikationsdatum ≠ Ausfertigungsdatum → Station trägt das Ausgabedatum).
