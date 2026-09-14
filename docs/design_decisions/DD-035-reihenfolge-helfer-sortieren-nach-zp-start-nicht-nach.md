[← Index](../design_decisions.md)

# DD-035: Reihenfolge-Helfer sortieren nach `zp_start`, nicht nach Listenposition

**Datum:** 11.07.2026

**Kontext:** 5 Vorgänge scheiterten mit HTTP 400 *Track validation Failed*
(17/1000 Einzelpläne 16/17, 17/3500 Einzelpläne 11/16/17). DD-012 und DD-025
verankerten ihre Reihenfolge-Korrekturen an der **Listenposition**, während das
Backend vor der Track-Validierung nach `zp_start` sortiert (s. DD-016). PARLIS
liefert die Fundstellen aber nicht chronologisch — eine Mitte-November datierte
Beschlussempfehlung kann **nach** der Dezember-Erstlesung in der Liste stehen,
und Lesungen können in beliebiger Datumsreihenfolge auftreten. Dann greifen die
listenpositions-basierten Heuristiken nicht (Issue #48):

- `_ensure_ausschber_after_vollvlsgn` datierte nur `parl-ausschber` um, die
  **vor** der ersten `parl-vollvlsgn` *im Listenindex* lagen
  (`stationen[:first_vollvlsgn_idx]`). Ein chronologisch früher, aber später
  gelisteter Ausschussbericht blieb unverändert und sortierte vor die Lesung.
- `_ensure_initiativ_after_regbsl` datierte die synthetische `parl-initiativ`
  vom **listen-nächsten** Station — war das eine später datierte Lesung, landete
  die Initiative hinter einer früheren Erstlesung.

**Entscheidung:** Beide Helfer argumentieren jetzt über `zp_start`:

- **Ausschber:** Anker ist die **früheste** `parl-vollvlsgn` nach `zp_start`
  (`min`, nicht erste in der Liste). Jeder `parl-ausschber` mit
  `zp_start ≤ Anker` wird — unabhängig von der Listenposition — auf `Anker + 1h`
  gesetzt. `parl-ausschber` mit `zp_start > Anker` (kanonisch zwischen zwei
  Lesungen) bleiben unverändert. Die `zp_modifiziert`-Invariante aus DD-025
  bleibt erhalten.
- **Initiativ:** Datum ist das **kleinste `zp_start` aller auf den `regbsl`
  folgenden Stationen** (`> regbsl.zp_start`), nicht das der listen-nächsten.
  Das liegt garantiert bei/vor der ersten Lesung. Eine Datierung *exakt* auf die
  erste Lesung wurde verworfen: sie kollidiert, und `_enforce_total_ordering`
  (bumpt nur vorwärts) würde die Lesung über den umdatierten Ausschussbericht
  hinausschieben.

**Implementierung:** `bawue_vorgaenge_scraper.py`,
`_ensure_ausschber_after_vollvlsgn()` und `_ensure_initiativ_after_regbsl()`.
Reihenfolge der Aufrufe unverändert (DD-025).

**Tests:** `tests/unit/test_bawue_scraper.py::TestIssue48OrderByZpStartNotListPosition`
— je eine Regression pro Drucksache (17/1000, 17/3500) über `scraper_build_vorgang`,
plus direkte Helfer-Tests (Ausschber nach der Lesung gelistet; Anker = früheste
Lesung; Initiativ-Datierung).
