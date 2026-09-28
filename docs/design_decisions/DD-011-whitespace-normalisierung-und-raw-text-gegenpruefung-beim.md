[← Index](../design_decisions.md)

# DD-011: Whitespace-Normalisierung und Raw-Text-Gegenprüfung beim Enum-Mapping

**Datum:** 27.03.2026 | **Aktualisiert:** 28.09.2026 (Issue #13)

**Kontext:** PARLIS formatiert Fundstellentexte mit doppelten Leerzeichen als
Trennzeichen zwischen Feldern (z. B. Stationstyp, Autor, Datum). Der Parser
(`parlis_parser.py`) nutzt ein Non-Greedy-Regex, das am *ersten* Doppelleerzeichen
trennt, um den `station_typ` zu extrahieren.

Problematisch wird dies bei mehrteiligen Stationstypen wie
„Beschluss des Landtags in Zweiter Beratung", wenn PARLIS ein Doppelleerzeichen
*innerhalb* des Stationstyps einfügt. Der Parser schneidet dann nach „Landtags"
ab und extrahiert `station_typ = "Beschluss des Landtags"` — ohne den
qualifizierenden Zusatz „in Zweiter Beratung".

**Auswirkung:** „Beschluss des Landtags" mappt auf `parl-akzeptanz` (Annahme),
obwohl „Beschluss des Landtags in Zweiter Beratung" korrekt auf
`parl-vollvlsgn` (Plenarlesung) mappt. Betroffene Vorgänge (insb.
Haushaltsgesetzgebung) zeigten dadurch mehrere falsche Annahme-Stationen.

**Entscheidung:** Zweistufige Absicherung:

1. **Whitespace-Normalisierung:** `map_stationstyp()` und `map_dokumententyp()`
   kollabieren vor dem Substring-Matching alle Whitespace-Sequenzen zu einfachen
   Leerzeichen. Damit matcht auch „Landtags in" den Schlüssel
   „Beschluss des Landtags in".

2. **Raw-Text-Gegenprüfung:** `_build_station()` mappt zunächst den extrahierten
   `station_typ`. Anschließend wird auch der vollständige Fundstellentext (`raw`)
   gemappt. Falls das Raw-Mapping ein *anderes*, nicht-`sonstig` Ergebnis liefert,
   wird dieses bevorzugt — es hat einen spezifischeren (längeren) Schlüssel
   gematcht.

Die Gegenprüfung überschreibt nur, wenn das Raw-Ergebnis nicht `sonstig` ist.
Dadurch bleiben Fälle unberührt, in denen `station_typ` manuell gesetzt oder
präziser als der Rohtext ist (z. B. „Beschlussempfehlung und Bericht" im
`station_typ`, während der Rohtext nur „Beschlussempfehlung" enthält).

**Implementierung:** `enum_mapper.py`, Funktion `_normalize_whitespace()`,
aufgerufen in `map_stationstyp()` und `map_dokumententyp()`.
`bawue_vorgaenge_scraper.py`, `_map_fundstelle_stationstyp()` (aus
`_build_station()` aufgerufen) — Gegenprüfung nach dem primären Mapping.

**Update (Issue #13): nur der Label-Teil des Rohtexts zählt.** Raw-Fallback und
Gegenprüfung mappen nicht mehr den ganzen Rohtext, sondern nur den Teil **vor**
der ersten Fundstellen-Referenz (`Drucksache`, `Plenarprotokoll` oder Datum
`TT.MM.JJJJ`). PARLIS hängt hinter die Plenarprotokoll-Referenz gelegentlich
eine Notiz an, deren Schlüsselwörter sonst den Stationstyp bestimmten
(V-230205, G9-Volksantrag):

- `Plenarprotokoll 17/85 … (Zulassung des Volksantrags)` → `parl-initiativ`
  statt `sonstig` (und damit keine DD-031-Wiederherstellung als Lesung);
- `Beratung   Plenarprotokoll 17/92 … (Gesetzentwurf wurde abgelehnt)` →
  `preparl-regbsl` statt `parl-vollvlsgn` (plus synthetische `parl-initiativ`,
  DD-012; das SPD-Änderungsantrag-Dokument fand keine Lesung und wurde verworfen).

Der Split-Fall oben bleibt abgedeckt: Der Zusatz „in Zweiter Beratung" steht vor
der Referenz. `map_dokumententyp()` bekommt weiter den vollen Text — der
`redeprotokoll`-Fallback für Plenarprotokolle (DD-031) hängt daran.
Offline-Rebuild aller 238 WP17/WP18-Vorgänge (PARLIS-Stand 28.09.2026): nur
V-230205 ändert sich. Test: `TestIssue13CitationNotesDoNotTypeStations`.
