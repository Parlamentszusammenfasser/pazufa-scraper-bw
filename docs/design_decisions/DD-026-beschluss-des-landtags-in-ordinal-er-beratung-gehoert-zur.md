[← Index](../design_decisions.md)

# DD-026: „Beschluss des Landtags in <Ordinal>er Beratung" gehört zur selben Lesungsrunde

**Datum:** 10.05.2026

**Kontext:** Der Staging-Lauf vom 10.05.2026 lehnte 22 Haushaltsgesetzgebung-
Einzelpläne (Staatshaushaltsplan 2023/2024, Einzelpläne 07/08/…) mit HTTP 400
*Track validation Failed* ab. Beispielhafte Stationsreihenfolge (V-222745,
sortiert nach `zp_start`):

```
preparl-regbsl (25.10.2022)
parl-initiativ (17.11.2022, synthetisch nach DD-012)
parl-vollvlsgn (14.12.2022, „Zweite Beratung")
parl-ausschber (14.12.2022 +1h, retimed nach DD-025)
parl-vollvlsgn (16.12.2022, „Beschluss des Landtags in Zweiter Beratung")
parl-vollvlsgn (21.12.2022, „Dritte Beratung")
parl-vollvlsgn (21.12.2022, „Beschluss des Landtags in Dritter Beratung")
```

Vier `V`-Stationen — der Track `BW.gg-land-parl =
"((E*R+)?S)?I((VA*(Z|VJGA*KA*|VN|VA*(Z|VJGA*KA*|VN)))|Z)"` erlaubt nach `I`
aber höchstens drei `V` (Pfad `V A* V A* V J G A* K A*`). Backend rejektierte
das vierte V („Beschluss des Landtags in Dritter Beratung").

**Ursache in den Quelldaten:** Bei BW-Haushalts-Einzelplänen listet PARLIS für
jede Lesungsrunde **zwei** Fundstellen — das Plenarprotokoll der Aussprache
(„Zweite Beratung", „Dritte Beratung") und die Drucksache mit dem formalen
Abstimmungsvermerk („Beschluss des Landtags in Zweiter/Dritter Beratung").
Beide bilden parlamentarisch denselben Verfahrensschritt ab (eine Lesung mit
ihrer Schlussabstimmung), unterscheiden sich nur in Dokumenttyp und Datum.
Das Mapping `Beschluss des Landtags in …` → `parl-vollvlsgn` ist absichtlich
und durch `TestBeschlussDesLandtagsInBeratung` regressionsgesichert
(siehe `enum_mapper.py`); DD-024 verschmolz aber nur Stationen mit
**zeichengleichem** `station_typ`-Text und ließ daher beide Drucksachen-
Varianten als separate `V`-Stationen stehen.

**Entscheidung:** `_same_round_label` in `bawue_vorgaenge_scraper.py` wird um
eine Lesungsrunden-Äquivalenz erweitert. Zwei Labels werden auch dann als
gleich behandelt, wenn beide ein gemeinsames Lesungs-Ordinal („Erste",
„Zweite", „Dritte", „Vierte", „Fünfte" — case-insensitive Stamm-Match) und das
Wort „Beratung" enthalten. Damit verschmelzen:

- `"Erste Beratung"` ≡ `"Beschluss des Landtags in Erster Beratung"`,
- `"Zweite Beratung"` ≡ `"Beschluss des Landtags in Zweiter Beratung"`,
- `"Dritte Beratung"` ≡ `"Beschluss des Landtags in Dritter Beratung"`.

Die existierende DD-024-Konsolidierungslogik (`_try_merge_station` /
`_collect_stationen`) übernimmt dann automatisch `zp_start` = frühestes
Datum, `zp_modifiziert` = spätestes Datum und konkateniert die `dokumente`-
Listen.

**Defensive Vorgaben:**

- Labels ohne `"Beratung"` (z. B. `"Überweisung"`, `"Schlussabstimmung"`,
  `"Gesetzesbeschluss des Landtags"`) liefern `_reading_round` → `None` und
  fallen damit auf den DD-024-Exact-Match zurück — die seit DD-024 gültigen
  Negativtests (`test_consecutive_vollvlsgn_ueberweisung_not_merged` etc.)
  bleiben unverändert grün.
- Leere Labels werden weiterhin defensiv als „nicht vergleichbar" behandelt
  (`return False`).

**Begründung gegen Alternativen:**

1. **Re-Mapping `Beschluss des Landtags in …` → `parl-akzeptanz`:** würde die
   `TestBeschlussDesLandtagsInBeratung`-Regression brechen und das Track-
   Regex weiterhin sprengen (zwei `J` ohne dazwischenliegendes `V J G K`-
   Abschluss-Muster). Verworfen.
2. **Backend-Regex-Lockerung (mehr `V`):** Würde das parlamentarische Modell
   verfälschen — die BW-Geschäftsordnung kennt für reguläre Gesetzgebung max.
   drei Lesungen, das Regex bildet das korrekt ab. DD-024 hatte denselben
   Trade-off bereits zugunsten Scraper-seitiger Konsolidierung entschieden.
3. **„Beschluss in …"-Fundstellen verwerfen:** Verlust der Drucksache mit
   dem formalen Abstimmungstext.

**Implementierung:** `bawue_vorgaenge_scraper.py`, neuer Helper
`_reading_round()` und Erweiterung von `_same_round_label()`. Aufrufkette
unverändert: `_collect_stationen` → `_try_merge_station` → `_find_merge_target`
→ `_same_round_label`.

**Tests:** `tests/unit/test_bawue_scraper.py::TestReadingRoundEquivalence`:

- `test_zweite_and_beschluss_in_zweiter_merged` — End-to-End mit zwei V-
  Fundstellen, Ergebnis: 1 V mit `zp_start`/`zp_modifiziert`-Spanne und 2
  Dokumenten.
- `test_haushalt_einzelplan_collapses_to_two_v_stations` — End-to-End-
  Reproduktion des V-222745-Musters: aus 4 V-Fundstellen werden 2 V-
  Stationen, sortierte Sequenz `S I V A V` passt in das Track-Regex.
- `test_different_rounds_still_separate` — `Erste Beratung` ≠ `Zweite
  Beratung`, weiterhin 2 separate V.
- `test_ueberweisung_not_merged_with_erste_beratung` — Defensive: kein
  „Beratung" → Exact-Match-Fallback → keine Verschmelzung.
- Sechs `test_unit_*`-Tests direkt gegen `_reading_round` und
  `_same_round_label` (Ordinal-Extraktion plain & „Beschluss in …", Negativ-
  Fälle, Empty-Label-Defensivverhalten).

Die DD-024-Tests (`TestStationMerging`,
`test_staatshaushaltsgesetz_consolidation_end_to_end`) und die
`TestBeschlussDesLandtagsInBeratung`-Mapping-Regressionen bleiben unverändert
grün.
