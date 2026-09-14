[← Index](../design_decisions.md)

# DD-025: `parl-ausschber` vor erster Plenarlesung wird nachgelagert

**Datum:** 10.05.2026

**Kontext:** Beim Staging-Lauf am 09.05.2026 lehnte das Backend 54 Vorgänge mit
HTTP 400 *Track validation Failed* ab. Der überwiegende Teil betraf
**Haushaltsgesetzgebung-Einzelpläne** über drei Haushaltsperioden (2022,
2023/2024, 2025/2026). Die Stationsreihenfolge sah jeweils so aus:

```
preparl-regbsl (Okt) → parl-initiativ (Nov, synthetisch nach DD-012)
                    → parl-ausschber (Nov, +1h nach DD-012-Bumps)
                    → parl-vollvlsgn (Dez, mehrfach)
```

Der Track `gg-land-parl = "((E*R+)?S)?I((VA*(Z|VJGK|VN|...))|Z)"` (s. DD-016)
verlangt **vor** `A` (`parl-ausschber`) zwingend mindestens ein `V`
(`parl-vollvlsgn`). Die Backend-Validierung rejektierte daher den
Ausschussbericht-Eintrag.

**Ursache in den Quelldaten:** PARLIS datiert die Fundstelle
„Beschlussempfehlung und Bericht" auf das Veröffentlichungsdatum der
Drucksache. Bei Haushalts-Einzelplänen erscheint diese Drucksache typischerweise
Mitte November, während die ersten Plenarberatungen erst Mitte Dezember
beginnen. Die Datierung ist sachlogisch: Der Finanzausschuss berät die
Einzelpläne **vor** der Schlussberatung im Plenum. Im Track-Modell wird
`parl-ausschber` aber als Zwischenphase **zwischen** Lesungen geführt — DD-024
hatte diese Folgearbeit bereits unter „committee on initiativ day" ausgewiesen
und auf eine Backend-Regex-Lockerung gehofft (`IA?`-Präfix analog zu `BB`).

**Entscheidung:** Die Korrektur erfolgt scraper-seitig, **nicht** im
Backend-Track. Wenn ein `parl-ausschber` chronologisch vor der ersten
`parl-vollvlsgn` desselben Vorgangs liegt, wird sein `zp_start` auf eine
Stunde nach der ersten `parl-vollvlsgn` gesetzt. Damit landet er kanonisch
zwischen erster und zweiter Lesung — der Position, die der Track für `A`
vorsieht.

- Die Listenposition der Station bleibt unverändert; das Backend sortiert
  Stationen vor der Track-Validierung nach `zp_start`, daher genügt die
  Anpassung des Zeitstempels.
- Mehrere `parl-ausschber` vor der ersten Lesung erhalten alle dasselbe
  Anker-Datum (`first_vollvlsgn.zp_start + 1h`). Gleiche Stationstypen
  dürfen `zp_start` teilen (s. `_enforce_total_ordering`).
- `zp_modifiziert` wird nur dann mitangehoben, wenn es sonst kleiner als der
  neue `zp_start` würde — die für mehrtägige Ausschussphasen relevante
  Obergrenze (DD-024) bleibt erhalten.
- Wenn keine `parl-vollvlsgn` existiert (selten, abgebrochene Vorgänge),
  bleibt der Ausschussbericht unverändert — es gibt keinen Anker.

**Begründung gegen Alternativen:**

1. **Backend-Lockerung (`IA?`-Präfix):** Würde das parlamentarische Recht
   verletzen — `IA` ohne dazwischenliegendes `V` impliziert eine
   Ausschussberatung **ohne** vorherige Plenarlesung, was die GO-LT BW
   §42 nicht vorsieht. DD-016 hält fest, dass Tracks parlamentarisches Recht
   abbilden, nicht Quelldaten-Eigenheiten.
2. **Station verwerfen:** Verlust eines belegten Verfahrensschritts inkl.
   der zugehörigen Drucksache (Dokument bleibt erhalten, aber ohne Trägerstation
   verwaist).
3. **Auf das nächste Plenardatum springen:** Weniger generisch als der
   `+1h`-Anker und schwerer zu testen — ohne klare Reihenfolgeregel zwischen
   `V`, `A` und einem ggf. zweiten `V` am selben Tag.

**Implementierung:** `bawue_vorgaenge_scraper.py`,
Methode `_ensure_ausschber_after_vollvlsgn()`. Aufgerufen in
`_build_vorgang()` **vor** `_ensure_ablehnung_station()`, davor wiederum
`_enforce_total_ordering()`. Die Reihenfolge ist wichtig: Erst alle
synthetischen Stationen (DD-010, DD-012) einfügen, dann den Ausschussbericht
neu datieren, dann die synthetische Ablehnung anhängen (deren Anker seit
DD-031 das chronologisch letzte `zp_start` ist — der Ausschussbericht muss
also bereits neu datiert sein), dann die Tie-Breaker-Bumps (DD-016) — so
werden eventuelle neue Kollisionen am Anker-Slot (`first_vollvlsgn + 1h`)
regulär aufgelöst. *(Reihenfolge ursprünglich umgekehrt — korrigiert in
DD-031.)*

**Tests:** `tests/unit/test_bawue_scraper.py::TestEnsureAusschberAfterVollvlsgn`:

- `test_haushalt_einzelplan_ausschber_retimed_after_first_vollvlsgn` —
  End-to-End-Muster eines Haushalts-Einzelplans (Gesetzentwurf →
  Beschlussempfehlung → drei Lesungen).
- `test_canonical_order_ausschber_after_vollvlsgn_unchanged` —
  Ausschussbericht **nach** Erster Lesung bleibt unverändert.
- `test_no_vollvlsgn_means_ausschber_left_alone` — kein Anker, keine
  Verschiebung.
- `test_multiple_ausschber_before_vollvlsgn_all_retimed` — mehrere Berichte
  werden auf denselben Anker gepinnt.
- `test_retimed_ausschber_does_not_collide_with_other_types` —
  Zusammenspiel mit `_enforce_total_ordering`.
- Vier `test_unit_*`-Tests gegen die statische Methode direkt, inkl.
  `zp_modifiziert`-Invariante und Leerlisten-Verträglichkeit.

**Verbleibende Folgearbeiten** (nicht in DD-025):

- 2 Volksantrag-Vorgänge vom Typ `gg-land-volk` (DD-024-Folgearbeit) —
  weiterhin Backend-seitig.
- Doppelte `parl-vollvlsgn` am selben Zeitstempel in den Haushaltsgesetz-
  Elternvorgängen 2023/2024 und 2025/2026 — eigene Untersuchung, vermutlich
  Konsolidierungs-Lücke aus DD-024 für Schlussabstimmungen.
  → **adressiert in DD-026**.
- 1 XSS-False-Positive in einer LLM-`zusammenfassung` (V-243670, Artefakt
  `</narrow>` aus dem Modell-Output) — getrennt zu behandeln.
  → **adressiert in DD-027**.
