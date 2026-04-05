# Track Validation: Analyse fuer Baden-Wuerttemberg

**Datum:** 04.04.2026 (Erstanalyse) | 05.04.2026 (Review-Update)
**Backend-Version:** v0.2.7 (Track Validation eingefuehrt)
**Issue:** [Issue 26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26)
**Review:** Crystalkey (Backend-Entwickler), 05.04.2026

---

## Hintergrund

Mit Backend v0.2.7 werden Vorgaenge gegen Track-Definitionen (DFA/Regex) validiert.
BW verwendet aktuell den BY-Track. Die Erstanalyse ergab 6 Abweichungen und schlug
einen eigenen BW-Track vor. Nach Review durch Crystalkey zeigt sich: **Die meisten
Abweichungen lassen sich scraper-seitig loesen.** Nur eine minimale Track-Aenderung
ist noetig.

Quellen: [tracks.toml](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/tracks.toml), [Wiki — Track Validation](https://wiki.pazufa.de/books/backend-api/page/track-validation)

---

## Stations-Alphabet

| Buchstabe | Stationstyp      | Bedeutung                            |
|-----------|------------------|--------------------------------------|
| `R`       | `preparl-regent` | Regierungsentwurf                    |
| `E`       | `preparl-eckpup` | Eckpunktepapier                      |
| `S`       | `preparl-regbsl` | Kabinettsbeschluss                   |
| `I`       | `parl-initiativ` | Parlamentarische Initiative          |
| `V`       | `parl-vollvlsgn` | Plenarlesung (1./2./3. Beratung)     |
| `A`       | `parl-ausschber` | Ausschussberatung                    |
| `J`       | `parl-akzeptanz` | Annahme/Verabschiedung               |
| `N`       | `parl-ablehnung` | Ablehnung                            |
| `Z`       | `parl-zurueckgz` | Zurueckgezogen                       |
| `G`       | `postparl-gsblt` | Gesetzblatt-Verkuendung              |
| `K`       | `postparl-kraft` | Inkrafttreten                        |
| `Y`       | `postparl-vesja` | Volksabstimmung (NICHT Ausfertigung) |
| `X`       | `postparl-vesne` | Volksabstimmung abgelehnt            |

---

## BY-Track (Referenz-Baseline)

```
gg-land-parl = "((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)"
```

| Regex-Teil    | Bedeutung                                                                              |
|---------------|----------------------------------------------------------------------------------------|
| `((E*R+)?S)?` | Optionaler vorparlamentarischer Praefix: E + R + S (Kabinettsbeschluss Pflicht nach R) |
| `I`           | Parlamentarische Initiative (immer erforderlich)                                       |
| `VA*`         | Lesung (V) + Ausschussberatungen (A*)                                                  |
| `Z`           | Ruecknahme                                                                             |
| `VJGK`        | 2. Lesung + Annahme + Gesetzblatt + Inkrafttreten                                      |
| `VN`          | 2. Lesung + Ablehnung                                                                  |

---

## Review-Ergebnisse (Crystalkey, 05.04.2026)

### Uebersicht

| # | Crystalkey-Punkt                     | Bewertung    | Auswirkung auf Track                                                       |
|---|--------------------------------------|--------------|----------------------------------------------------------------------------|
| 1 | Prefix-Matching ist inhaerent        | BESTAETIGT   | `GK?` unnoetig — `IVAVJG` ist gueltiger Praefix von `IVAVJGK`              |
| 2 | R als S umklassifizieren             | UEBERNOMMEN  | Scraper-Fix: `preparl-regent` → `preparl-regbsl`. BY-Praefix funktioniert. |
| 3 | V vor J — GO §42 verlangt 2 Lesungen | UEBERNOMMEN  | `V?J` unnoetig. 126/128 Annahmen haben zwei explizite V.                   |
| 4 | V vor N — Strukturvorschlag          | TEILWEISE    | Ablehnung nach 1. Lesung (IVN) zulassen. **Einzige Track-Aenderung.**      |
| 5 | K nicht abschwaechen                 | UEBERNOMMEN  | `GK` beibehalten. Prefix-Matching loest das Problem.                       |
| 6 | Y ≠ Ausfertigung                     | ZUR KENNTNIS | `postparl-vesja` = Volksabstimmung, nicht Unterschrift MP.                 |

### Punkt 1: Prefix-Matching ist inhaerent

Das Backend fuehrt immer Prefix-Matching durch — alle DFA-Zustaende sind akzeptierend.
Ein Vorgang ist gueltig, sobald seine bisherige Stationsfolge ein Praefix eines
gueltigen vollstaendigen Pfades ist.

**Konsequenz:** Unvollstaendige Vorgaenge (`R`, `SI`, `SIV`, `SIVAVJG`) sind
automatisch gueltig. Problem P5 (K fehlt, 135 Vorgaenge) ist damit kein Problem —
`IVAVJG` ist ein gueltiger Praefix von `IVAVJGK`. Unser `GK?`-Vorschlag war unnoetig.

### Punkt 2: R → S Umklassifizierung

PARLIS "Gesetzentwurf Landesregierung" zeigt den Entwurf **nach Kabinettsbeschluss**,
also den parlamentarischen Eingang. Die vorparlamentarische Entwurfsphase (R) wird
bereits vom Beteiligungsportal-Scraper abgedeckt.

**Aenderung:** `enum_mapper.py` — "Gesetzentwurf" + Initiator "Landesregierung"
wird zu `PREPARL_MINUS_REGBSL` (S) statt `PREPARL_MINUS_REGENT` (R) gemappt.

Auswirkung auf Sequenzen:
- `RIVAVJG` (112x) → `SIVAVJG` — matcht BY-Praefix `((E*R+)?S)?` ✓
- Beteiligungsportal + PARLIS zusammengefuehrt: `RSIVAVJG` — matcht ebenfalls ✓

Damit entfaellt Problem P1 (R ohne S) vollstaendig.

### Punkt 3: V vor J — GO §42 verlangt Zwei Lesungen

GO BW §42 schreibt mindestens zwei Beratungen (Lesungen) vor, §49 regelt die
Schlussabstimmung am Ende der letzten Beratung. Im Dev-Lauf haben 126 von 128
Annahme-Vorgaengen zwei explizite V-Stationen vor J.

**Konsequenz:** `V?J` ist unnoetig. `VJ` (wie im BY-Track) ist korrekt.

### Punkt 4: V vor N — Ablehnung nach 1. Lesung

Ablehnung (N) kann nach der 1. Lesung erfolgen, ohne dass eine separate 2. Lesung
stattfindet. Der BY-Track verlangt aktuell `VN` (2. Lesung + Ablehnung) an allen
Stellen. Um `IVN` (Ablehnung direkt nach 1. Lesung) zu ermoeglichen, muss die
Struktur angepasst werden.

**Dies ist die einzige Aenderung am Track-String.** Siehe "Vorgeschlagener BW-Track".

### Punkt 5: K (Inkrafttreten) beibehalten

Der Track soll nicht abgeschwaecht werden, nur weil PARLIS kein K liefert.
Inkrafttreten findet statt — es steht im Gesetzestext, nicht als PARLIS-Fundstelle.
Prefix-Matching akzeptiert `IVAVJG` als gueltigen Praefix von `IVAVJGK`.

### Punkt 6: Y = Volksabstimmung

`postparl-vesja` bedeutet Volksabstimmung (Ja), nicht Ausfertigung/Unterschrift MP.
Unser Y?-Vorschlag im Track war auf einer falschen Annahme aufgebaut. Y gehoert
nicht in den `gg-land-parl`-Track.

**Korrektur noetig:** Kommentar in `enum_mapper.py:70` ("Ausfertigung") korrigieren.

---

## Aktualisierte Problemtabelle

| #  | Problem                   | Schwere (alt) | Status nach Review  | Loesung                                                       |
|----|---------------------------|---------------|---------------------|---------------------------------------------------------------|
| P1 | R ohne S                  | KRITISCH      | **GELOEST**         | R → S Umklassifizierung im Scraper                            |
| P2 | J ohne vorheriges V       | NIEDRIG       | **GELOEST**         | GO §42 schreibt 2 Lesungen vor, kommt praktisch nicht vor     |
| P3 | N ohne vorheriges V       | NIEDRIG       | **TRACK-AENDERUNG** | IVN zulassen (Punkt 4)                                        |
| P4 | Y im Pfad                 | NIEDRIG       | **GELOEST**         | Y = Volksabstimmung, irrelevant fuer `gg-land-parl`           |
| P5 | K fehlt                   | NIEDRIG       | **GELOEST**         | Prefix-Matching akzeptiert `IVAVJG` als Praefix von `IVAVJGK` |
| P6 | Kein `gg-land-volk` Track | NIEDRIG       | OFFEN               | Separates Issue                                               |
| P7 | `sonstig` crasht Backend  | KRITISCH      | OFFEN               | Backend-Bug (`validate.rs:310`), scraper-seitig filtern       |

---

## Vorgeschlagener BW-Track

### Ausgangspunkt: BY-Track

```
((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)
```

### Schritt 1: Struktur identifizieren

Der innere Teil nach `I` lautet:

```
I(
  VA*(Z|VJGK|VN|VA*(Z|VJGK|VN))   ← 1. Lesung + Ausschuss + Alternativen
  | Z                               ← sofortige Ruecknahme
)
```

Nach der 1. Lesung (V) und optionalem Ausschuss (A*) gibt es `VN` als Option —
das verlangt eine **2. Lesung vor Ablehnung**. Fuer BW soll Ablehnung auch direkt
nach der 1. Lesung moeglich sein (`IVN`).

### Schritt 2: Einzige Aenderung — N nach 1. Lesung zulassen

```
I(
  V(
    N                               ← NEU: Ablehnung nach 1. Lesung
    | A*(Z|VJGK|VN|VA*(Z|VJGK|VN)) ← unveraendert: Ausschuss + Alternativen
  )
  | Z                               ← sofortige Ruecknahme (unveraendert)
)
```

### BW-Track (Ergebnis)

```toml
[BW]
gg-land-parl = "((E*R+)?S)?I(V(N|A*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)"
```

### Aenderung gegenueber BY

| BY-Track       | BW-Vorschlag      | Begruendung                             |
|----------------|-------------------|-----------------------------------------|
| `I((VA*(...))` | `I(V(N\|A*(...))` | Ablehnung nach 1. Lesung zulassen (IVN) |

Alles andere bleibt identisch zum BY-Track.

### Gueltige Sequenzen (mit R→S Umklassifizierung)

| Sequenz    | Anzahl | Beschreibung                                                                                |
|------------|--------|---------------------------------------------------------------------------------------------|
| `SIVAVJG`  | 112    | Regierungsentwurf: S + I(synth) + 1. Lesung + Ausschuss + 2. Lesung + Annahme + Gesetzblatt |
| `IVAVJG`   | 14     | Fraktionsentwurf: gleicher Pfad ohne vorparlamentarische Phase                              |
| `IVAVN`    | 11     | Ablehnung nach Ausschuss und 2. Lesung                                                      |
| `IVN`      | 0*     | Ablehnung nach 1. Lesung (theoretisch moeglich, NEU erlaubt)                                |
| `IZ`       | 0*     | Eingereicht, sofort zurueckgezogen                                                          |
| `SIVAVJGK` | 0*     | Vollstaendiger Zyklus mit Inkrafttreten (theoretisch, nie beobachtet)                       |

\* Nicht im Dev-Lauf beobachtet, aber gueltige Pfade.

---

## Dev-Lauf Ergebnisse (04.04.2026)

**Backend:** v0.2.7 lokal | **Scraper:** 173 gefunden, 141 publiziert, 28 fehlgeschlagen

### Beobachtete Stationsfolgen (Top-Muster)

| Sequenz            | Anzahl | BY-Track (nach R→S) | BW-Track           |
|--------------------|--------|---------------------|--------------------|
| `SIVAVJG`          | 112    | PASS                | PASS               |
| `IVAVJG`           | 14     | PASS                | PASS               |
| `IVAVN`            | 11     | PASS                | PASS               |
| `I?VAVN`           | 15     | **PANIC**           | **PANIC**          |
| `IV?AVN`           | 3      | **PANIC**           | **PANIC**          |
| Sonstige (mit `?`) | 16     | PANIC / Akzeptiert  | PANIC / Akzeptiert |

Legende: `?` = `sonstig` (nicht gemappte Fundstelle). Wird durch Fix 1 geloest.

### Zentrale Erkenntnisse (aktualisiert)

1. **Prefix-Matching bestaetigt.** Unvollstaendige Vorgaenge sind gueltig.
2. **R→S Umklassifizierung** loest 112 von 118 ehemaligen R-ohne-S-Fehlern.
3. **`sonstig` crasht das Backend** — 27 Vorgaenge (108 Panics). Weiterhin Blocker.
4. **BY-Track passt nach R→S fuer ~98% der Vorgaenge.** Nur IVN-Pfad fehlt.

---

## Scraper-Fixes

| # | Fix                     | Prio | Betroffene    | Beschreibung                                                                                                                      |
|---|-------------------------|------|---------------|-----------------------------------------------------------------------------------------------------------------------------------|
| 1 | `sonstig` filtern       | P0   | 28 Vorgaenge  | Stationen mit Typ `sonstig` herausfiltern. "Mitteilung"/"Dokument" nicht mappbar.                                                 |
| 2 | Leere Stationen         | P0   | 1 Vorgang     | Vorgaenge ohne Stationen ueberspringen (Guard vor Postparl-Filter).                                                               |
| 3 | ~~R → S Umklassifizierung~~ | ~~P0~~ | ~~112 Vorgaenge~~ | ✅ Erledigt. `enum_mapper.py`: `PREPARL_MINUS_REGBSL`. Methode umbenannt zu `_ensure_initiativ_after_regbsl()`. |
| 4 | Post-G abschneiden      | P1   | ~5 Vorgaenge  | Stationen nach Gesetzblatt (G) entfernen — PARLIS-Seitenreihenfolge ≠ chronologisch.                                              |
| 5 | Doppelte I              | P2   | 1 Vorgang     | Zweite `parl-initiativ` deduplizieren (PARLIS + DD-012 synth. I).                                                                 |

Fix 5 (partielle Sequenzen) aus der Erstanalyse **entfaellt** — Prefix-Matching bestaetigt.

### Erwartetes Ergebnis nach Fixes

167/171 Vorgaenge matchen den vorgeschlagenen BW-Track.
4 partielle Sequenzen (`S`, `SI`, `SIV`) sind gueltige Praefixe.

---

## Abhaengigkeiten

| Abhaengigkeit                                          | Status                               |
|--------------------------------------------------------|--------------------------------------|
| Backend implementiert BW-Track (nur Punkt-4-Aenderung) | Ausstehend — Issue #26 aktualisieren |
| Backend: `sonstig` Panic beheben (`validate.rs:310`)   | Ausstehend                           |
| `gg-land-volk` Track fuer Volksantraege                | Offen (separates Issue)              |

---

## Naechste Schritte

1. ~~Dev-Lauf durchfuehren~~ **Erledigt** (04.04.2026)
2. ~~Review durch Backend-Entwickler~~ **Erledigt** (05.04.2026)
3. **Scraper-Fixes implementieren:** Fix 1+2+3 (P0) sofort, Fix 4+5 danach
4. **Issue #26 aktualisieren:** Minimalen BW-Track vorschlagen (nur Punkt-4-Aenderung)
5. **Backend-Bug melden:** Panic bei `validate.rs:310` — `.get()` statt `[]`
6. **Validierungs-Lauf** nach Scraper-Fixes durchfuehren

---

## Referenzen

- [tracks.toml](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/tracks.toml) — Track-Definitionen
- [Wiki: Track Validation](https://wiki.pazufa.de/books/backend-api/page/track-validation) — Dokumentation
- [Backend v0.2.7 Release](https://codeberg.org/PaZuFa/pazufa-backend/releases/tag/v0.2.7) — Release mit Track Validation
- `docs/design_decisions.md` — DD-010 (synth. Ablehnung), DD-011 (Whitespace), DD-012 (synth. Initiative)
- `src/bawue/enum_mapper.py` — Stationstyp-Zuordnungen
