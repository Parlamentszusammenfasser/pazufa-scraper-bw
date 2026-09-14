[← Index](../design_decisions.md)

# DD-032: Issue #33 (falsche Fundstelle/Runde bei Redeprotokoll-Stationen) — durch DD-024/DD-026 bereits behoben

**Datum:** 10.07.2026

**Kontext:** Ein Reviewer meldete (Issue #33) an *veralteten* Staging-Daten zwei
Symptome bei Plenarlesungs-Stationen (`parl-vollvlsgn`), deren `link`/`titel`
deterministisch — vor jedem PDF-Download/LLM-Call — aus den PARLIS-Fundstellen
entstehen:

- **Fall 1 (V-213867, Landeshochschulgesetz 17/847):** Die „1. Lesung"-Karte
  zeigte die *Zweite Beratung* eines fremden Gesetzes (Klimaschutzgesetz 17/521),
  Link `17_0013_06102021.pdf#page=23`.
- **Fall 2 (V-215974, Staatshaushaltsplan Einzelplan 03):** Beide Lesungen trugen
  den `titel` „Zweite Beratung", die Karten waren um eine Runde verschoben.

**Untersuchung:** Beide Vorgänge wurden am 10.07.2026 live aus PARLIS neu gezogen
und durch die aktuelle `_build_vorgang`-Pipeline (ohne LLM/Netz) geführt. Die
rohen Fundstellen (`station_typ` + `pdf_url`) sind als Fixtures committet und
stimmen 1:1 mit der Live-Antwort überein.

- **Fall 1 ist mit dem aktuellen Code nicht reproduzierbar.** PARLIS labelt die
  erste Lesung dieses Vorgangs als „Erste Beratung" und liefert den Anker
  `#page=41` selbst im Fundstellen-href; die zweite Lesung ist „Zweite Beratung"
  `#page=27` (PP 17/15). Der Wert `#page=23` existiert in den PARLIS-Daten von
  V-213867 nicht. Die beiden Lesungen werden zudem durch die dazwischenliegende
  Ausschussbericht-Station getrennt und verschmelzen nie. Der Root-Cause-Verdacht
  im Issue (vorgangsübergreifendes Matching per Datum+Position) trifft die
  tatsächliche Architektur nicht: PARLIS liefert je Vorgang eigene Fundstellen mit
  eigenen Labels/Ankern — es gibt keinen vorgangsübergreifenden
  Disambiguierungs-Schritt. Das Staging-Symptom stammt aus einem älteren
  Scraper-Stand.
- **Fall 2 ist durch DD-024 (Konsolidierung gleicher Lesungsrunden) und DD-026
  („Beschluss des Landtags in Zweiter Beratung" = selbe Runde) behoben.** Die
  runden-bewusste Merge-Logik faltet den Drucksachen-Beschluss in die
  Zweite-Beratung-Station und hält „Dritte Beratung" als eigene Station mit
  korrektem Label; Ergebnis: zwei distinkte Stationen mit `titel` „Zweite"/„Dritte
  Beratung" (`#page=49` / `#page=26`), exakt wie gegen die Original-PDFs belegt.
  Ein Mutationstest (`_same_round_label` → immer `True`) kollabiert V-215974 auf
  eine Station und zeigt, dass der Regressionstest greift.

**Abgrenzung zur Kartenbeschriftung:** Dass das Frontend Haushalts-Einzelpläne als
„1. Lesung"/„2. Lesung" nummeriert, obwohl die tatsächlichen Runden 2 und 3 sind,
ist eine Anzeige-Entscheidung des PaZuFa-Frontends (Einzelpläne haben keine eigene
Erste-Beratung-Plenarstation) und liegt außerhalb dieses Scrapers — die
Scraper-Daten tragen die korrekten Rundenlabels.

**Entscheidung:** Keine Code-Änderung nötig. Das korrekte Verhalten wird durch
`tests/unit/test_issue33_fundstelle_mapping.py` (committete Real-Fundstellen als
Fixtures) gegen Regression gepinnt.
