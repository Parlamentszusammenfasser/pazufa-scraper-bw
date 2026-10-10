[← Index](../design_decisions.md)

# DD-065: `Station.gremium_federf` aus der Beschlussempfehlung statt aus dem Plenarprotokoll (GitHub Issue #64)

**Datum:** 10.10.2026

**Kontext:** `gremium_federf` blieb immer leer. Die Spec will es an der Ausschuss-Station
gesetzt sehen, wenn der Ausschuss federführend ist. Issue #64 wollte es aus der Überweisung im
Plenarprotokoll lesen (abhängig von #35). In BW berichtet aber nur der federführende Ausschuss
dem Plenum mit einer „Beschlussempfehlung und Bericht“. Vor- und mitberatende Ausschüsse geben
ihr Votum an ihn weiter und erscheinen in PARLIS nicht als eigene Fundstelle. Belege:

- WP17-Dump (468 Vorgänge): kein Vorgang hat `parl-ausschber`-Stationen aus mehr als einem
  Ausschuss.
- Die Beschlussempfehlungen sagen es selbst, z. B. zu Drs. 17/273: „Der federführende Ständige
  Ausschuss …“; der vorberatende Innenausschuss steht nur im Fließtext.
- Das Plenarprotokoll wäre die schlechtere Quelle: Drs. 18/75 wurde laut PlPr 18/6 an den
  Ständigen Ausschuss überwiesen, die Beschlussempfehlung kam vom Ausschuss für Finanzen.

**Entscheidung:**

1. `gremium_federf=True` an jeder `parl-ausschber`-Station aus einer Fundstelle
   „Beschlussempfehlung …“, die einen Ausschuss nennt (`fund["ausschuss"]`).
2. Sonst bleibt das Feld `UNSET`: ohne genannten Ausschuss (Gremium `plenum`) und bei den
   gemappten, aber nie beobachteten Quellen „Ausschussberatung“ und „Bericht und Empfehlungen“,
   wo der Ausschuss auch ein mitberatender sein könnte.
3. `False` entsteht nie, denn mitberatende Ausschüsse haben keine Station.
4. Kein Plenarprotokoll-Parsing; #35 bleibt als künftige Arbeit offen.

`api_id` (aus `vorgang_id`, `typ`, `zp_start`, DD-034) und die Reihenfolge der Stationen bleiben
unverändert.

**Wirkung auf Bestehendes:** Das Feld ist abgeleitet und geht nicht in den `vg2:`-Fingerprint
ein (DD-052). Bereits gecachte Vorgänge bekommen es erst beim nächsten Neubau. Ob das Backend
es beim Merge in eine bestehende Station übernimmt, ist nicht geprüft (vgl. DD-060 zu
`schlagworte`).

**Code:** `BawueVorgaengeScraper._build_station`

**Tests:** `tests/unit/test_bawue_scraper.py::TestIssue64GremiumFederf`
