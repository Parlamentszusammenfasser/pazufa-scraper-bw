[← Index](../design_decisions.md)

# DD-024: Plenarlesungen gleicher Runde werden konsolidiert

**Datum:** 23.04.2026

**Kontext:** Beim Staging-Lauf am 22.04.2026 wurden drei *Staatshaushaltsgesetz*-
Elternvorgänge (StHG 2022, 2023/2024, 2025/2026) vom Backend mit HTTP 400
*Track validation Failed* abgelehnt. Grund: Das Regex für `BW.gg-land-parl` in
`deploy/tracks.toml` erlaubt höchstens drei Plenarlesungen (`V A* V A* V`), die
Elternvorgänge emittierten aber 8–9 `parl-vollvlsgn`-Stationen, weil PARLIS für
jeden Einzelplan (Ministerium) eine eigene Fundstelle „Zweite Beratung" listet
und `enum_mapper.py` alle „Erste/Zweite/Dritte Beratung"-Texte gleich auf
`Stationstyp.PARL_MINUS_VOLLVLSGN` abbildet. DD-004 verbot bis dahin jegliche
Zusammenführung von Plenarstationen — das bewahrte zwar zu Recht die
Unterscheidung zwischen 1., 2. und 3. Lesung, erzwang aber pro Einzelplan-
Sitzungstag eine eigene Station.

Das OpenAPI-`Station`-Modell hat kein Feld für Kind-Stationen, bietet aber mit
`zp_start` (erste Aktion) + `zp_modifiziert` (letzte Aktion) + `dokumente: List[...]`
genau die Felder, um eine über mehrere Sitzungstage gespannte Phase in einer
einzigen Station abzubilden — inkl. aller pro-Tag-PDFs als Dokumente.

**Entscheidung:** Aufeinanderfolgende `parl-vollvlsgn`-Stationen mit
**identischem roh-`station_typ`-Text** (case-insensitive, getrimmt,
nicht-leer) und identischem Gremium werden zu einer Station zusammengeführt:

- `zp_start` = Minimum der beteiligten Fundstellendaten,
- `zp_modifiziert` = Maximum der beteiligten Fundstellendaten,
- `dokumente` = Vereinigung (die nachgelagerte Drucksachen-Deduplizierung in
  `_dedup_drucks` bleibt greifbar).

Unterschiedliche Rundentexte ("Erste Beratung" ≠ "Zweite Beratung" ≠
"Überweisung" ≠ "Dritte Beratung") bleiben **weiterhin getrennte Stationen**,
damit die semantische Unterscheidung zwischen 1., 2. und 3. Lesung erhalten
bleibt — die vom Track-Regex zwingend gefordert wird: Für eine erfolgreiche
Gesetzgebung verlangt die Regex mindestens zwei `V`-Stationen vor `J G K`.

**Defensive Vorgabe:** Ein leerer `station_typ`-Text (PARLIS-Parser konnte den
Rundenlabel nicht extrahieren) verhindert die Zusammenführung — im Zweifelsfall
bleibt es bei zwei getrennten Stationen.

**Begründung:**

- Semantisch korrekt: Mehrere Einzelplan-Debatten am 15./16./17. Dezember
  gehören zur selben 2. Lesung der Haushaltsberatung, nicht zu drei Lesungen.
- Modell-konform: Das Station-Schema sieht `zp_start`/`zp_modifiziert` genau
  für solche mehrtägigen Phasen vor.
- Regel-konform: Die resultierende Station-Liste (`V A V V J G …`) bleibt
  innerhalb der vom Track-Regex erlaubten drei `V`-Stationen.
- Null-Risiko für Bestandsfälle: Unterschiedliche Rundentexte werden weiterhin
  getrennt, womit alle bisher validen Vorgänge unverändert durchlaufen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_try_merge_station()`
(neuer `PARL_MINUS_VOLLVLSGN`-Zweig vor der generischen Merge-Logik) und
`_collect_stationen()` (trackt `last_station_typ_str` über die Schleife).

**Tests:** `tests/unit/test_bawue_scraper.py::TestStationMerging` —
`test_vollvlsgn_same_typ_text_different_days_merged`,
`test_vollvlsgn_same_typ_text_same_day_merged`,
`test_vollvlsgn_three_same_typ_merged`,
`test_staatshaushaltsgesetz_consolidation_end_to_end` (End-to-End StHG-Muster
mit 9 Plenarfundstellen → 3 konsolidierte V-Stationen),
`test_vollvlsgn_empty_station_typ_not_merged` (defensive Vorgabe).
Die bestehenden Regressionstests
`test_consecutive_vollvlsgn_not_merged_even_with_documents` (Erste + Zweite,
unterschiedlicher Text) und `test_consecutive_vollvlsgn_ueberweisung_not_merged`
(Erste + Überweisung) bleiben unverändert und schützen davor, die
Rundenunterscheidung versehentlich zu verlieren.

**Offene Folgearbeiten (nicht in DD-024):**

- 51 weitere Staging-Ablehnungen mit „committee on initiativ day" (Stationstyp
  `parl-ausschber` unmittelbar nach `parl-initiativ` am selben Tag) benötigen
  eine Lockerung im Backend-Regex (`IA?`-Präfix analog zu `BB` in
  `deploy/tracks.toml`).
- Zwei Ablehnungen vom Typ `gg-land-volk` (Volksantrag) benötigen eine eigene
  Track-Definition für BW im Backend.

Beide Punkte sind Backend-seitig und werden separat adressiert.
