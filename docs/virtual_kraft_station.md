# Proposal: Virtuelle `postparl-kraft`-Station

**Status:** Offen (Roadmap #12, optional) — **nicht implementiert**
**Zugehöriges Issue:** [parlamentszusammenfasser#40](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues/40)
**Verwandte Entscheidungen:** DD-002 (Mitteilungen ≠ `postparl-kraft`), DD-010 (synthetische Ablehnung),
DD-012 (synthetische `parl-initiativ`), DD-016 (BY-Track), DD-018 (Filter post-parlamentarischer Ausschussberichte).

Dieses Dokument beschreibt den Vorschlag, fasst die Diskussion zusammen und hält die offenen
Entscheidungsfragen fest. Es ist **kein Implementierungs-Ticket** — vor einer Umsetzung ist
Rücksprache mit BaWue / Benni und eine Klärung der in §7 gelisteten Fragen erforderlich.

---

## 1. Ausgangslage

- In BaWue-PARLIS fehlt für viele verabschiedete Gesetze die `postparl-kraft`-Fundstelle.
  Der Verlauf endet oft bei `postparl-gsblt`; das Inkrafttretensdatum steht ausschließlich im
  Gesetzestext (Gesetzblatt).
- Der Scraper behandelt aktuell sowohl `postparl-gsblt` als auch `postparl-kraft` als
  terminale Marker.
- Vorschlag (Issue #40, Benni): Wenn ein Vorgang akzeptiert und verkündet ist, soll der
  Scraper eine virtuelle `postparl-kraft`-Station **ohne Dokument** einfügen, um den
  Lebenszyklus formal zu schließen — analog zu DD-010 / DD-012.

## 2. Rechtlicher und semantischer Rahmen

- Art. 63 LV BW und GO §49 ff.: Jedes Landesgesetz muss ausgefertigt und verkündet werden;
  das Inkrafttreten ist Pflichtbestandteil des Gesetzestextes (Schlussartikel oder
  Default „am Tag nach der Verkündung").
- Das Inkrafttreten kann zeitversetzt sein (gestuftes Inkrafttreten, Bedingungen,
  Rechtsverordnungen) und liegt häufig Monate oder Jahre nach `postparl-gsblt`.
- Die rechtliche Regel „jedes verabschiedete Gesetz tritt irgendwann in Kraft" ist also
  korrekt, aber das konkrete **Datum** ist aus PARLIS nicht ableitbar.

## 3. Verfügbare Signale im Scraper

| Signal                                   | Belastbarkeit „K existiert" | Belastbarkeit als Datum                 |
|------------------------------------------|-----------------------------|-----------------------------------------|
| `parl-akzeptanz` vorhanden               | Mittel                      | Gering (Akzeptanz ≠ Inkrafttreten)      |
| `postparl-gsblt` vorhanden               | Hoch                        | Mittel (GSBLT+1 Tag als Default)        |
| `Aktueller Stand` = „In Kraft" in PARLIS | Potenziell hoch             | Kein Datum                              |
| Volltext des akzeptierten Entwurfs       | Hoch                        | Hoch, aber Parser/LLM erforderlich      |
| Gesetzblatt-Scraper (Roadmap #6)         | **Höchste**                 | **Höchste** — echte Quelle für K-Datum  |

## 4. Vergleich mit den bestehenden Synthesen

| Aspekt         | DD-010 (Ablehnung)                        | DD-012 (Initiativ nach RegBSL)                      | **Virtual K (Vorschlag)**                           |
|----------------|-------------------------------------------|-----------------------------------------------------|-----------------------------------------------------|
| Primärsignal   | `Aktueller Stand: Abgelehnt` (explizit)   | Strukturelle Lücke in PARLIS (deterministisch)      | `parl-akzeptanz` ± `postparl-gsblt` (heuristisch)   |
| Datum          | Letzte Station (Abstimmungstag)           | Folgestation (i.d.R. 1. Beratung)                   | **Unklar** — Heuristik nötig                        |
| Dokumente      | Keine (fehlt in PARLIS sowieso)           | Kopie der RegBSL-Dokumente (semantisch korrekt)     | **Keine** — kein originäres Dokument verfügbar      |
| Wahrheitsgehalt| Belegt                                    | Formalität, rechtlich ohnehin gegeben               | **Vermutung**, in Edge Cases falsch                 |
| Track-Nutzen   | Erfüllt `…N`-Zweig                        | Erfüllt `…I…`-Zwang                                 | Nicht nötig — Prefix-Matching akzeptiert `IVAVJG`   |

**Präzedenz DD-002:** Das Projekt hat bereits explizit entschieden, „Mitteilung" **nicht**
pauschal auf `postparl-kraft` zu mappen, weil „eine pauschale Zuordnung […] in vielen Fällen
falsch wäre". Ein virtuelles K ohne eindeutiges Signal steht in Spannung zu dieser Linie.

## 5. Risiken und Failure Modes

1. **Falsche Tatsachenbehauptung im UI.** Ein „in Kraft seit"-Badge ist teurer als ein
   ehrliches „Verkündet, Inkrafttreten siehe Gesetzblatt". Gestuftes Inkrafttreten in 5–10 %
   der Fälle würde die Anzeige verfälschen.
2. **Akzeptanz ≠ Rechtsgeltung.** Volksentscheid, Normenkontrollklage oder
   Rechtsverordnungsbedingungen können Inkrafttreten verzögern oder verhindern.
3. **Konflikt mit Roadmap #6.** Sobald der Gesetzblatt-Scraper echte K-Daten liefert,
   entstehen Merge-Konflikte zwischen virtueller Station und realer Quelle. Backend-Merge-
   Verhalten bei abweichenden `zp_start`-Werten ist implementation-defined.
4. **Kein technischer Backend-Druck.** DD-016 bestätigt: `IVAVJG` ist ein gültiger Prefix
   des BY-Tracks — die Synthese ist nicht erforderlich für Track-Validierung.
5. **Schwächt den Anreiz für die richtige Lösung** (Gesetzblatt-Scraper, Roadmap #6).
6. **Retroaktive Re-Synthese.** Vorgänge mit `parl-akzeptanz` aber ohne GSBLT sind
   potenziell noch nicht einmal verkündet — ein virtuelles K wäre hier grob falsch.

## 6. Implementierungs-Optionen (falls umgesetzt)

Bei Umsetzung sollen **alle** folgenden Guards gleichzeitig gelten, um das Risiko falscher
Daten zu minimieren:

1. **Gating:** Vorhandensein sowohl von `parl-akzeptanz` **und** `postparl-gsblt`.
2. **Zeitpuffer:** GSBLT-Datum + *N* Tage (z.B. 14), um gestuftes Inkrafttreten nicht sofort
   zu überschreiben.
3. **Volksentscheid-Ausschluss:** Vorgänge mit `postparl-vesja` oder `postparl-vesne` im
   Zweig werden übersprungen (eigener Pfad).
4. **Datum-Default:** `GSBLT-Datum + 1 Tag` (GO-Regel „am Tag nach der Verkündung") —
   niemals `datetime.now()`, niemals Akzeptanz-Datum.
5. **Explizite Markierung:** Station als synthetisch kennzeichnen, z.B. via Titel
   „Inkrafttreten (synthetisch, angenommen)" oder via separatem Marker-Feld, damit UI und
   zukünftiger Gesetzblatt-Scraper die Station sicher erkennen und ersetzen können.
6. **Feature-Flag:** `[bawue] synthesize-kraft-stations = false` als Default (analog
   DD-017 / `filter-sonstig-stations`). Opt-in only.
7. **Neue Designentscheidung** (DD-020 o.ä.) mit explizitem TODO: „Synthese entfernen,
   sobald Roadmap #6 (Gesetzblatt-Scraper) produktiv Daten liefert."

**Implementation-Anker:** Die Logik würde nach dem Muster von `_ensure_ablehnung_station()`
in `bawue_vorgaenge_scraper.py` als `_ensure_kraft_station()` umgesetzt, aufgerufen aus
`_build_vorgang()` direkt nach dem bestehenden `_ensure_ablehnung_station`-Aufruf.

## 7. Offene Fragen

Vor einer Umsetzung müssen folgende Punkte geklärt sein:

1. **UI-Treiber.** Zeigt das PaZuFa-Frontend heute für Vorgänge mit GSBLT aber ohne K
   einen verwirrenden „unvollständig"-Status? Falls nein, wäre eine UI-Lösung
   („Inkrafttreten: siehe Gesetzblatt") sauberer als Scraper-Synthese.
2. **PARLIS-Metadatum.** Pflegt PARLIS ein „Aktueller Stand: In Kraft" o.ä. analog zu
   „Abgelehnt" (DD-010)? Falls ja, hätte der Vorschlag ein belastbares Signal.
3. **Gesetzblatt-Scraper-Roadmap.** Wer ist Owner von Roadmap #6 und wie weit ist die
   Priorisierung? Virtual K ist nur sinnvoll, wenn #6 noch deutlich entfernt ist.
4. **PaZuFa-weite Konsistenz.** Synthetisieren andere Landtags-Scraper (BY, RLP) K, und
   wenn ja: nach welchen Regeln? Einheitliches Verhalten wäre ein Argument pro.
5. **Rückmeldung von BaWue.** Einverständnis, dass die virtuelle Station als
   synthetisch markiert wird und bei Verfügbarkeit echter Gesetzblatt-Daten überschrieben
   werden soll.

## 8. Empfehlung

**Primär:** Roadmap #6 (Gesetzblatt-Scraper) vorziehen statt zu synthetisieren. Das löst das
Problem an der Wurzel und liefert echte Inkrafttretens-Daten.

**Sekundär:** UI-Fallback im PaZuFa-Frontend für Vorgänge mit GSBLT ohne K
(„Inkrafttreten: siehe Gesetzblatt"). Keine Scraper-Synthese.

**Tertiär** (nur wenn primär und sekundär nicht in absehbarer Zeit möglich sind):
Synthese mit **allen** Guards aus §6, neuem Feature-Flag und einer zugehörigen
DD-Entscheidung inklusive Removal-Klausel.
