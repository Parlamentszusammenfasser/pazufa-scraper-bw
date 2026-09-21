[← Index](../design_decisions.md)

# DD-056: Teil-Zusammenfassungen `intention-llm` / `regelungsinhalt-llm` / `kosten-llm` wie Brandenburg (GitHub Issue #42, Schritt 2)

**Datum:** 21.09.2026

**Kontext:** Seit DD-054 geht die LLM-Zusammenfassung als `[(full-llm, …)]` raus. Die Spec
lässt weitere, frei benannte `Zusammenfassungstupel` zu; das Backend-Team will herausfinden,
welche Teil-Zusammenfassungen sich bewähren. `pazufa-scraper-bb` hat das bereits umgesetzt
(dort #53/#67) — ein gemeinsames Vokabular in corelib gibt es noch nicht (BB #60, Teil 1).

**Entscheidung:** Wir übernehmen BBs Satz unverändert, damit die Länder vergleichbar bleiben:

1. Drei Abschnitte, je 1–3 Sätze: `intention-llm` (Problem und Ziel), `regelungsinhalt-llm`
   (was konkret geregelt wird), `kosten-llm` (finanzielle Auswirkungen). Kleinschreibung mit
   `-llm`-Suffix wie die reservierten Typen; die Website setzt das EU-Kennzeichen „KI-generiert"
   anhand von `llm` im Typ (`isLlmTyp`). Die im Issue genannten `Intention`/`Kosten`/`Nachteile`
   werden damit verworfen.
2. Nur für die Einzelthemen-Drucksachen `ENTWURF`, `PREPARL_ENTWURF` und `BESCHLUSSEMPF`
   (Prompts `BODY_PROMPT_ENTWURF`/`_BESCHLUSSEMPF`). Protokolle behandeln viele Punkte, feste
   Abschnitte ergeben dort keinen Sinn; Stellungnahme und Generic bleiben wie bei BB ohne.
3. BBs Abschnittsregeln stehen mit im Prompt: sachlich, ohne Wertung; leerer String, wenn
   das Dokument nichts hergibt; die Zusammenfassung ist immer zu füllen; keine Aufzählung
   der Artikelstruktur.
4. Kein zusätzlicher LLM-Call: die Abschnitte kommen im selben JSON wie Zusammenfassung,
   Schlagworte und Kurztitel. Reihenfolge im Payload: `full-llm` zuerst, dann die Abschnitte.
5. Leere oder nicht-textuelle Abschnitte werden weggelassen (das Backend lehnt leere Strings
   ab; `kosten` fehlt oft). Jeder Abschnitt läuft durch `_sanitize_llm_text` (DD-027).
   Fehlt die `full-llm`-Zusammenfassung (leer, nur Artefakte, kein String), entfallen auch
   alle Abschnitte — wie bei BB, wo die Zusammenfassung Pflicht ist; ein Abschnitt allein
   stünde ohne den Text, den er gliedert.
   `zusammenfassung_text()` liest weiterhin nur `full-llm` — der Kurztitel- und der
   Ressort-Input (DD-053/DD-055) bleiben unverändert.

**Kosten / Cache:** Die zwei geänderten Prompts ändern `_prompt_fingerprint` für `ENTWURF`,
`PREPARL_ENTWURF` und `BESCHLUSSEMPF`: deren `llm-semantics:`-Einträge werden einmalig neu
berechnet (wie DD-051); die übrigen Doktypen behalten ihre Einträge. Weil dabei auch der
`full-llm`-Text der Gesetzentwürfe neu geschrieben wird und dieser in die Cache-Keys von
`vorgang-kurztitel:` und `vorgang-ressort:` eingeht (DD-053/DD-055), laufen beim nächsten Neubau
eines Vorgangs mit Entwurf auch diese beiden Calls einmal neu — `Vorgang.kurztitel` und `ressort` können sich dabei
ändern. Gesendet werden die Abschnitte trotzdem erst, wenn ein
Vorgang neu gesendet wird — nach Änderung am Record oder Löschen der `vg2:`-Einträge (DD-052).

**Backend-Verhalten:** Zusammenfassungen werden je Typ gemerged (`ON CONFLICT (dok_id, zf_typ)
DO UPDATE`). Ältere Backends haben neue Typen im Merge-Pfad (also für bereits gespeicherte
Dokumente) stillschweigend verworfen; laut BB ist das seit Backend-`main` 0642f90 behoben
(backend#158) und auf Staging nachgemessen. Beim ersten Lauf ein bekanntes Dokument
zurücklesen und prüfen, dass die Abschnitte angekommen sind.

**Code:** `bawue_dok.ZUSAMMENFASSUNG_TEILE`, `_llm_zusammenfassung`, `BODY_PROMPT_ENTWURF`,
`BODY_PROMPT_BESCHLUSSEMPF`

**Tests:** `test_bawue_dok.py::TestIssue42PartialSummaries`,
`tests/integration/test_llm_extraction.py::TestEntwurfEnrichment::test_entwurf_carries_the_partial_summaries`
