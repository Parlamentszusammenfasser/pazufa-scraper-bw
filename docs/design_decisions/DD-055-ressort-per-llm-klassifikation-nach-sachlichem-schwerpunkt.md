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
2. Der Prompt (`RESSORT_PROMPT`) **spiegelt den Prompt von `pazufa-scraper-bb`** — gleiche Regeln
   (Schwerpunkt statt Akteur, Haushalt/Steuern → `Finanzen`, Kommunalrecht → `Kommunales`,
   `null` wenn nichts passt), damit ein Ressort in BW dasselbe bedeutet wie in BB. Änderungen an
   den Regeln gehören mit BB abgestimmt.
3. Alle 33 Enum-Werte stehen im Prompt; ein Testkanarienvogel sichert das ab (analog BB). Eine
   Antwort außerhalb des Enums wird verworfen (`None`), nicht als Rohstring gesendet.
4. Kein Ressort → Feld bleibt `UNSET` und wird gar nicht gesendet: LLM aus (Default), Call
   fehlgeschlagen, `null` oder unbekannter Wert.
5. **Eigener Redis-Namespace** `vorgang-ressort:<sha256(System-Prompt + Prompt + Titel +
   Zusammenfassung)>` wie beim Kurztitel (DD-053). `llm-semantics:` und `vorgang-kurztitel:`
   bleiben unberührt; eine Prompt-Änderung invalidiert nur den Ressort-Cache.
6. Kosten: ein zusätzlicher LLM-Call je Vorgang (wie der Kurztitel-Call), gecacht.

**Konsequenz:** Das Ressort beschreibt den fachlichen Schwerpunkt, nicht das einreichende Haus.
Ein Windkraft-Gesetz aus dem Umweltministerium kann damit `Energie` tragen — gewollt, und
konsistent mit BB. Zur Spec-Formulierung „usually the name of a ministry": der Schwerpunkt einer
Regelung *ist* in aller Regel der Geschäftsbereich eines Ministeriums; die Spec legt sich nicht
auf das einbringende Haus fest.

**Reichweite:** Nur mit aktivem `[llm]`; ohne LLM bleibt das Feld leer. Bereits gesendete Vorgänge
bekommen ihr Ressort erst, wenn sich ihr PARLIS-Record ändert bzw. die `vg2:`-Einträge gelöscht
werden (DD-052).

**Offen:** Das Backend schrieb `ressort` zeitweise nur beim Insert, nicht beim Merge
(BB-CHANGELOG, backendseitig behoben 2026-08-26) — beim ersten Live-Lauf prüfen, ob das Feld an
bestehenden Vorgängen tatsächlich ankommt.

**Code:** `bawue_dok.RESSORT_PROMPT`, `bawue_dok.vorgang_ressort`, `_parse_ressort`,
`bawue_vorgaenge_scraper._build_vorgang`, `bawue_beteiligung_scraper._build_vorgang`

**Tests:** `test_issue39_vorgang_ressort.py`, `test_bawue_scraper.py::TestIssue39Ressort`,
`test_beteiligung_scraper.py::TestIssue39Ressort`
