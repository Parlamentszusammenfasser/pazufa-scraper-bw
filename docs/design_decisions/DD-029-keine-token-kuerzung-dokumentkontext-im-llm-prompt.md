[← Index](../design_decisions.md)

# DD-029: Keine Token-Kürzung + Dokumentkontext im LLM-Prompt

**Datum:** 13.07.2026

**Kontext:** Issue #32 zeigte, dass die Redeprotokoll-Zusammenfassungen des
Fischereigesetz-Vorgangs (V-212391, Drucksache 17/529) ein völlig anderes Thema
beschrieben (ein Open-Data-/Transparenzgesetz). Ursache war ein Zusammenspiel
zweier Effekte im 30-Seiten-Fenster eines Plenarprotokolls (`#page=N`-Hint):

1. **Token-Kürzung (DD-013).** Das Fenster von Plenarprotokoll 17/12 (29.9.2021)
   beginnt bei Tagesordnungspunkt 3 (Open Data, Drucksache 17/513) und erreicht
   die Fischerei-Debatte (TOP 4, Drucksache 17/529) erst ab ~Token 8 700; sie
   läuft bis ~Token 17 000. Die Kürzung bei 12 000 Tokens behielt die
   Open-Data-Debatte **vollständig** und schnitt die Fischerei-Debatte auf rund
   ein Drittel — das LLM fasste folglich das falsche Gesetz zusammen.
2. **Fehlender Dokumentkontext.** Der Prompt nannte weder Titel noch Drucksache
   des Zielvorgangs. Selbst mit vollständigem Text fehlte dem Modell der Anker,
   welche der mehreren debattierten Vorlagen zusammenzufassen ist.

**Entscheidung:**

- **Kürzung ersatzlos entfernt.** `truncate_text()`, die Konstante
  `DEFAULT_TRUNCATE_TOKENS`, der Konfigurationsparameter `truncate-tokens` und die
  `max_tokens`-Parameter von `extract_semantics()`/`enrich_dokument()` sind
  gestrichen. Der vollständige (normalisierte) Volltext geht an das LLM. Die
  Kosten sind für `gpt-5-nano` vernachlässigbar; das 30-Seiten-Fenster (DD zur
  Page-Hint-Extraktion) begrenzt Protokolle weiterhin auf einen relevanten
  Ausschnitt.
- **Dokumentkontext im Prompt.** `extract_semantics()` stellt dem Body-Prompt
  einen Kontext-Header voran, der den Zielvorgang benennt: den Vorgangstitel (das
  Gesetz) und die Drucksachennummer. Das Modell wird angewiesen, bei mehreren
  Themen (Plenarprotokoll mit mehreren Tagesordnungspunkten) ausschließlich die
  Abschnitte zu diesem Vorgang bzw. dieser Drucksache zu berücksichtigen. Der
  Vorgangstitel wird von `_build_vorgang()` über `_collect_stationen` →
  `_build_station` → `_build_dokumente` bis `enrich_dokument()` durchgereicht;
  fehlt er, dient der Dokumenttitel als Rückfall.
- **Dokumentidentität im Cache-Schlüssel.** Der LLM-Semantik-Cache ist mit
  `(doc_hash, prompt_hash)` verschlüsselt. Da dasselbe Protokoll-PDF (gleicher
  Datei-Hash) von mehreren in derselben Sitzung debattierten Gesetzen genutzt
  wird und alle den Doktyp `redeprotokoll` teilen, fließen Titel und Drucksache
  jetzt in `_prompt_fingerprint()` ein. Andernfalls erhielte das zweite Gesetz
  die zwischengespeicherte (themenfremde) Zusammenfassung des ersten.
- **Abschnitts-Extraktion via corelib (Redeprotokolle, OpenAI-Pfad).** Zusätzlich
  zum Kontext-Header wird der Zusammenfassungs-*Input* bei Redeprotokollen
  vorab auf den relevanten Tagesordnungspunkt eingegrenzt. `enrich_dokument()`
  ruft dazu `LLMConnector.extract_relevant_section()` der corelib auf: Die
  Funktion zerlegt den Text in Chunks, lässt das LLM die relevanten
  Zeilenbereiche markieren und extrahiert diese **verbatim** (der zugehörige
  Prompt kennt TOP-Grenzen). Das eliminiert die Fehlerursache an der Wurzel — das
  Modell sieht nur noch die Fischerei-Debatte statt des ganzen 30-Seiten-Fensters
  mit drei Tagesordnungspunkten.

  Bewusste Eingrenzung:
    - **Nur `redeprotokoll`** — andere Doktypen sind einthemig; eine
      Abschnitts-Extraktion wäre verschwendet und könnte Inhalte verlieren.
    - **None-sicherer Rückfall:** Findet die Extraktion nichts oder schlägt sie fehl,
      bleibt das volle Fenster erhalten.
    - **Nur der LLM-Input wird eingegrenzt**, nicht das gespeicherte `volltext`
      (weiterhin das Page-Hint-Fenster). Die Eingrenzung läuft nur bei einem
      Cache-Miss, damit der Semantik-Cache wirksam bleibt.

  Das Page-Hint-Fenster (30 Seiten) bleibt als kostenloser Vorfilter bestehen und
  liefert der Abschnitts-Extraktion einen einzigen Chunk (~18 000 Tokens < 30 000)
  → genau ein zusätzlicher LLM-Aufruf pro Redeprotokoll-Dokument.

**Implementierung:** `bawue_dok.py` — `_context_prefix()`, `_prompt_fingerprint()`,
`extract_semantics()`, `narrow_to_relevant_section()`, `enrich_dokument()`;
Durchreichen des Vorgangstitels in `bawue_vorgaenge_scraper.py`.

**Tests:**

- Unit (`tests/unit/test_bawue_dok.py`): `TestExtractSemanticsNoTruncation`
  (voller Text erreicht das LLM), `TestExtractSemanticsDocumentContext`
  (Drucksache + Titel im Prompt), `TestPromptFingerprintContext`
  (Cache-Kollision zwischen Gesetzen derselben Sitzung ausgeschlossen),
  `TestNarrowToRelevantSection` (Abschnitts-Extraktion nur für Redeprotokoll;
  None-/Fehler-Rückfall auf das volle Fenster; eingegrenzter Text erreicht die
  Zusammenfassung).
- Integration (`tests/integration/test_issue32_real_documents.py`): reales
  Plenarprotokoll 17/12 — deterministischer Nachweis, dass das Fenster das alte
  12 000-Token-Limit übersteigt und die historische Kürzung die Fischerei-Debatte
  zerstört hätte; LLM-gestützter End-to-End-Test, dass die Zusammenfassung das
  Fischereigesetz (nicht Open Data) beschreibt.

**Kosten/Trade-off:** Ohne Kürzung steigen Input-Tokens pro Aufruf. Bei
`gpt-5-nano` (~0,05 $/1M Input) bleibt das im Bereich von Bruchteilen eines Cents
pro Dokument; der Redis-Cache dedupliziert wiederkehrende PDFs. Der Gewinn an
inhaltlicher Korrektheit überwiegt die geringen Mehrkosten deutlich.
