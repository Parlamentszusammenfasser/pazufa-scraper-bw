[← Index](../design_decisions.md)

# DD-055: `Vorgang.ressort` per LLM nach sachlichem Schwerpunkt (GitHub Issue #39)

**Datum:** 21.09.2026

**Kontext:** Spec 0.2.5 (DD-051) kennt `Vorgang.ressort` mit dem Enum `Ressort` (33 Werte).
Baden-Württemberg liefert kein Ressort-Feld; bekannt ist nur ein Ministeriumsname — im
Beteiligungsportal das federführende Haus, in PARLIS die `Initiative` bzw. der Autor einer
Fundstelle.

Zwei Wege wurden geprüft:

1. **Ableitung aus dem Ministeriumsnamen** (erste Fassung dieses DDs, verworfen). BW-Ministerien
   decken mehrere Ressorts ab, das Enum kennt nur einen Wert. Jedes Haus trüge damit über eine
   ganze Wahlperiode denselben Wert, `Klimaschutz` und `Energie` kämen in BW-Daten nie vor, und
   in PARLIS bleiben Regierungsentwürfe ohnehin ohne Ministerium („Landesregierung").
2. **Klassifikation des Inhalts durch das LLM** — das ist der im Projekt bereits etablierte Weg:
   `pazufa-scraper-bb` klassifiziert seit seinem Issue #53 jeden Vorgang per eigenem LLM-Call
   gegen dieselbe Enum-Liste, ausdrücklich nach dem *fachlichen Schwerpunkt der Regelung, nicht
   nach dem einbringenden Akteur*.

Ein gemeinsames Mapping gibt es nirgends: weder im Backend (`Ressort` ist dort nur ein Rust-Enum
ohne Tabelle/Seed), noch in corelib (`normalization/mappings/` kennt `authors`, `organizations`,
`parteien`, `sachgebiete`, `global_tags` — kein Ressort), noch in einem Wiki. Die einzige
gemeinsame Quelle ist die Enum-Liste selbst.

**Entscheidung:**

1. `bawue_dok.vorgang_ressort` klassifiziert je Vorgang per eigenem LLM-Call aus `titel` +
   Initiativ-Zusammenfassung gegen `Ressort`. Kein Wert wird aus Namen abgeleitet.
2. Der Prompt (`RESSORT_PROMPT`) **spiegelt die Regeln von `pazufa-scraper-bb`** — Schwerpunkt
   statt Akteur, Haushalt/Steuern → `Finanzen`, Kommunalrecht → `Kommunales`, `null` wenn nichts
   passt; die vier REGELN und die Enum-Liste sind zeichengleich. Änderungen an den Regeln gehören
   mit BB abgestimmt.

   **Bewusst nicht gespiegelt** (Review zu PR #55): BB klassifiziert aus `volltext[:5000]` des
   Gesetzentwurfs, BW aus `titel` + LLM-Zusammenfassung des Initiativdokuments (ohne
   Zusammenfassung: Titel allein); BB spricht vom „Gesetzentwurf", BW vom „parlamentarischen
   Vorgang"; die Default-Modelle unterscheiden sich (BB `gpt-4o-mini`, BW `gpt-5-nano`). Die
   Zusammenfassung als Eingabe ist billiger und bereits gecacht, kann aber den Schwerpunkt
   verschieben — insbesondere bei Haushaltsbegleitgesetzen, wo die Zusammenfassung die
   finanzierten Bereiche plastischer beschreibt als die Haushaltssystematik. Vor dem ersten
   vollständigen Backfill an einer gemeinsamen Stichprobe mit BB gegenprüfen.
3. Alle 33 Enum-Werte stehen im Prompt; ein Testkanarienvogel sichert das ab (analog BB).
   Die Antwort wird schreibweisentolerant aufgelöst (Groß-/Kleinschreibung, Leerzeichen und
   Trennzeichen der zusammengesetzten Werte wie `Verkehr/Infrastruktur` oder
   `Landes-/Stadtentwicklung`, auch der Enum-Membername). Bleibt sie unauflösbar, wird sie
   verworfen (`None`, nie als Rohstring gesendet) und mit `logger.warning` protokolliert, damit
   ein Auseinanderdriften von Modell und Enum im Lauf sichtbar wird.
   Das Modell begründet zuerst kurz und nennt dann das Ressort (`begruendung` vor `ressort`, wie
   in BB): das verbessert die Zuordnung und macht eine falsche Klassifikation nachvollziehbar.
4. Kein Ressort → Feld bleibt `UNSET` und wird gar nicht gesendet: LLM aus (Default),
   Platzhaltertitel (`TODO`, sonst würde aus dem Nichts ein Ressort erfunden), Call
   fehlgeschlagen, `null` oder unauflösbarer Wert.
   Klassifiziert wird nur aus dem **Initiativdokument** (`_initiativ_zusammenfassung(...,
   any_document=False)`); die Zusammenfassung eines Plenarprotokolls beschreibt die Debatte, nicht
   den Regelungsgegenstand. Der Kurztitel behält seinen Fallback auf ein beliebiges Dokument.
5. **Eigener Redis-Namespace** `vorgang-ressort:<sha256(System-Prompt + Prompt + Titel +
   Zusammenfassung)>` wie beim Kurztitel (DD-053). `llm-semantics:` und `vorgang-kurztitel:`
   bleiben unberührt; eine Prompt- oder Enum-Änderung invalidiert nur den Ressort-Cache (die
   Enum-Liste steht im Prompt). Gespeichert wird `{"ressort": …, "begruendung": …}` — **auch ein
   klassifiziertes `null`**: „nichts passt" ist eine dauerhafte Antwort, kein Fehler, und würde
   sonst bei jeder Neuableitung erneut bezahlt und neu gewürfelt. Nur ein fehlgeschlagener Call
   bleibt ungecacht, damit ein transienter Fehler kein leeres Ergebnis festschreibt. Ein
   unlesbarer Eintrag wird als Rohwert gelesen statt zu werfen.
6. Kosten: ein zusätzlicher LLM-Call je Vorgang (wie der Kurztitel-Call), gecacht.

**Konsequenz:** Das Ressort beschreibt den fachlichen Schwerpunkt, nicht das einreichende Haus.
Ein Windkraft-Gesetz aus dem Umweltministerium kann damit `Energie` tragen — gewollt, und
konsistent mit BB. Zur Spec-Formulierung „usually the name of a ministry": der Schwerpunkt einer
Regelung *ist* in aller Regel der Geschäftsbereich eines Ministeriums; die Spec legt sich nicht
auf das einbringende Haus fest.

**Reichweite:** Nur mit aktivem `[llm]`; ohne LLM bleibt das Feld leer. Bereits gesendete
PARLIS-Vorgänge bekommen ihr Ressort erst, wenn sich ihr Record ändert (Fingerprint, DD-052) oder
die `vg2:`-Einträge gelöscht werden. Im Beteiligungsportal-Scraper gibt es **keine**
Änderungserkennung: dort wird ein gecachter Eintrag allein wegen seiner Existenz übersprungen, ein
erneuter Lauf braucht also zwingend das Löschen der Cache-Keys.

**Offen:** Das Backend schrieb `ressort` zeitweise nur beim Insert, nicht beim Merge
(BB-CHANGELOG, backendseitig behoben 2026-08-26) — beim ersten Live-Lauf prüfen, ob das Feld an
bestehenden Vorgängen tatsächlich ankommt.

**Code:** `bawue_dok.RESSORT_PROMPT`, `bawue_dok.vorgang_ressort`, `_parse_ressort`,
`bawue_vorgaenge_scraper._build_vorgang`, `bawue_beteiligung_scraper._build_vorgang`

**Tests:** `test_issue39_vorgang_ressort.py`, `test_bawue_scraper.py::TestIssue39Ressort`,
`test_beteiligung_scraper.py::TestIssue39Ressort`
