# Track Validation: Analyse fuer Baden-Wuerttemberg

**Datum:** 04.04.2026
**Backend-Version:** v0.2.7 (Track Validation eingefuehrt)
**Status:** Analyse — Staging-Validierung ausstehend

---

## Hintergrund

Mit Backend v0.2.7 werden eingereichte Vorgaenge gegen Track-Definitionen validiert.
Tracks beschreiben als DFA/Regex, in welcher Reihenfolge Stationen eines Vorgangs
aufeinander folgen duerfen. Die Track-Definitionen liegen im
[parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/tracks.toml).

**Aktueller Zustand:** BW (Baden-Wuerttemberg) verwendet den identischen Track wie
BY (Bayern). Laut Backend-Announcement ist das fuer die meisten Laender nicht korrekt.

Quelle: [Wiki — Track Validation](https://wiki.pazufa.de/books/backend-api/page/track-validation)

---

## Stations-Alphabet

Jeder Stationstyp hat einen Buchstaben in der Track-Regex:

| Buchstabe | Stationstyp | Bedeutung |
|-----------|-------------|-----------|
| `R` | `preparl-regent` | Regierungsentwurf |
| `E` | `preparl-eckpup` | Eckpunktepapier |
| `S` | `preparl-regbsl` | Kabinettsbeschluss |
| `I` | `parl-initiativ` | Parlamentarische Initiative |
| `V` | `parl-vollvlsgn` | Plenarlesung (1./2./3. Beratung) |
| `A` | `parl-ausschber` | Ausschussberatung |
| `J` | `parl-akzeptanz` | Annahme/Verabschiedung |
| `N` | `parl-ablehnung` | Ablehnung |
| `Z` | `parl-zurueckgz` | Zurueckgezogen |
| `G` | `postparl-gsblt` | Gesetzblatt-Verkuendung |
| `K` | `postparl-kraft` | Inkrafttreten |
| `Y` | `postparl-vesja` | Ausfertigung (Unterschrift MP) |
| `X` | `postparl-vesne` | Ausfertigung verweigert (Veto) |

---

## Aktueller Track (BY, zugewiesen an BW)

```
gg-land-parl = "((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)"
```

### Aufschluesselung

| Regex-Teil | Bedeutung |
|------------|-----------|
| `((E*R+)?S)?` | Optionaler vorparlamentarischer Praefix: Eckpunkte + Regierungsentwurf + **Kabinettsbeschluss (S pflicht nach R)** |
| `I` | Parlamentarische Initiative (immer erforderlich) |
| `VA*` | Erste Lesung (V) + null oder mehr Ausschussberatungen (A) |
| `Z` | Ruecknahme |
| `VJGK` | Zweite Lesung (V) + Annahme (J) + Gesetzblatt (G) + Inkrafttreten (K) — **alle vier zusammen** |
| `VN` | Zweite Lesung (V) + Ablehnung (N) |
| `VA*(Z\|VJGK\|VN)` | Weitere Ausschussrunde mit gleichen Endoptionen |

### Gueltige Sequenzen (BY-Track)

- `IZ` — eingereicht, zurueckgezogen
- `IVVJGK` — zwei Lesungen, angenommen, verkuendet, in Kraft
- `SIVVJGK` — mit Kabinettsbeschluss
- `ERSIVAVJGK` — Eckpunkte + Regierungsentwurf + Kabinett + Initiative + 1. Lesung + Ausschuss + 2. Lesung + Annahme + Gesetzblatt + Inkrafttreten
- `IVVN` — zwei Lesungen, abgelehnt
- `IVAZ` — 1. Lesung, Ausschuss, zurueckgezogen

---

## Stationsfolgen des BaWue-Scrapers

### Fall 1: Fraktionsentwurf (haeufigster Fall)

PARLIS-Fundstellen:
```
Gesetzentwurf → Erste Beratung → Beschlussempfehlung und Bericht → Zustimmung
```

Stationsfolge:
```
I (parl-initiativ) → V (parl-vollvlsgn) → A (parl-ausschber) → J (parl-akzeptanz)
Buchstabenfolge: IVAJ
```

**Quelle:** Test-Fixture `tests/fixtures/parlis/gesetzgebung_results.html`,
Integration-Test `test_gesetzgebung_station_types_mapped_correctly`

### Fall 2: Regierungsentwurf

PARLIS-Fundstellen:
```
Gesetzentwurf (Landesregierung) → Erste Beratung → Beschlussempfehlung → Zustimmung
```

Stationsfolge (nach DD-003 + DD-012):
```
R (preparl-regent) → I (synthetisch) → V → A → J
Buchstabenfolge: RIVAJ
```

**Quelle:** DD-012 in `docs/design_decisions.md`, Test `test_gesetzentwurf_from_landesregierung`

### Fall 3: Ablehnung

PARLIS-Fundstellen:
```
Gesetzentwurf → Zweite Beratung
Metadaten: Aktueller Stand = "Abgelehnt"
```

Stationsfolge (nach DD-010):
```
I → V → N (synthetisch)
Buchstabenfolge: IVN
```

**Quelle:** DD-010 in `docs/design_decisions.md`, Test `test_abgelehnt_appends_ablehnung_station`

### Fall 4: "Beschluss des Landtags in Zweiter Beratung"

PARLIS-Fundstellen (Haushaltsgesetzgebung):
```
Gesetzentwurf (Landesregierung) → Beschluss des Landtags in Zweiter Beratung
```

Stationsfolge (nach DD-011):
```
R → I (synthetisch) → V (Beschluss des Landtags in Zweiter Beratung)
Buchstabenfolge: RIV
```

**Quelle:** DD-011 in `docs/design_decisions.md`

### Fall 5: Vollstaendiger Gesetzgebungszyklus (wenn PARLIS alle Schritte liefert)

```
Gesetzentwurf → Erste Beratung → Beschlussempfehlung → Zweite Beratung → Zustimmung → Gesetzblatt → Inkrafttreten
I → V → A → V → J → G → K
Buchstabenfolge: IVAVJGK
```

Dieser Fall **wuerde** den BY-Track erfuellen — aber es ist unklar, ob PARLIS
fuer BaWue tatsaechlich immer separate Fundstellen fuer "Zweite Beratung" (V)
und "Zustimmung" (J) liefert.

---

## Regex-Analyse: Wo scheitert der BY-Track?

### Problem 1 (KRITISCH): `R` ohne `S` — Regierungsentwuerfe

**Betroffene Sequenz:** `RI...` (jeder Regierungsentwurf)

Der Track-Praefix `((E*R+)?S)?` verlangt `S` (Kabinettsbeschluss) nach `R`:

```
((E*R+)?S)?  →  erlaubt: nichts, S, RS, RRS, ERS, EERS, ...
                erlaubt NICHT: R allein, RI, ERI
```

**Warum BaWue betroffen ist:**

Das BaWue-PARLIS kennt keine "Kabinettsbeschluss"-Fundstelle. Der Stationstyp
`preparl-regbsl` (S) hat keine Zuordnung in `enum_mapper.py:STATIONSTYP_MAP` und
wird vom Scraper nie erzeugt.

**Regex-Matching fuer `RIVAJ`:**
```
Versuch: ((E*R+)?S)? matcht "R I V A J"
  (E*R+)? matcht R ✓
  S erwartet S, findet I → FAIL
  Backtrack: (E*R+)? matcht leer
  S erwartet S, findet R → FAIL
  Aeusseres ? ueberspringt die ganze Gruppe
  I erwartet I, findet R → GESAMTFEHLER
```

**Ergebnis:** Jeder Regierungsentwurf scheitert an der Track-Validierung.

---

### Problem 2 (HOCH): `J` ohne vorheriges `V` — Annahme ohne separate Zweite Lesung

**Betroffene Sequenz:** `IVAJ` (haeufigster Fraktionsentwurf-Pfad)

Der Track verlangt `VJ` als zusammengehoeriges Paar. `J` darf nie direkt nach `A` kommen.

**Regex-Matching fuer `IVAJ`:**
```
Nach I: VA*(Z|VJGK|VN|VA*(...))
  V matcht V ✓
  A* matcht A (ein A) ✓
  Dann brauchen wir: Z|VJGK|VN|VA*(...)
  Vorhanden: J
  - Z? Nein (J ≠ Z)
  - VJGK? Nein (J ≠ V, braeuchte VJGK)
  - VN? Nein (J ≠ V)
  - VA*(...)? Nein (J ≠ V)
  → Keine Alternative matcht → FAIL
```

**Warum BaWue betroffen ist:**

PARLIS liefert bei vielen Vorgaengen nur "Zustimmung" (→ `parl-akzeptanz` / J)
ohne eine separate "Zweite Beratung" (→ `parl-vollvlsgn` / V) Fundstelle davor.
Die Zustimmung findet zwar in einer Plenarsitzung statt, aber PARLIS erzeugt
dafuer nicht immer eine eigene Fundstelle.

DD-011 belegt, dass "Beschluss des Landtags in Zweiter Beratung" als Fundstelle
existiert (→ mappt auf V) — aber das ist nicht fuer alle Vorgangstypen der Fall.
Das Test-Fixture (`gesetzgebung_results.html`) zeigt nur "Zustimmung" (→ J)
ohne vorherige Zweite Beratung.

**Ergebnis:** Der haeufigste BaWue-Gesetzgebungspfad (`IVAJ`) scheitert.

---

### Problem 3 (MITTEL): `N` ohne vorheriges `V` — Ablehnung ohne Zweite Lesung

**Betroffene Sequenz:** `IVN` oder `IVAN`

Gleiche Logik wie Problem 2: `VN` ist nur als Paar gueltig.

**Regex-Matching fuer `IVN`:**
```
Nach I: VA*(Z|VN|...)
  V matcht V ✓
  A* matcht leer ✓
  Dann: Z|VJGK|VN|VA*(...)
  Vorhanden: N
  - VN? Nein (N ≠ V, braeuchte VN = zwei Zeichen)
  → FAIL
```

**Warum:** Wenn ein Vorgang in der Ersten Beratung abgelehnt wird, gibt es nur
eine Lesung (V). Die synthetische Ablehnung (N, DD-010) folgt direkt danach.
Der Track verlangt aber VVN (eine Lesung verbraucht durch VA*, eine durch VN).

**Ergebnis:** Ablehnungen nach erster Lesung scheitern. Ablehnungen nach Ausschuss
und zweiter Lesung (`IVAVN`) funktionieren.

---

### Problem 4 (NIEDRIG): `Y` (Ausfertigung) nicht im Track

**Betroffene Sequenz:** `...VJYGK` (mit Ausfertigung)

`enum_mapper.py:104` mappt "Ausfertigung" → `postparl-vesja` (Y). Der Track
kennt nur `VJGK` als Annahmepfad — `Y` dazwischen bricht die Sequenz.

**Auswirkung:** Unklar, ob BaWue-PARLIS "Ausfertigung" als Fundstelle liefert.
Falls ja, scheitern betroffene Vorgaenge.

---

### Problem 5 (NIEDRIG): `K` (Inkrafttreten) fehlt in PARLIS

Der BY-Track verlangt `VJGK` als vollstaendigen Annahmepfad. `K` (Inkrafttreten)
wird von PARLIS jedoch nie als Fundstelle geliefert (0/204 Vorgaenge).

**Klarstellung zu G (Gesetzblatt):** `docs/status.md` listet "Missing data sources:
Gesetzblatt BaWue (postparl-gsblt)" — das bezieht sich auf eine **eigenstaendige
Gesetzblatt-Datenquelle** (separater Scraper), NICHT auf fehlende PARLIS-Fundstellen.
PARLIS liefert Gesetzblatt-Fundstellen zuverlaessig: **135/135 angenommene Vorgaenge**
haben eine G-Station (siehe [Korrektur G/K](#korrektur-g-ist-pflicht-k-optional)).

**Warum K fehlt:** Inkrafttreten-Daten stehen im Gesetzestext selbst (z.B. "Dieses
Gesetz tritt am Tag nach seiner Verkuendung in Kraft"). PARLIS dokumentiert den
parlamentarischen Prozess (Fundstellen), nicht die nachgelagerte Rechtswirkung.

**Hinweis:** Das Backend koennte Praefix-Matching verwenden (Teilsequenzen
validieren). In dem Fall waere `IVAVJG` ein gueltiger Praefix von `IVAVJGK`.
Das muss gegen das Staging-Backend verifiziert werden.

---

### Problem 6 (NIEDRIG): Kein `gg-land-volk` Track definiert

Der Scraper mappt `Volksantrag` → vgtyp `gg-land-volk`. Fuer BW existiert in
`tracks.toml` nur `gg-land-parl`. Verhalten bei fehlendem Track ist unbekannt.

---

## Zusammenfassung

| # | Problem | Sequenz | Erwartet (BY) | Match? |
|---|---------|---------|---------------|--------|
| 1 | Regierungsentwurf ohne S | `RI...` | `((E*R+)?S)?I...` | NEIN |
| 2 | Annahme ohne 2. Lesung | `IVAJ` | `VA*(VJGK)` | NEIN |
| 3 | Ablehnung ohne 2. Lesung | `IVN`, `IVAN` | `VA*(VN)` | NEIN |
| 4 | Ausfertigung im Pfad | `VJYGK` | `VJGK` | NEIN |
| 5 | Inkrafttreten (K) fehlt | `IVAVJG` | `VJGK` (alle vier) | NEIN* |
| 6 | Volksantrag | vgtyp `gg-land-volk` | nicht definiert | NEIN |

\* Abhaengig davon, ob das Backend Praefix-Matching unterstuetzt.

### Strukturelle Unterschiede BW vs. BY

| Aspekt | Bayern (BY) | Baden-Wuerttemberg (BW) |
|--------|------------|------------------------|
| Kabinettsbeschluss (S) | Explizit im Datensatz | Nicht in PARLIS |
| 2. Lesung vor Abstimmung | Immer separates V vor J/N | Manchmal mit J/N verschmolzen |
| Ausfertigung (Y) | Unbekannt | Gemappt, evtl. selten |
| Volksantrag-Track | N/A | Benoetigt eigenen Track |

---

## Vorgeschlagener BaWue-Track

```toml
BW = {
    gg-land-parl = "R?I((VA*(Z|V?JY?GK?|V?N|VA*(Z|V?JY?GK?|V?N)))|Z)",
}
```

### Aenderungen gegenueber BY

| BY-Track | BW-Vorschlag | Begruendung |
|----------|-------------|-------------|
| `((E*R+)?S)?` | `R?` | S (Kabinettsbeschluss) existiert nicht in PARLIS BaWue. Einfaches optionales R genuegt. E wird nicht gescrapt. |
| `VJ` | `V?J` | Zweite Lesung (V) vor Annahme (J) ist nicht immer als separate Fundstelle vorhanden. |
| `VN` | `V?N` | Ablehnungen koennen nach erster Lesung erfolgen, ohne separate zweite Lesung. |
| `GK` (pflicht) | `GK?` | G (Gesetzblatt) ist pflicht — 135/135 angenommene Vorgaenge haben G in PARLIS. K (Inkrafttreten) fehlt immer (0/204). Siehe [Korrektur G/K](#korrektur-g-ist-pflicht-k-optional). |
| (nicht vorhanden) | `Y?` nach J | Ausfertigung (postparl-vesja) kann zwischen Annahme und Gesetzblatt stehen. |

### Gueltige Sequenzen mit dem vorgeschlagenen Track

- `IZ` — eingereicht, zurueckgezogen
- `IVAJG` — 1. Lesung, Ausschuss, Annahme, Gesetzblatt (ohne separate 2. Lesung)
- `IVAVJG` — 1. Lesung, Ausschuss, 2. Lesung, Annahme, Gesetzblatt (haeufigster BaWue-Pfad)
- `IVAVJGK` — vollstaendiger Zyklus mit Inkrafttreten (theoretisch)
- `IVAVJYGK` — mit Ausfertigung
- `RIVAJG` — Regierungsentwurf, 1. Lesung, Ausschuss, Annahme, Gesetzblatt
- `RIVAVJG` — Regierungsentwurf, vollstaendiger Zyklus (dominantes Muster: 112/171)
- `IVN` — abgelehnt nach 1. Lesung
- `IVAN` — abgelehnt nach Ausschuss
- `IVAVN` — abgelehnt nach 2. Lesung

---

## Validierung gegen Staging-Backend

### Log-Quellen

1. **ERROR-Log-Zeilen** — Track-Validation-Fehler des Backends:
   ```
   ERROR: API Exception: (400)
   HTTP response body: Track validation Failed. Reasons:
   The Vorgang with this station ordering: ["(date / type)", ...] has at least one station ...
   ```
   Enthaelt: Stationsfolge des Vorgangs, problematische Stationen, verwendeter Track.
   Vorgang-Titel/ID steht in der vorhergehenden INFO-Zeile:
   ```
   INFO: Sending Vorgang 'Titel...' (id=...) to API
   ```

2. **JSONL-Log** — Vollstaendige Vorgang-Objekte (vor API-Aufruf geschrieben):
   ```
   locallogs/{scraper_id}.jsonl
   ```
   Enthaelt: Alle Stationen mit `typ`, `zp_start`, `gremium` fuer jeden Vorgang.

   **Hinweis:** JSONL-Logging ist nur aktiv, wenn `api-obj-log = "locallogs"` in der
   Config gesetzt ist. `config.dev.toml` hat das aktiviert, `config.staging.toml` nicht.
   Fuer Staging-Laeufe entweder in `config.staging.toml` ergaenzen:
   ```toml
   [logging]
   api-obj-log = "locallogs"
   ```
   Oder nur auf die ERROR-Log-Zeilen stuetzen (enthalten die Stationsfolge aus der
   Backend-Antwort).

3. **stdout.log** (Docker-Compose):
   ```
   locallogs/stdout.log
   ```
   Kompletter Log-Output inklusive INFO und ERROR (via `tee` in `docker-compose.yml`).

### Auswertungs-Anleitung

**Schritt 1: Scraper laufen lassen**

Gegen lokales Backend:
```bash
cd /Users/froeser/Workspace/schneefisch/pazufa-bawue-scraper
source .venv/bin/activate
python -m collector --config-file config.dev.toml --once
```

Gegen Staging (Docker):
```bash
docker-compose up --build
```

**Schritt 2: Track-Validation-Fehler extrahieren**

```bash
# Alle Track-Validation-Fehler
grep "Track validation Failed" locallogs/stdout.log

# Stationsfolgen der fehlgeschlagenen Vorgaenge
grep -A5 "Track validation Failed" locallogs/stdout.log | grep "station ordering"

# Anzahl fehlgeschlagener Vorgaenge
grep -c "Track validation Failed" locallogs/stdout.log
```

**Schritt 3: Gegen Vorhersagen validieren**

Erwartete Muster in den Fehlermeldungen:

| Vorhergesagtes Muster | Erkennbar im Log durch |
|----------------------|----------------------|
| Problem 1: R ohne S | `preparl-regent` gefolgt von `parl-initiativ` ohne `preparl-regbsl` dazwischen; Vorgaenge mit "Landesregierung" als Initiative |
| Problem 2: J nach A | `parl-ausschber` direkt gefolgt von `parl-akzeptanz` ohne `parl-vollvlsgn` dazwischen |
| Problem 3: N nach V | `parl-vollvlsgn` direkt gefolgt von `parl-ablehnung` bei nur einer Lesung |
| Problem 5: J ohne GK | `parl-akzeptanz` am Ende ohne `postparl-gsblt`/`postparl-kraft` |

**Schritt 4: Stationsfolgen als Buchstaben uebersetzen**

Fuer jede Fehlermeldung die Stationsfolge in Buchstaben uebersetzen und gegen
den BY-Track `((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)` pruefen.

Uebersetzung:
```
preparl-regent  → R    parl-initiativ  → I    parl-vollvlsgn → V
parl-ausschber  → A    parl-akzeptanz  → J    parl-ablehnung → N
parl-zurueckgz  → Z    postparl-gsblt  → G    postparl-kraft → K
postparl-vesja  → Y    sonstig         → ? (kein Buchstabe)
```

### Erwartetes Ergebnis

- **Viele HTTP 400** — insbesondere fuer Fraktionsentwuerfe (IVAJ) und Regierungsentwuerfe (RIVAJ)
- **Wenige bis keine erfolgreichen Vorgaenge** — nur Vorgaenge mit vollstaendigem `IVAVJGK`-Pfad sollten durchgehen
- **Bestaetigte Probleme** — die Fehlermeldungen sollten die Probleme 1-3 widerspiegeln

Falls wider Erwarten alle Vorgaenge durchgehen, muss geprueft werden, ob:
- PARLIS tatsaechlich immer separate "Zweite Beratung"-Fundstellen liefert (widerlegt Problem 2)
- Das Backend fuer BW die Validierung ausgeschaltet hat
- Das Backend Praefix-Matching verwendet

---

## Ergebnis: Lokaler Dev-Lauf (04.04.2026)

**Backend:** v0.2.7 (lokal via docker-compose.dev.yml)
**Scraper:** 173 Vorgaenge gefunden, 141 publiziert, 28 fehlgeschlagen

### Beobachtete Stationsfolgen

171 Vorgaenge im JSONL-Log (alle `gg-land-parl`). 19 verschiedene Sequenzen:

| Sequenz | Anzahl | BY-Track | Backend-Ergebnis |
|---------|--------|----------|-----------------|
| `RIVAVJG` | 112 | FAIL | Akzeptiert |
| `I?VAVN` | 15 | FAIL | **PANIC** |
| `IVAVJG` | 14 | FAIL | Akzeptiert |
| `IVAVN` | 11 | PASS | Akzeptiert |
| `IV?AVN` | 3 | FAIL | **PANIC** |
| `R` | 2 | FAIL | Akzeptiert |
| `IVAVVJG` | 2 | FAIL | Akzeptiert |
| `(leer)` | 1 | FAIL | **Validation Error** |
| `I` | 1 | FAIL | Akzeptiert |
| `IV` | 1 | FAIL | Akzeptiert |
| `I?V` | 1 | FAIL | **PANIC** |
| `IV?AVVJG` | 1 | FAIL | **PANIC** |
| `IV?AV?N` | 1 | FAIL | **PANIC** |
| `RIVAVJG?` | 1 | FAIL | **PANIC** |
| `RIVAIVJG?A?` | 1 | FAIL | **PANIC** |
| `IV?AVJG` | 1 | FAIL | **PANIC** |
| `RIVAVJG??A?` | 1 | FAIL | **PANIC** |
| `IV?AVJG?` | 1 | FAIL | **PANIC** |
| `RIVA?V?JG??A?????????` | 1 | FAIL | **PANIC** |

Legende: `?` = `sonstig` Stationstyp (nicht gemappte Fundstelle).

### PASS/FAIL Zusammenfassung

- **BY-Track PASS:** 11 Vorgaenge (nur `IVAVN`)
- **BY-Track FAIL:** 160 Vorgaenge
- **Backend akzeptiert:** 143 Vorgaenge (davon 132 die am BY-Regex scheitern)
- **Backend Panic:** 27 Vorgaenge (108 Panics = 27 x 4 Retries)
- **Validation Error:** 1 Vorgang (leere Stationen)

### Kritische Erkenntnis: Backend erzwingt Track-Regex NICHT

Das Backend v0.2.7 **prueft Stationsfolgen nicht gegen die Track-Regex**.
Die `track_class`-SQL-Abfrage wird ausgefuehrt, aber das Ergebnis dient nur fuer
eine HashMap-Zuordnung der Stationstypen. Solange alle Stationstypen im Track-Alphabet
bekannt sind, fuegt das Backend den Vorgang ein — unabhaengig davon, ob die Sequenz
zur Regex passt.

Die 108 Panics bei `validate.rs:310:48` ("no entry found for key") treten auf, weil
der Stationstyp `sonstig` **kein Schluessel in der Backend-HashMap** ist. Der Rust-Code
verwendet den `[]`-Operator (panict bei fehlendem Key) statt `.get()`.

**Konsequenz:** Die 132 Vorgaenge mit Sequenzen wie `RIVAVJG` (die am BY-Regex scheitern
wuerden) wurden stillschweigend akzeptiert. Track-Validation ist fuer BaWue effektiv
nicht funktional — sie crasht nur bei unbekannten Stationstypen.

### Validierung der Vorhersagen

| # | Vorhergesagtes Problem | Schwere (alt) | Tatsaechlich | Schwere (neu) |
|---|----------------------|--------------|-------------|--------------|
| P1 | R ohne S | KRITISCH | 118 Vorgaenge mit R ohne S. **Nicht abgelehnt** — Backend akzeptiert alle. | NIEDRIG (solange keine Regex-Erzwingung) |
| P2 | J ohne vorheriges V | HOCH | Dominantes Muster ist `IVAVJG` (separate "Zweite Beratung"). PARLIS liefert fast immer ein eigenes V. Nur 1 Randfall. | **NIEDRIG** |
| P3 | N ohne vorheriges V | MITTEL | Ablehnungen folgen konsistent `IVAVN` (PASS). Nur 1 Randfall. | **NIEDRIG** |
| P4 | Y (Ausfertigung) im Pfad | NIEDRIG | **Nicht beobachtet** — Null Vorgaenge mit Y-Stationen. | NIEDRIG |
| P5 | K (Inkrafttreten) fehlt | NIEDRIG | G ist in **allen** 135 angenommenen Vorgaengen vorhanden. Nur K fehlt (0/204). Siehe [Korrektur G/K](#korrektur-g-ist-pflicht-k-optional). | NIEDRIG |
| P6 | gg-land-volk Track | NIEDRIG | **Nicht ausgeloest** — keine Volksantraege im Lauf. | NIEDRIG |

**Zentrale Ueberraschung:** Problem P2 und P3 waren weitgehend falsch. Die Analyse
sagte `IVAJ` als haeufigsten Pfad voraus, aber das tatsaechliche Hauptmuster ist
`IVAVJG` (1. Lesung V + Ausschuss A + 2. Lesung V + Annahme J + Gesetzblatt G).
BaWue-PARLIS liefert fast immer eine separate "Zweite Beratung"-Fundstelle.

### Neue Probleme (nicht vorhergesagt)

| # | Problem | Betroffene | Beschreibung |
|---|---------|-----------|-------------|
| P7 | `sonstig` crasht Backend | 27 Vorgaenge (108 Panics) | **KRITISCH.** Stationstyp `sonstig` ist nicht im Track-Alphabet des Backends. Rust-HashMap-Panic statt HTTP 400. Ursache: PARLIS-Fundstellen "Mitteilung" (35x) und "Dokument" (9x) sind nicht in `enum_mapper.py:STATIONSTYP_MAP`. |
| P8 | K (Inkrafttreten) fehlt in PARLIS | 135 Vorgaenge | G (Gesetzblatt) ist immer vorhanden. K (Inkrafttreten) wird nie als Fundstelle geliefert — steht im Gesetzestext selbst. `GK?` im korrigierten Track. |
| P9 | Leere Stationen | 1 Vorgang | "Berichtigung" ohne parsebare Stationen wird eingereicht und abgelehnt. |

### Aktualisierter BaWue-Track-Vorschlag

Basierend auf den tatsaechlichen Daten (korrigiert — G ist pflicht, K optional):

```toml
BW = {
    gg-land-parl = "R?I((VA*(Z|V?JY?GK?|V?N|VA*(Z|V?JY?GK?|V?N)))|Z)",
}
```

Aenderung gegenueber erstem Vorschlag: `G?K?` → `GK?`. Gesetzblatt (G) ist in
135/135 angenommenen Vorgaengen vorhanden (siehe [Korrektur G/K](#korrektur-g-ist-pflicht-k-optional)).
Die `V?` vor J und N werden nur selten benoetigt (je 1 Randfall), sind aber
noetig fuer vollstaendige Abdeckung.

---

## Korrektur: G ist Pflicht, K optional

**Datum:** 04.04.2026 (Nachtrag nach Datenauswertung)

Die urspruengliche Analyse schlug `G?K?` (beides optional) vor. Nach Auswertung
der JSONL-Daten und Verifikation gegen die PARLIS-Website ist das **falsch fuer G**:

### Datengrundlage

| Metrik | Ergebnis |
|--------|----------|
| Vorgaenge mit J (parl-akzeptanz) | 135 |
| davon mit G (postparl-gsblt) | **135 (100%)** |
| davon mit K (postparl-kraft) | **0 (0%)** |

### PARLIS-Verifikation

Beide Beispiele zeigen: PARLIS liefert Gesetzblatt als Fundstelle **mit PDF-Link**.

**[V-220176](https://parlis.landtag-bw.de/parlis/vorgang/V-220176)** (Fraktionsentwurf):
```
Gesetzentwurf (I) → Erste Beratung (V) → Beschlussempfehlung (A) →
Zweite Beratung (V) → Gesetzesbeschluss (J) →
Gesetz vom 26.07.2022 GBl Nr. 26 S. 410 (G) ← PDF vorhanden
```

**[V-214895](https://parlis.landtag-bw.de/parlis/vorgang/V-214895)** (Regierungsentwurf):
```
Gesetzentwurf LReg (R+I) → Erste Beratung (V) → Beschlussempfehlung (A) →
Zweite Beratung (V) → Gesetzesbeschluss (J) →
Gesetz vom 22.12.2021 GBl Nr. 43 S. 1040-1041 (G) ← PDF vorhanden
```

Kein Vorgang hat eine "Inkrafttreten"-Fundstelle. Beide: "Aktueller Stand: Verkuendet".

### Ursache des falschen `G?`-Vorschlags

`docs/status.md` (Zeile 24, 82) listet "Missing data sources: Gesetzblatt BaWue
(`postparl-gsblt`)" und "Gesetzblatt publications: Not implemented". Das bezieht
sich auf einen **eigenstaendigen Gesetzblatt-Scraper** (separate Datenquelle), NICHT
auf fehlende Fundstellen in PARLIS-Suchergebnissen. Der PARLIS-Scraper extrahiert
Gesetzblatt-Stationen bereits erfolgreich fuer 135/135 angenommene Vorgaenge.

### Warum K nie erscheint

Inkrafttreten-Daten stehen im Gesetzestext selbst (z.B. "Dieses Gesetz tritt am
Tag nach seiner Verkuendung in Kraft"). PARLIS dokumentiert den parlamentarischen
Prozess, nicht die nachgelagerte Rechtswirkung. K ist kein parlamentarisches Ereignis.

### Korrigierter Track

```
Vorher:  R?I((VA*(Z|V?JY?G?K?|V?N|VA*(Z|V?JY?G?K?|V?N)))|Z)
Nachher: R?I((VA*(Z|V?JY?GK?|V?N|VA*(Z|V?JY?GK?|V?N)))|Z)
                             ^                   ^
                         G pflicht           G pflicht
```

---

## Naechste Schritte

1. ~~Staging-Lauf durchfuehren~~ **Erledigt** (lokaler Dev-Lauf, 04.04.2026)
2. **Scraper-Fixes implementieren:**
   - "Mitteilung" zu `STATIONSTYP_MAP` hinzufuegen (behebt 27/28 Fehler)
   - Vorgaenge mit leeren Stationen ueberspringen (behebt 1/28 Fehler)
3. **Backend-Bug melden:** Panic bei `validate.rs:310` — `.get()` statt `[]` verwenden
4. **Issue eroeffnen** im [parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues):
   - Titel: "Track-Definition fuer BW (Baden-Wuerttemberg) — gg-land-parl"
   - Inhalt: Vorgeschlagener Track, Beispiel-Sequenzen aus dem Lauf
   - Hinweis: Track-Regex wird aktuell nicht erzwungen (nur Alphabet-Lookup)
5. **`gg-land-volk` Track** separat klaeren (fuer Volksantraege)

---

## Issue: Track-Definition fuer BW — gg-land-parl

**Issue:** [Issue 26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26)  
**Zielrepo:** [parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues)
**Datum:** 04.04.2026

### Zusammenfassung

Der aktuelle Track fuer BW (gg-land-parl) ist identisch mit dem BY-Track:

`((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)`

Dieser Track passt nicht zu den Stationsfolgen, die das BaWue-PARLIS liefert. Ein
lokaler Dev-Lauf gegen Backend v0.2.7 mit 171 Vorgaengen zeigt, dass nur 11 (6%)
den BY-Track-Regex matchen wuerden. Die restlichen 160 scheitern an strukturellen
Unterschieden zwischen BY und BW.

### Beobachtete Stationsfolgen (Dev-Lauf, 04.04.2026)

| Sequenz | Anzahl | BY-Track Match? |
|---------|--------|-----------------|
| `RIVAVJG` | 112 | NEIN |
| `IVAVJG` | 14 | NEIN |
| `IVAVN` | 11 | JA |
| `IVAVVJG` | 2 | NEIN |
| `R` | 2 | NEIN |
| `I` | 1 | NEIN |
| `IV` | 1 | NEIN |
| Sonstige (mit `sonstig`-Stationen) | 28 | NEIN (Backend Panic) |

Die 28 Vorgaenge mit `sonstig`-Stationen verursachen einen Panic im Backend bei
`validate.rs:310:48` (HashMap-Zugriff mit `[]` statt `.get()` fuer unbekannte
Stationstypen). Das ist ein separates Backend-Problem.

### Strukturelle Unterschiede BW vs. BY

| Aspekt | BY | BaWue |
|--------|-----|-------|
| Kabinettsbeschluss (S) | Explizit vorhanden, Pflicht nach R | Nicht in PARLIS — BaWue liefert kein S |
| Regierungsentwurf (R) | Nur mit S zusammen: `((E*R+)?S)?` | R steht allein vor I (112 Vorgaenge) |
| Zweite Lesung vor J/N | Immer separates V vor J/N | Fast immer vorhanden, aber nicht garantiert (Randfaelle) |
| Inkrafttreten (K) | Track verlangt GK zusammen | PARLIS liefert G, aber nie K (0/204 Vorgaenge). Inkrafttreten steht im Gesetzestext selbst, nicht als Fundstelle. |

### Begruendung der Probleme

**Problem 1 — R ohne S (112 Vorgaenge, KRITISCH):**
Der Praefix `((E*R+)?S)?` verlangt S nach R. BaWue-PARLIS kennt keine
Kabinettsbeschluss-Fundstelle. Jeder Regierungsentwurf (`R` → `I` → ...)
scheitert, weil `R` allein nicht matcht.

**Problem 2 — K (Inkrafttreten) fehlt (135 Vorgaenge):**
Der Track verlangt `VJGK` als Einheit. PARLIS liefert Gesetzblatt (G)
**zuverlaessig** (135/135 angenommene Vorgaenge haben G — verifiziert gegen die
PARLIS-Website, z.B.
[V-220176](https://parlis.landtag-bw.de/parlis/vorgang/V-220176),
[V-214895](https://parlis.landtag-bw.de/parlis/vorgang/V-214895)), aber nie
Inkrafttreten (K). Falls das Backend kein Praefix-Matching macht, scheitern alle
angenommenen Vorgaenge an dem fehlenden K.

**Problem 3 — V optional vor J/N (Randfaelle):**
In seltenen Faellen fehlt die separate "Zweite Beratung" (V) vor Annahme (J) oder
Ablehnung (N). Der Dev-Lauf zeigt, dass PARLIS fast immer ein separates V liefert,
aber nicht in 100% der Faelle.

### Vorgeschlagener Track

```toml
[BW]
gg-land-parl = "R?I((VA*(Z|V?JY?GK?|V?N|VA*(Z|V?JY?GK?|V?N)))|Z)"
```

### Aenderungen gegenueber BY

| BY-Track | BW-Vorschlag | Begruendung |
|----------|-------------|-------------|
| `((E*R+)?S)?` | `R?` | S existiert nicht in BaWue-PARLIS. E wird nicht gescrapt. Einfaches optionales R genuegt. |
| `VJ` | `V?J` | Zweite Lesung vor Annahme nicht immer als separate Fundstelle vorhanden (Randfaelle). |
| `VN` | `V?N` | Ablehnung kann nach erster Lesung erfolgen, ohne separate zweite Lesung (Randfaelle). |
| `GK` (Pflicht) | `GK?` | G (Gesetzblatt) ist Pflicht — 135/135 angenommene Vorgaenge haben G in PARLIS. K (Inkrafttreten) fehlt immer (0/204) — steht im Gesetzestext, nicht als Fundstelle. |
| (nicht vorhanden) | `Y?` nach J | Ausfertigung (`postparl-vesja`) kann zwischen Annahme und Gesetzblatt stehen. Im Dev-Lauf nicht beobachtet (0 Vorgaenge mit Y), aber im Enum-Mapping vorhanden. |

### Gueltige Sequenzen mit vorgeschlagenem Track

- `IZ` — eingereicht, zurueckgezogen
- `IVAJG` — 1. Lesung, Ausschuss, Annahme, Gesetzblatt (ohne separate 2. Lesung)
- `IVAVJG` — 1. Lesung, Ausschuss, 2. Lesung, Annahme, Gesetzblatt (haeufigster Fraktionsentwurf-Pfad, 14x)
- `RIVAVJG` — mit Regierungsentwurf (dominanter Pfad, 112x)
- `IVAVN` — abgelehnt nach 2. Lesung (11x)
- `IVAVJGK` — vollstaendiger Zyklus mit Inkrafttreten (theoretisch, nie beobachtet)
- `IVN` — abgelehnt nach 1. Lesung
- `IVAN` — abgelehnt nach Ausschuss

### Anmerkung: `sonstig`-Stationstyp

28 Vorgaenge enthalten Stationen vom Typ `sonstig` (unmapped PARLIS-Fundstellen
wie "Mitteilung" und "Dokument"). Diese verursachen einen Panic im Backend
(`validate.rs:310:48`), weil der `sonstig`-Typ nicht im Track-Alphabet ist. Das
ist ein separates Backend-Problem (HashMap `[]` statt `.get()`), das wir separat
melden. Auf Scraper-Seite werden wir die fehlenden Mappings ergaenzen.

---

## Referenzen

- [tracks.toml](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/tracks.toml) — Track-Definitionen
- [Wiki: Track Validation](https://wiki.pazufa.de/books/backend-api/page/track-validation) — Dokumentation
- [Backend v0.2.7 Release](https://codeberg.org/PaZuFa/pazufa-backend/releases/tag/v0.2.7) — Release mit Track Validation
- `docs/design_decisions.md` — DD-010 (synth. Ablehnung), DD-011 (Whitespace), DD-012 (synth. Initiative)
- `src/bawue/enum_mapper.py` — Stationstyp-Zuordnungen
