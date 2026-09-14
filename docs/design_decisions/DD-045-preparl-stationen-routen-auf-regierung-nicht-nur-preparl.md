[← Index](../design_decisions.md)

# DD-045: `preparl-*`-Stationen routen auf `regierung`, nicht nur `preparl-regbsl` (Issue #10)

**Datum:** 31.07.2026

**Kontext:** Issue #10 (migriert von Codeberg #74) beobachtete, dass
`preparl-regbsl`-Stationen (Regierungsbeschluss/Kabinettsbeschluss — der einzige
`preparl-*`-Wert, den `map_stationstyp` aus PARLIS-Fundstellentext tatsächlich
produziert, s. DD-003) in `_determine_gremium` in den `plenum`-Default fiel, statt das
für genau diesen Fall reservierte `regierung` zu bekommen (DD-021, Spec + Wiki).
`preparl-regbsl` ist eine Kabinetts-, keine Landtags-Handlung — inkonsistent mit
`preparl-regent`, das der Beteiligungsportal-Scraper bereits direkt mit
`ReservedGremium.REGIERUNG` baut (`bawue_beteiligung_scraper.py`). Die Community
bestätigte im Issue ("Fazit Yes"): pre-parlamentarische Regierungsstationen sollen
`regierung` bekommen.

**Entscheidung:** `_determine_gremium` routet nicht nur `preparl-regbsl`, sondern
*jeden* `Stationstyp`, dessen Wert mit `"preparl-"` beginnt, auf `regierung`
(Priorität: benannter Ausschuss > `postparl-gsblt` → `gesetzesblatt` > `preparl-*` →
`regierung` > `plenum`-Default). Das PaZuFa-Spec-Enum kennt vier `preparl-*`-Werte
(`PREPARL_REGENT`, `PREPARL_ECKPUP`, `PREPARL_REGBSL`, `PREPARL_VBEGDE`); nur
`PREPARL_REGBSL` ist über diesen Scraper heute erreichbar, aber ein wertspezifisches
`if station_typ == Stationstyp.PREPARL_REGBSL` hätte denselben Bug für
`PREPARL_ECKPUP`/`PREPARL_VBEGDE` reproduziert, sobald `enum_mapper.py` künftig
PARLIS-Text auf einen dieser Werte abbildet (aktuell ungenutzt, s. Kommentar-Block
"Vorparlamentarisch" in `enum_mapper.py`). Die präfixbasierte Regel schließt diese
Lücke präventiv, ohne über tatsächlich beobachtetes Verhalten hinaus zu spekulieren —
sie kodiert nur die bereits getroffene Konvention ("vorparlamentarisch =
Regierungsstufe") generisch statt fallweise.

**Bewusst nicht geändert:** Die beiden hartkodierten `ReservedGremium.PLENUM`-Stellen
für synthetische Stationen (`_ensure_ablehnung_station` → `parl-ablehnung`,
`_ensure_initiativ_after_regbsl` → `parl-initiativ`) bleiben unverändert — beides sind
`parl-*`-Typen (Landtags-Handlungen: Ablehnung durch den Landtag bzw. parlamentarische
Einbringung), keine `preparl-*`-Typen, und fallen damit korrekt weiter unter den
`plenum`-Default.

**Implementierung:** `bawue_vorgaenge_scraper.py::_determine_gremium`.

**Tests** (`tests/unit/test_bawue_scraper.py`):

- `TestBuildVorgang::test_preparl_regbsl_station_uses_regierung_gremium` — End-to-end-
  Regression über die reale PARLIS-Fundstelle ("Gesetzentwurf" + Initiator
  "Landesregierung"), pinnt Issue #10.
- `TestDetermineGremiumRouting` — direkte Coverage von `_determine_gremium`:
  `test_preparl_station_types_use_regierung` deckt alle vier `preparl-*`-Werte ab
  (auch die drei, die PARLIS-seitig heute nicht erreichbar sind);
  `test_non_preparl_station_types_still_default_to_plenum` ist der Regressionsschutz,
  dass die Präfix-Regel nicht versehentlich andere Typen mit erfasst;
  `test_postparl_gsblt_still_uses_gesetzesblatt` pinnt die unveränderte
  Gesetzblatt-Route; `test_named_ausschuss_overrides_preparl_routing` und
  `test_named_ausschuss_overrides_gsblt_routing` pinnen die Prioritätsreihenfolge
  (Ausschuss schlägt beide Spezialfälle).
