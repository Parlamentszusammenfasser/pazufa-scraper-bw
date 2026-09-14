[← Index](../design_decisions.md)

# DD-031: Unlabeled Plenarprotokoll-Fundstelle als Erste Beratung erkannt

**Datum:** 09.07.2026

**Kontext:** V-246637 („Gesetz zur Änderung des Abgeordnetengesetzes", WP18,
Fraktion der AfD — dasselbe Vorgang wie DD-028, inzwischen bis zur Ablehnung
fortgeschritten) wurde erneut mit HTTP 400 *Track validation Failed*
abgelehnt. Die vier realen PARLIS-Fundstellen, in Auftrittsreihenfolge:

```
Gesetzentwurf (03.06)
→ Plenarprotokoll 18/6 10.06.2026 S. 94-97   [kein Stationstyp-Label!]
→ Beschlussempfehlung und Bericht / Ständiger Ausschuss (24.06)
→ Zweite Beratung   Plenarprotokoll 18/10 01.07.2026
```

Die zweite Fundstelle — die Erste Beratung — trägt in den Rohdaten **kein**
führendes Label (`"Erste Beratung   Plenarprotokoll …"` fehlt; stattdessen nur
`"Plenarprotokoll 18/6 10.06.2026 S. 94-97"`). Der `station_typ`-Regex in
`parlis_parser.py` verlangt ein Doppelleerzeichen/Tab-terminiertes führendes
Label (DD-011) und liefert hier nichts. `_build_station()` fällt auf den
vollen Rohtext zurück (DD-011-Gegenprüfung), aber `map_stationstyp()` kennt
keinen Schlüssel `"Plenarprotokoll"` (nur `map_dokumententyp()` tut das) —
das Ergebnis ist `SONSTIG`, und `filter-sonstig-stations=true` verwirft die
Station stillschweigend. Bereits in `docs/observed_station_types.md`
("Regex fallback") als bekanntes, aber bis dato für harmlos gehaltenes
Verhalten vermerkt.

**Ursache — warum das den Track bricht:** Der `gg-land-parl`-Track (DD-016)
verlangt für eine Ablehnung zwingend **zwei** Lesungen vor `N`:
`IVAVN` (Punkt 4 in DD-016, aus GO-LT BW §43 Abs. 4 — Abstimmung in erster
Lesung ist unzulässig). Mit der verworfenen Ersten Beratung sendete der
Scraper nur `I A V N` (eine Lesung) — unabhängig von der
Stationsreihenfolge/-zeitstempeln ungültig, weil dem Track schlicht ein `V`
fehlt. Eine erste Analyse vermutete stattdessen einen Zeitstempel-Bug in
`_ensure_ablehnung_station()`/`_ensure_ausschber_after_vollvlsgn()`
(Ausschussbericht-Fundstelle vom 24.06 erscheint listenpositions-mäßig vor
der Zweite-Beratung-Fundstelle vom 01.07, ähnlich DD-025) — diese Vermutung
erwies sich per Reproduktion als falsch: Mit vollständiger Lesungspaar-Folge
(inkl. wiederhergestellter Erster Beratung) löst `_enforce_total_ordering`
(DD-016) die Zeitstempel-Kollision zwischen Ausschussbericht und
synthetischer Ablehnung bereits korrekt auf, ganz ohne die Reihenfolgeänderung
unten. Die eigentliche Ursache war ausschließlich die fehlende zweite Lesung.

**Entscheidung (zwei unabhängige Änderungen):**

1. **Positionsheuristik für unlabeled Plenarprotokoll-Fundstellen** (die
   eigentliche Behebung): Trägt eine Fundstelle kein `station_typ`-Label,
   referenziert aber ein Plenarprotokoll, und wurde noch **keine**
   `parl-vollvlsgn`-Station für diesen Vorgang erfasst, wird sie als
   `parl-vollvlsgn` (typischerweise die Erste Beratung) reklassifiziert statt
   als `SONSTIG` verworfen. Jede *weitere* unlabeled Plenarprotokoll-Fundstelle
   im selben Vorgang wird weiterhin als `SONSTIG` gefiltert — die Heuristik
   nimmt nur für die erste an, dass es sich um die fehlende Lesung handelt.
   `Gremium`/`Doktyp` sind von der Reklassifizierung unberührt: Ohne
   `ausschuss`-Feld landet das Gremium ohnehin auf `plenum` (DD-021), und
   `map_dokumententyp()` erkennt `"Plenarprotokoll"` bereits unabhängig als
   `redeprotokoll` (bestehender Fallback in `_build_dokumente()`).
2. **Härtung der Ausschussbericht-/Ablehnung-Reihenfolge** (defensiv, nicht
   die Ursache dieses Vorfalls, aber eine reale Fragilität): Bislang lief
   `_ensure_ablehnung_station()` **vor** `_ensure_ausschber_after_vollvlsgn()`
   und ankerte auf `stationen[-1].zp_start` — der letzten Station nach
   **Listenposition**. Für Vorgänge, deren Ausschussbericht-Fundstelle vor der
   finalen Lesungs-Fundstelle in der Liste steht (Ankunftsreihenfolge folgt
   PARLIS-Auftrittsreihenfolge, nicht Chronologie), aber **nach** der
   DD-025-Neudatierung chronologisch später liegt, ankerte die synthetische
   Ablehnung auf einem inzwischen überholten Zeitstempel. `_enforce_total_ordering`
   fängt das für den bislang beobachteten 3-Stationen-Fall ab (Ablehnung wird
   stets zuletzt in die Liste angehängt und damit zuletzt in der
   Kollisions-Bump-Reihenfolge verarbeitet), aber das ist Zufall der
   Verarbeitungsreihenfolge, keine Garantie. Jetzt: Aufruf-Reihenfolge
   `_ensure_ausschber_after_vollvlsgn()` **vor** `_ensure_ablehnung_station()`,
   und der Anker ist `max(s.zp_start for s in stationen)` (chronologisch
   letzte Station) statt `stationen[-1].zp_start` (Listenposition letzte
   Station).

**Implementierung:** `bawue_vorgaenge_scraper.py` — `_collect_stationen()`
(neuer `seen_vollvlsgn`-Flag + Positionsheuristik, analog zu `seen_ausschber`
aus DD-019), `_ensure_ablehnung_station()` (Anker-Berechnung), `_build_vorgang()`
(Aufruf-Reihenfolge).

**Tests:**

- `tests/unit/test_bawue_scraper.py::TestUnlabeledPlenarprotokollFallback` —
  `test_unlabeled_erste_beratung_recovered_as_vollvlsgn` (V-246637-Muster,
  gegen Vor-Fix-Code verifiziert rot), `test_unlabeled_plenarprotokoll_after_first_reading_not_reclassified`
  (nur die erste unlabeled Fundstelle wird reklassifiziert),
  `test_labeled_sonstig_fundstelle_still_filtered` (gelabelte SONSTIG-Fundstellen
  wie „Mitteilung" bleiben gefiltert).
- `tests/unit/test_bawue_scraper.py::TestAblehnungAnchorsAfterAusschberRetiming` —
  Regressionstest für die Reihenfolge-/Anker-Härtung.
- `tests/integration/test_scraper_pipeline.py::TestUnlabeledPlenarprotokollFundstelle` —
  End-to-End über den echten HTML/Regex-PARLIS-Parser (neue Fixtures
  `gesetzgebung_unlabeled_erste_beratung_{search.json,results.html}`),
  verifiziert die vollständige Stationstyp-Sequenz und die finale
  Zeitstempel-Kette `Erste Beratung < Ausschussbericht < Zweite Beratung
  < Ablehnung`.

**Verifikation in Produktion:** Beim nächsten Staging-Cycle sollte V-246637
mit vollständiger Vier-Stationen-Sequenz (`parl-initiativ`, zwei
`parl-vollvlsgn`, `parl-ausschber`, plus synthetische `parl-ablehnung`) ohne
*Track validation Failed* durchlaufen. Da der eigentliche Fehler die fehlende
zweite Lesung war (nicht ein Zeitstempel-Problem), ist unklar, ob andere,
noch nicht identifizierte Vorgänge mit demselben Muster (unlabeled Erste-
Beratung-Fundstelle) ebenfalls betroffen waren — ein Re-Scrape wird zeigen,
ob weitere zuvor fehlgeschlagene Vorgänge dadurch geheilt werden.
