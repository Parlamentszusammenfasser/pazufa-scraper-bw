[← Index](../design_decisions.md)

# DD-039: Gleicher Ausschuss, großer Zeitabstand → keine Zusammenführung (Issue #54)

**Datum:** 12.07.2026

**Kontext:** `_find_matching_ausschuss()` fasst aufeinanderfolgende `parl-ausschber`-
Fundstellen desselben Gremiums (ohne dazwischenliegende Plenarstation) zu einer
Station zusammen (DD-004). Das ist richtig für die Fundstellen **einer**
Beratungsrunde (Beschlussempfehlung + Bericht, wenige Tage auseinander), aber falsch
für **zwei getrennte** Beratungen desselben Ausschusses, die Monate auseinanderliegen.
Beim Staatshaushaltsgesetz 2022 (V-214597, 17/1000) existieren zwei
Beschlussempfehlungen des Finanzausschusses vom 30.06.2022 und 09.02.2023. Sie wurden
zu einer Station verschmolzen, die nur das frühere Datum behielt — `_merge_into()`
weitet die Zeitspanne für `parl-ausschber` nicht auf (anders als für `parl-vollvlsgn`,
DD-024), das spätere Datum ging also verloren.

**Entscheidung:** `_find_matching_ausschuss()` erhält zusätzlich das `zp_start` der neuen
Station. Liegt der einzige rückwärts gefundene Kandidat desselben Gremiums mehr als
`_AUSSCHBER_MERGE_MAX_GAP` (60 Tage) von der neuen Station entfernt, gilt er als
eigenständige Beratung und wird **nicht** zusammengeführt (`return None`) — die neue
Station bleibt als eigener Datensatz erhalten. 60 Tage trennen mehrmonatige
Distanzen (Nachtrags-/Folgeberatungen) sicher von einer einzelnen, über Tage/Wochen
laufenden Ausschussrunde. Die Prüfung nutzt `abs()`, ist also reihenfolgeunabhängig.

Bewusst nicht angetastet: Die Zusammenführung innerhalb des Fensters bleibt unverändert
(bestehende Merge-Tests grün), und `_merge_into()` weitet die Ausschber-Spanne weiterhin
nicht auf — das ist nur bei getrennten Beratungen ein Problem, und dieser Fall wird jetzt
an der Wurzel (keine Zusammenführung) gelöst.

**Implementierung:** `bawue_vorgaenge_scraper.py` — `_AUSSCHBER_MERGE_MAX_GAP`,
`_find_matching_ausschuss()`, `_find_merge_target()`.

**Tests:** Unit (`tests/unit/test_bawue_scraper.py::TestStationMerging`):
`test_ausschuss_no_merge_across_large_time_gap` (V-214597-Szenario: zwei
Beschlussempfehlungen des Finanzausschusses 30.06.2022 / 09.02.2023 → zwei Stationen,
beide Daten erhalten). Die bestehenden `test_ausschuss_merge_backwards_no_plenum_between`
(2 Tage → 1 Station) und `test_ausschuss_no_merge_across_plenum` bleiben grün.
