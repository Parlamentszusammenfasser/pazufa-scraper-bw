# Track Validation: Analyse fuer Baden-Wuerttemberg

**Datum:** 04.04.2026 (Erstanalyse) | 05.04.2026 (Review-Update + Analyse Crystalkey-Response)
**Backend-Version:** v0.2.7 (Track Validation eingefuehrt)
**Issue:** [Issue 26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26) (eingereicht in pazufa-backend, kuenftige Track-Issues in [parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues))
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
| `Y`       | `postparl-vesja` | Volksentscheid Ja (Referendum angenommen). **Nicht** Ausfertigung/Unterschrift MP. |
| `X`       | `postparl-vesne` | Volksentscheid Nein (Referendum abgelehnt)                                         |

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
| 6 | Y ≠ Ausfertigung                     | BESTAETIGT   | `postparl-vesja` = Volksentscheid. **enum_mapper.py:104 Mapping falsch.**  |

### Punkt 1: Prefix-Matching ist inhaerent

**Crystalkey:** "The backend always performs prefix matching; this is inherent, so
a proceeding isn't less valid."

**Bewertung: BESTAETIGT.** Das [Wiki](https://wiki.pazufa.de/books/backend-api/page/track-validation)
dokumentiert: "A sequence of stations is not only valid when it represents a complete
proceeding, but also when only the first few stations of a proceeding are present."
Alle DFA-Zustaende sind akzeptierend — der Input-String muss nur konsumierbar sein.

**Konsequenz:** Unvollstaendige Vorgaenge (`R`, `SI`, `SIV`, `SIVAVJG`) sind
automatisch gueltig. Problem P5 (K fehlt, 135 Vorgaenge) ist damit kein Problem —
`IVAVJG` ist ein gueltiger Praefix von `IVAVJGK`. Unser `GK?`-Vorschlag war unnoetig.

**Validierung gegen Daten:**
- `S` (2x) — gueltiger Praefix von `SI...` ✓
- `SI` (1x) — gueltiger Praefix von `SIV...` ✓
- `SIV` (1x) — gueltiger Praefix von `SIVA...` ✓
- `SIVAVJG` (112x) — gueltiger Praefix von `SIVAVJGK` ✓

### Punkt 2: R → S Umklassifizierung ✅

**Crystalkey:** Auch wenn E und S aktuell nicht gescrapt werden, hat BaWue
Kabinettsbeschluesse fuer Gesetzentwuerfe. Vorschlag: R als S umklassifizieren und
die parlamentarische Fassung als Kabinettsbeschluss behandeln.

**Bewertung: SEMANTISCH STIMMIG, UMGESETZT.**

Begruendung:
- PARLIS "Gesetzentwurf Landesregierung" zeigt den Entwurf **nach Kabinettsbeschluss**,
  also den parlamentarischen Eingang (nicht die Entwurfsphase)
- Legislativer Prozess: Regierung entwirft (R) → Kabinett beschliesst Einbringung (S) → Parlament (I)
- PARLIS dokumentiert den parlamentarischen Eingang = S, nicht die fruehere Entwurfsphase = R
- Beteiligungsportal-Scraper (`bawue_beteiligung_scraper.py:203-209`) deckt die
  fruehere R-Phase bereits ab (vorparlamentarische Entwuerfe aus Buergerbeteiligung)

**Umgesetzt:** `enum_mapper.py` mappt "Gesetzentwurf" + Initiator "Landesregierung"
auf `PREPARL_MINUS_REGBSL` (S). Siehe DD-003 (aktualisiert) und DD-012.

**Validierung gegen Daten:**
- `RIVAVJG` (112x) → `SIVAVJG` — matcht BY-Praefix `((E*R+)?S)?` ✓
- Beteiligungsportal + PARLIS zusammengefuehrt: `RSIVAVJG` — matcht `((E*R+)?S)?` ✓
- `R` (2x) → `S` — gueltiger Praefix ✓

Problem P1 (R ohne S) damit vollstaendig geloest.

### Punkt 3: V vor J — GO §42 verlangt Zwei Lesungen

**Crystalkey:** Fragt, ob die Geschaeftsordnung nicht mindestens zwei Lesungen
verlangt. Verweist auf GO §42 (mindestens zwei Beratungen) und §49 (Schlussabstimmung
am Ende der letzten Beratung). Bittet um konkrete Faelle, in denen PARLIS von der
tatsaechlichen Parlamentspraxis abweicht.

**Bewertung: KORREKT — unsere V?J-These war uebervorsichtig.**

Validierung gegen Daten:
- 126/128 Annahme-Vorgaenge haben zwei explizite V-Stationen vor J
- "Beschluss des Landtags in Zweiter Beratung" (`enum_mapper.py:99`) mappt korrekt
  auf V (Lesung), nicht J (Annahme) — das IST die zweite Lesung (DD-011)
- Das Test-Fixture V-98001 (`tests/fixtures/parlis/gesetzgebung_results.html`)
  zeigt `IVAJ` (eine Lesung), ist aber ein vereinfachtes Fixture, nicht Realdaten
- Im 171-Vorgang-Dev-Lauf wurde `IVAJ` **kein einziges Mal** beobachtet — widerlegt
  unsere urspruengliche Vorhersage, dass IVAJ der haeufigste Pfad sei

**Entscheidung:** Crystalkeys Ansatz uebernommen — Tracks bilden parlamentarisches
Recht ab (was passieren SOLL), nicht was der Scraper liefern KANN. Wenn PARLIS-Daten
von der GO abweichen, ist das ein Scraper-Bug oder PARLIS-Datenfehler, kein Track-Problem.

**Konsequenz:** `V?J` ist unnoetig. `VJ` (wie im BY-Track) ist korrekt.

### Punkt 4: V vor N — Ablehnung nach 1. Lesung

**Crystalkey:** Schlaegt `IV(N|...)` statt unserem `V?N` vor. Wenn Ablehnung nach
der 1. Lesung passiert, soll das strukturell sauber abgebildet werden.

**Bewertung: STRUKTURELL BESSER als unser Vorschlag.**

Unser `V?N` ist zu permissiv — es wuerde N ohne jedes vorherige V an bestimmten
Regex-Positionen erlauben. Crystalkeys Ansatz `IV(N|VA*(...))` bewahrt die Semantik:
es gibt immer mindestens EINE Lesung (V) vor Ablehnung, aber keine zweite Pflicht-
Lesung vor N.

Vergleich:
```
Unser V?N:        ...VA*(Z|V?JGK|V?N|...)     — N kann ohne V in aeusseren Alternativen stehen
Crystalkeys:      I(V(N|A*(Z|VJGK|VN|...)))   — sauberer, aber restrukturiert den ganzen Regex
```

Validierung gegen Daten:
- 11 Ablehnungen, ALLE folgen `IVAVN` (zwei Lesungen vor Ablehnung)
- `IVN` (Ablehnung nach 1. Lesung) wurde im Dev-Lauf nicht beobachtet, ist aber
  theoretisch moeglich (Vorgang wird in 1. Lesung abgelehnt, keine Ueberweisung)
- `IN` (Ablehnung ohne Lesung) kommt nicht vor — Crystalkeys Struktur verhindert das korrekt

**Dies ist die einzige Aenderung am Track-String.** Siehe "Vorgeschlagener BW-Track".

### Punkt 5: K (Inkrafttreten) beibehalten

**Crystalkey:** Der Track sollte nicht abgeschwaecht werden, nur weil PARLIS kein K
liefert — K findet real statt. Der Scraper sollte Inkrafttreten-Daten aus
veroeffentlichten Gesetzen extrahieren und als nachtraegliche K-Station hinzufuegen.

**Bewertung: PHILOSOPHISCH KORREKT, PRAKTISCH IRRELEVANT (dank Prefix-Matching).**

- Prefix-Matching akzeptiert `IVAVJG` als gueltigen Praefix von `IVAVJGK` → kein Blocker
- K-Extraktion aus Gesetzestext ("Dieses Gesetz tritt am Tag nach seiner Verkuendung
  in Kraft") wuerde erfordern: PDF-Parsing des Gesetzblatts + Datumsextraktion aus
  juristischem Text → erhebliche neue Scraper-Faehigkeit, eigenes Feature
- 0/204 Vorgaenge haben K in PARLIS — PARLIS dokumentiert den parlamentarischen
  Prozess, nicht die nachgelagerte Rechtswirkung

**Konsequenz:** `GK` im Track beibehalten (nicht `GK?`). Prefix-Matching loest das
Problem. K-Extraktion ist ein separates Backlog-Item (niedrige Prio).

### Punkt 6: Y = Volksentscheid (BESTAETIGT)

**Crystalkey:** Y = `postparl-vesja` bedeutet "Volksentscheid ja" (Referendum
angenommen), nicht Ausfertigung/Unterschrift MP. Wuerde einen eigenen Track
(`gg-land-volk`) erfordern.

**Bewertung: KORREKT — unsere Interpretation war FALSCH.**

`ves` = **V**olks**e**nt**s**cheid. Bestaetigt durch Backend-Entwickler.

**Fehler in unserem Code:**
- `enum_mapper.py:70`: Kommentar sagt "Ausfertigung (Unterschrift durch den
  Ministerpraesident)" — **FALSCH**, muss korrigiert werden
- `enum_mapper.py:71`: Kommentar sagt "Veto (Ausfertigung verweigert)" — **FALSCH**
- `enum_mapper.py:104`: Mapping `"Ausfertigung": Stationstyp.POSTPARL_MINUS_VESJA`
  — **SEMANTISCH FALSCH**: "Ausfertigung" (Unterschrift MP) ist nicht dasselbe wie
  "Volksentscheid ja". Dieses Mapping muss entfernt oder auf einen anderen Stationstyp
  umgelenkt werden.

**Konsequenz fuer Track:** Y gehoert nicht in den `gg-land-parl`-Track. Unser
Y?-Vorschlag war auf einer falschen Annahme aufgebaut. Fuer Volksantraege braucht
es ggf. einen separaten `gg-land-volk`-Track.

**Konsequenz fuer Scraper:** PARLIS liefert "Ausfertigung" als Fundstelle fuer
0 der 171 Dev-Lauf-Vorgaenge. Falls es doch auftaucht, ist `postparl-vesja` der
falsche Stationstyp. Es gibt aktuell keinen passenden Stationstyp im Backend-Schema
fuer "Ausfertigung" (= Unterschrift MP).

---

## Aktualisierte Problemtabelle

| #  | Problem                   | Schwere (alt) | Status nach Review  | Loesung                                                       |
|----|---------------------------|---------------|---------------------|---------------------------------------------------------------|
| P1 | R ohne S                  | KRITISCH      | **GELOEST**         | R → S Umklassifizierung im Scraper                            |
| P2 | J ohne vorheriges V       | NIEDRIG       | **GELOEST**         | GO §42 schreibt 2 Lesungen vor, kommt praktisch nicht vor     |
| P3 | N ohne vorheriges V       | NIEDRIG       | **TRACK-AENDERUNG** | IVN zulassen (Punkt 4)                                        |
| P4 | Y im Pfad                 | NIEDRIG       | **GELOEST**         | Y = Volksentscheid (bestaetigt). Irrelevant fuer `gg-land-parl`. ✅ Mapping entfernt, Kommentare korrigiert. |
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
2. **R→S Umklassifizierung umgesetzt.** 112 ehemalige R-ohne-S-Fehler geloest.
3. **`sonstig` crasht das Backend** — 27 Vorgaenge (108 Panics). Weiterhin Blocker.
4. **BY-Track passt nach R→S fuer ~98% der Vorgaenge.** Nur IVN-Pfad fehlt.

---

## Scraper-Fixes

| # | Fix                         | Prio   | Betroffene        | Beschreibung                                                                                                   |
|---|-----------------------------|--------|-------------------|----------------------------------------------------------------------------------------------------------------|
| 1 | `sonstig` filtern           | P0     | 28 Vorgaenge      | Stationen mit Typ `sonstig` herausfiltern. "Mitteilung"/"Dokument" nicht mappbar.                              |
| 2 | Leere Stationen             | P0     | 1 Vorgang         | Vorgaenge ohne Stationen ueberspringen (Guard vor Postparl-Filter).                                            |
| 3 | ~~R → S Umklassifizierung~~ | ~~P0~~ | ~~112 Vorgaenge~~ | ✅ Erledigt. `enum_mapper.py`: `PREPARL_MINUS_REGBSL`. Methode umbenannt zu `_ensure_initiativ_after_regbsl()`. |
| 4 | Post-G abschneiden          | P1     | ~5 Vorgaenge      | Stationen nach Gesetzblatt (G) entfernen — PARLIS-Seitenreihenfolge ≠ chronologisch.                           |
| 5 | Doppelte I                  | P2     | 1 Vorgang         | Zweite `parl-initiativ` deduplizieren (PARLIS + DD-012 synth. I).                                              |

Fix 5 (partielle Sequenzen) aus der Erstanalyse **entfaellt** — Prefix-Matching bestaetigt.

### Erwartetes Ergebnis nach Fixes

167/171 Vorgaenge matchen den vorgeschlagenen BW-Track.
4 partielle Sequenzen (`S`, `SI`, `SIV`) sind gueltige Praefixe.

---

## Abhaengigkeiten

| Abhaengigkeit                                          | Status                                                   |
|--------------------------------------------------------|----------------------------------------------------------|
| Backend implementiert BW-Track (nur Punkt-4-Aenderung) | Ausstehend — Issue in parlamentszusammenfasser erstellen |
| Backend: `sonstig` Panic beheben (`validate.rs:310`)   | Ausstehend — separater Bug-Report                        |
| `gg-land-volk` Track fuer Volksantraege                | Zu klaeren (Punkt 3)                                     |
| Klaerung Y/vesja Semantik → enum_mapper Fix            | Bestaetigt: Volksentscheid. Mapping muss korrigiert werden. |
| Klaerung IVAVVJG Dreifach-Lesung                       | Zu klaeren (Punkt 2)                                     |

---

## Zu Klaeren

Offene Punkte fuer die Antwort auf Issue #26 bzw. Folge-Issues:

### 1. "Ausfertigung"-Mapping: entfernen oder umlenken?

`enum_mapper.py:104` mappt "Ausfertigung" → `postparl-vesja` (Y). Da Y = Volksentscheid
(bestaetigt), ist das semantisch falsch. Optionen:

- **Entfernen:** "Ausfertigung" aus `STATIONSTYP_MAP` streichen. Falls PARLIS die
  Fundstelle liefert, wird sie zu `sonstig` und rausgefiltert. Risiko: Information geht
  verloren.
- **Neuer Stationstyp:** Gibt es im Backend-Schema einen passenden Typ fuer
  "Ausfertigung" (Unterschrift MP)? Falls nicht, ist es kein regulaerer Schritt im
  Track und sollte nicht als Station modelliert werden.
- **Frage an Crystalkey:** Gibt es einen Stationstyp fuer "Ausfertigung durch den
  Ministerpraesidenten"? Oder ist das kein modellierter Schritt im PaZuFa-Schema?

### 2. IVAVVJG — Dreifach-Lesung (2 Vorgaenge)

2 Vorgaenge haben drei Lesungen (V, V, V): `IVAVVJG`. Weder der BY-Track noch unser
BW-Vorschlag laesst das zu.

- **Ursache unklar:** Haushaltsgesetzgebung? Dritte Beratung gemaess GO §42?
  PARLIS-Datenfehler?
- **Optionen:**
  - Als "aussergewoehnlich" markieren (Backend v0.2.8 via [Issue #21](https://codeberg.org/PaZuFa/pazufa-backend/issues/21))
  - Track erweitern: `VA*` dreimal wiederholen (komplex, nur 2 Faelle)
  - Scraper-seitig dritte Lesung deduplizieren (semantisch fragwuerdig)
- **Frage an Crystalkey:** Sollen Vorgaenge mit 3 Lesungen als "aussergewoehnlich"
  geflaggt werden, oder ist der Track anzupassen?

### 3. `gg-land-volk` Track fuer Volksantraege

BaWue-Scraper mappt `Volksantrag` → vgtyp `gg-land-volk`. In `tracks.toml` existiert
kein solcher Track fuer BW. Verhalten bei fehlendem Track ist unbekannt.

- **Frage:** Wird ein Vorgang mit unbekanntem vgtyp akzeptiert oder abgelehnt?
- **Frage:** Soll BW einen `gg-land-volk` Track definieren? Falls ja, wie sieht der
  typische Ablauf eines Volksantrags in BaWue aus?

### 4. Issue-Repo: parlamentszusammenfasser statt pazufa-backend

Crystalkey bittet, kuenftige Track-bezogene Issues im
[parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues)
zu erstellen, nicht im pazufa-backend. Issue #26 wurde im falschen Repo eroeffnet.

- **Aktion:** Neues Issue in parlamentszusammenfasser erstellen mit dem aktualisierten
  BW-Track-Vorschlag. Issue #26 referenzieren und als "falsch platziert" kennzeichnen.

---

## Naechste Schritte

1. ~~Dev-Lauf durchfuehren~~ **Erledigt** (04.04.2026)
2. ~~Review durch Backend-Entwickler~~ **Erledigt** (05.04.2026)
3. **Scraper-Fixes implementieren:** Fix 1+2 (P0) sofort, Fix 4+5 danach. ~~Fix 3 (R→S)~~ erledigt.
4. **Issue in parlamentszusammenfasser erstellen:** Minimalen BW-Track vorschlagen (nur Punkt-4-Aenderung), Zu-Klaeren-Punkte 1-3 adressieren
5. **Backend-Bug melden:** Panic bei `validate.rs:310` — `.get()` statt `[]`
6. **`enum_mapper.py` korrigieren:** Kommentare zu Y/X (Zeile 70-71) und Mapping "Ausfertigung" (Zeile 104) beheben
7. **Validierungs-Lauf** nach Scraper-Fixes durchfuehren

---

## Referenzen

- [Issue #26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26) — Unser Track-Vorschlag + Crystalkey-Response (falsches Repo, s. Zu Klaeren Punkt 4)
- [Issue #21](https://codeberg.org/PaZuFa/pazufa-backend/issues/21) — "Aussergewoehnlich"-Flag fuer Vorgaenge (Backend v0.2.8)
- [tracks.toml](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/tracks.toml) — Track-Definitionen
- [Wiki: Track Validation](https://wiki.pazufa.de/books/backend-api/page/track-validation) — Dokumentation (bestaetigt Prefix-Matching)
- [Backend v0.2.7 Release](https://codeberg.org/PaZuFa/pazufa-backend/releases/tag/v0.2.7) — Release mit Track Validation
- [parlamentszusammenfasser Issues](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues) — Richtiges Repo fuer Track-Issues
- `docs/design_decisions.md` — DD-010 (synth. Ablehnung), DD-011 (Whitespace), DD-012 (synth. Initiative)
- `src/bawue/enum_mapper.py` — Stationstyp-Zuordnungen
