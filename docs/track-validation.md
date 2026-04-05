# Track Validation: Analyse fuer Baden-Wuerttemberg

**Datum:** 04.04.2026
**Backend-Version:** v0.2.7 (Track Validation eingefuehrt)
**Issue:** [Issue 26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26)

---

## Hintergrund

Mit Backend v0.2.7 werden Vorgaenge gegen Track-Definitionen (DFA/Regex) validiert.
BW verwendet aktuell den BY-Track, der nicht zu den BaWue-PARLIS-Daten passt.

Quellen: [tracks.toml](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/tracks.toml), [Wiki — Track Validation](https://wiki.pazufa.de/books/backend-api/page/track-validation)

---

## Stations-Alphabet

| Buchstabe | Stationstyp      | Bedeutung                        |
|-----------|------------------|----------------------------------|
| `R`       | `preparl-regent` | Regierungsentwurf                |
| `E`       | `preparl-eckpup` | Eckpunktepapier                  |
| `S`       | `preparl-regbsl` | Kabinettsbeschluss               |
| `I`       | `parl-initiativ` | Parlamentarische Initiative      |
| `V`       | `parl-vollvlsgn` | Plenarlesung (1./2./3. Beratung) |
| `A`       | `parl-ausschber` | Ausschussberatung                |
| `J`       | `parl-akzeptanz` | Annahme/Verabschiedung           |
| `N`       | `parl-ablehnung` | Ablehnung                        |
| `Z`       | `parl-zurueckgz` | Zurueckgezogen                   |
| `G`       | `postparl-gsblt` | Gesetzblatt-Verkuendung          |
| `K`       | `postparl-kraft` | Inkrafttreten                    |
| `Y`       | `postparl-vesja` | Ausfertigung (Unterschrift MP)   |
| `X`       | `postparl-vesne` | Ausfertigung verweigert (Veto)   |

---

## BY-Track (aktuell BW zugewiesen)

```
gg-land-parl = "((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)"
```

| Regex-Teil | Bedeutung |
|------------|-----------|
| `((E*R+)?S)?` | Optionaler vorparlamentarischer Praefix: E + R + **S (Pflicht nach R)** |
| `I` | Parlamentarische Initiative (immer erforderlich) |
| `VA*` | Lesung (V) + Ausschussberatungen (A*) |
| `Z` | Ruecknahme |
| `VJGK` | 2. Lesung + Annahme + Gesetzblatt + Inkrafttreten (alle vier zusammen) |
| `VN` | 2. Lesung + Ablehnung |

---

## Probleme mit BY-Track fuer BW

| # | Problem | Schwere | Betroffene | Ursache |
|---|---------|---------|-----------|---------|
| P1 | R ohne S | KRITISCH | 118 Vorgaenge | BaWue-PARLIS kennt keine Kabinettsbeschluss-Fundstelle. `((E*R+)?S)?` verlangt S nach R. |
| P2 | J ohne vorheriges V | NIEDRIG | 1 Randfall | PARLIS liefert fast immer separate "Zweite Beratung" (V) vor Annahme (J). Nur selten verschmolzen. |
| P3 | N ohne vorheriges V | NIEDRIG | 1 Randfall | Gleiche Logik wie P2 — Ablehnung nach erster Lesung ohne separate zweite Lesung. |
| P4 | Y (Ausfertigung) im Pfad | NIEDRIG | 0 beobachtet | Track kennt nur `VJGK`, kein Y. Im Enum-Mapping vorhanden, aber nie im Dev-Lauf aufgetreten. |
| P5 | K (Inkrafttreten) fehlt | NIEDRIG | 135 Vorgaenge | PARLIS liefert G (Gesetzblatt) **zuverlaessig** (135/135), aber nie K. Inkrafttreten steht im Gesetzestext, nicht als Fundstelle. |
| P6 | Kein `gg-land-volk` Track | NIEDRIG | 0 beobachtet | Scraper mappt Volksantrag → `gg-land-volk`, aber kein Track dafuer definiert. |
| P7 | `sonstig` crasht Backend | KRITISCH | 27 Vorgaenge | "Mitteilung"/"Dokument" nicht in `STATIONSTYP_MAP` → `sonstig` → Backend-Panic bei `validate.rs:310:48` (HashMap `[]` statt `.get()`). |

Schweregrade basieren auf dem Dev-Lauf (171 Vorgaenge, 04.04.2026). Das Backend erzwingt
die Track-Regex aktuell **nicht** — es prueft nur, ob Stationstypen im Track-Alphabet
bekannt sind. Die Panics bei P7 sind der einzige echte Blocker.

---

## Vorgeschlagener BW-Track

```toml
[BW]
gg-land-parl = "R?I((VA*(Z|V?JY?GK?|V?N|VA*(Z|V?JY?GK?|V?N)))|Z)"
```

### Aenderungen gegenueber BY

| BY-Track | BW-Vorschlag | Begruendung |
|----------|-------------|-------------|
| `((E*R+)?S)?` | `R?` | S existiert nicht in BaWue-PARLIS. E wird nicht gescrapt. |
| `VJ` | `V?J` | Zweite Lesung vor Annahme nicht immer als separate Fundstelle (Randfaelle). |
| `VN` | `V?N` | Ablehnung kann nach erster Lesung ohne separate zweite Lesung erfolgen. |
| `GK` (Pflicht) | `GK?` | G ist Pflicht (135/135). K fehlt immer (0/204) — steht im Gesetzestext. |
| — | `Y?` nach J | Ausfertigung kann zwischen Annahme und Gesetzblatt stehen. |

### Gueltige Sequenzen

- `IZ` — eingereicht, zurueckgezogen
- `IVAVJG` — 1. Lesung, Ausschuss, 2. Lesung, Annahme, Gesetzblatt (haeufigster Fraktionsentwurf-Pfad, 14x)
- `RIVAVJG` — mit Regierungsentwurf (dominanter Pfad, 112x)
- `IVAVN` — abgelehnt nach 2. Lesung (11x)
- `IVAJG` — Annahme ohne separate 2. Lesung (Randfall)
- `IVN` / `IVAN` — Ablehnung nach 1. Lesung / nach Ausschuss
- `IVAVJGK` — vollstaendiger Zyklus mit Inkrafttreten (theoretisch, nie beobachtet)

### Strukturelle Unterschiede BW vs. BY

| Aspekt | Bayern (BY) | Baden-Wuerttemberg (BW) |
|--------|------------|------------------------|
| Kabinettsbeschluss (S) | Explizit im Datensatz | Nicht in PARLIS |
| 2. Lesung vor Abstimmung | Immer separates V vor J/N | Fast immer, nicht garantiert |
| Inkrafttreten (K) | Im Track als Pflicht | Nie als Fundstelle (steht im Gesetzestext) |
| Gesetzblatt (G) | Im Track als Pflicht | Pflicht — 135/135 angenommene Vorgaenge |

---

## Dev-Lauf Ergebnisse (04.04.2026)

**Backend:** v0.2.7 lokal | **Scraper:** 173 gefunden, 141 publiziert, 28 fehlgeschlagen

### Beobachtete Stationsfolgen (Top-Muster)

| Sequenz | Anzahl | BY-Track | Backend |
|---------|--------|----------|---------|
| `RIVAVJG` | 112 | FAIL | Akzeptiert |
| `I?VAVN` | 15 | FAIL | **PANIC** |
| `IVAVJG` | 14 | FAIL | Akzeptiert |
| `IVAVN` | 11 | PASS | Akzeptiert |
| `IV?AVN` | 3 | FAIL | **PANIC** |
| Sonstige (mit `?`) | 16 | FAIL | PANIC / Akzeptiert |

Legende: `?` = `sonstig` (nicht gemappte Fundstelle).

### Zentrale Erkenntnisse

1. **Backend erzwingt Track-Regex NICHT.** Es prueft nur, ob Stationstypen im Alphabet
   bekannt sind. 132 Vorgaenge mit BY-FAIL-Sequenzen wurden stillschweigend akzeptiert.
2. **`sonstig` crasht das Backend.** 27 Vorgaenge (108 Panics = 27 x 4 Retries) wegen
   HashMap-Panic bei `validate.rs:310:48`. Ursache: "Mitteilung" (35x) und "Dokument" (9x).
3. **P2/P3 waren ueberbewertet.** PARLIS liefert fast immer separate "Zweite Beratung"-
   Fundstellen. Das dominante Muster ist `IVAVJG`/`RIVAVJG`, nicht `IVAJ`.

---

## Scraper-Fixes (bei Track-Erzwingung)

| # | Fix | Prio | Betroffene | Beschreibung |
|---|-----|------|-----------|-------------|
| 1 | `sonstig` filtern | P0 | 28 Vorgaenge | Stationen mit Typ `sonstig` herausfiltern (`bawue_vorgaenge_scraper.py`, nach `_build_station()`). "Mitteilung" ist kontextabhaengig (DD-002), pauschales Mapping waere falsch. |
| 2 | Leere Stationen | P0 | 1 Vorgang | Vorgaenge ohne Stationen ueberspringen (Guard vor Postparl-Filter in `item_extractor()`). |
| 3 | Post-G abschneiden | P1 | ~5 Vorgaenge | Stationen nach Gesetzblatt (G) entfernen — PARLIS gibt Fundstellen in Seitenreihenfolge, nicht chronologisch. Nach Fix 1 verbleiben vereinzelt regulaere Stationen (z.B. A) nach G. |
| 4 | Doppelte I | P2 | 1 Vorgang | Zweite `parl-initiativ` deduplizieren. Ursache: PARLIS liefert zwei "Gesetzentwurf"-Fundstellen, DD-012 fuegt synthetisches I ein → doppeltes I. |
| 5 | Partielle Sequenzen | P2* | 4 Vorgaenge | In-Progress-Vorgaenge mit 1-2 Stationen (`R`→`RI`, `I`, `IV`). Entfaellt bei Praefix-Matching. |

\* Fix 5 nur noetig bei Full-Matching. Klaerung mit Backend-Team erforderlich.

### Erwartetes Ergebnis nach Fixes

167/171 Vorgaenge matchen den vorgeschlagenen BW-Track.
4 partielle Sequenzen (`RI`, `I`, `IV`) benoetigen Praefix-Matching oder werden uebersprungen.

### Abhaengigkeiten

| Abhaengigkeit | Status |
|--------------|--------|
| Backend implementiert BW-Track | Ausstehend |
| Praefix- vs. Full-Matching | Offen — bestimmt ob Fix 5 noetig |
| Backend: `sonstig` Panic beheben (`validate.rs:310`) | Ausstehend |
| `gg-land-volk` Track fuer Volksantraege | Offen (separates Issue) |

---

## Naechste Schritte

1. ~~Dev-Lauf durchfuehren~~ **Erledigt** (04.04.2026)
2. **Scraper-Fixes implementieren:** Fix 1+2 (P0) sofort, Fix 3+4 danach
3. **Backend-Bug melden:** Panic bei `validate.rs:310` — `.get()` statt `[]`
4. **BW-Track vorschlagen:** [Issue 26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26)
5. **`gg-land-volk` Track** separat klaeren

---

## Referenzen

- [tracks.toml](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/tracks.toml) — Track-Definitionen
- [Wiki: Track Validation](https://wiki.pazufa.de/books/backend-api/page/track-validation) — Dokumentation
- [Backend v0.2.7 Release](https://codeberg.org/PaZuFa/pazufa-backend/releases/tag/v0.2.7) — Release mit Track Validation
- `docs/design_decisions.md` — DD-010 (synth. Ablehnung), DD-011 (Whitespace), DD-012 (synth. Initiative)
- `src/bawue/enum_mapper.py` — Stationstyp-Zuordnungen
