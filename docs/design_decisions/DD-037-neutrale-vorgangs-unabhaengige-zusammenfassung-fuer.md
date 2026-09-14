[← Index](../design_decisions.md)

# DD-037: Neutrale, Vorgangs-unabhängige Zusammenfassung für Redeprotokolle (Issue #49)

**Datum:** 12.07.2026

**Kontext:** Issue #49 meldete, dass Plp 17/0107 (Zweite Beratung) fünf Vorgänge
verlinkt — Kindertagesbetreuungsgesetz, Privatschulgesetz, 5. HRÄG,
Gemeindeordnung, Haushaltsbegleitgesetz 2025/2026 —, von denen vier eine falsche
`zusammenfassung` anzeigten: alle zeigten die auf das Kindertagesbetreuungsgesetz
zugeschnittene Zusammenfassung. Eine Nachstellung gegen die lokale WP17-Devdaten
(Postgres) bestätigte den Fund unverändert: Dokument-Zeile 517 (exakt dieselben
fünf Vorgänge, verlinkt über `17_0107_06112024.pdf`) trug nur eine
Kita-spezifische `zusammenfassung`.

Ursache ist ein struktureller Konflikt
mit [DD-029](DD-029-keine-token-kuerzung-dokumentkontext-im-llm-prompt.md)/[DD-033](DD-033-initiativdrucksache-als-anker-fuer-abschnitts-extraktion-im.md):
Diese lösten Issue #32/#35 (falsches Thema in der Zusammenfassung), indem sie
`zusammenfassung` bei Redeprotokollen bewusst **pro Vorgang** unterschiedlich
berechnen — Abschnitts-Extraktion auf die Drucksache des jeweiligen Vorgangs
zugeschnitten, Cache-Schlüssel inkl. Vorgangstitel/-Drucksache. Das ist für einen
einzelnen Vorgang isoliert betrachtet korrekt. Das Backend speichert eine
`dokument`-Zeile jedoch **pro PDF-Hash**, geteilt von allen Vorgängen, die in
derselben Plenarsitzung debattiert wurden (`rel_station_dokument`, analog zum
Stationen-Hash-Merge aus DD-028/034). Jeder PUT eines weiteren Vorgangs
überschreibt also die zuvor gespeicherte, korrekt-aber-Vorgang-spezifische
Zusammenfassung — der zuletzt gescrapte Vorgang "gewinnt", alle anderen zeigen
sein Thema. Der Mechanismus, der #32/#35 löste, verursacht damit #49.

**Entscheidung:** Für `Doktyp.REDEPROTOKOLL` wird die Vorgangsidentität
(`vorgang_titel`, `vorgang_vnr`, `dok.drucksnr`) bewusst **nicht** an den
Summarization-Schritt weitergereicht — anders als bei jedem anderen Doktyp:

- **Kein Kontext-Header.** `_context_prefix()` erhält `titel=None`, sodass keine
  Vorgangs-Verankerung ("KONTEXT: Dieses Dokument gehört zu …") in den Prompt
  gelangt.
- **Keine Abschnitts-Extraktion.** `narrow_to_relevant_section()` bricht ohne
  Titel sofort ab (bestehender None-sicherer Rückfall, kein Code-Änderung
  nötig) — es wird kein zusätzlicher corelib-Aufruf mehr gemacht, und das volle
  Fenster (weiterhin das ±30-Seiten-Fenster aus DD-029) erreicht unverändert die
  Zusammenfassung.
- **Eigener, neutraler Prompt.** `BODY_PROMPT_REDEPROTOKOLL` ersetzt den
  bisherigen generischen Fallback-Prompt und weist das Modell explizit an, den
  Sitzungsabschnitt neutral zusammenzufassen und alle behandelten
  Tagesordnungspunkte gleichrangig zu nennen, statt sich auf einen Vorgang zu
  fokussieren.
- **Cache-Schlüssel kollabiert auf `(Datei-Hash, doktyp-Prompt)`.** Da
  `_prompt_fingerprint()` ohne Titel/Drucksache/Vorgangs-Drucksache aufgerufen
  wird, erhalten alle Vorgänge, die dasselbe Protokoll-PDF teilen, denselben
  Cache-Eintrag. Der zuerst angereicherte Vorgang berechnet die Zusammenfassung
  und speichert sie unter diesem Schlüssel; jeder weitere Vorgang mit
  demselben Hash bekommt einen Cache-Treffer und **übernimmt exakt dieselbe**
  Zusammenfassung/Schlagworte/Kurztitel, statt eine eigene (und damit
  überschreibende) Version zu berechnen. Das löst das Problem an der Wurzel,
  unabhängig von der Scrape-Reihenfolge.

Bewusst nicht angetastet: Das ±30-Seiten-Fenster (DD-029) bleibt unverändert —
es bestimmt weiterhin sowohl `volltext` als auch den Summarization-Input. Das
gespeicherte `volltext` selbst bleibt weiterhin abhängig davon, welcher Vorgang
zuerst angereichert wird (sein `#page=N`-Fenster "gewinnt" ebenfalls); das ist
eine kleinere, separate Inkonsistenz, die Issue #49 nicht adressiert (die
Akzeptanzkriterien betreffen ausschließlich `zusammenfassung`).
[DD-049](DD-049-ein-dokument-row-je-fundstelle-der-page-n-anker-gilt-auch.md) hat diese Inkonsistenz aufgelöst: seit dort jede Fundstelle ihren
eigenen Row bekommt, trägt jeder Row sein eigenes Fenster als `volltext`. Der
Cache-Schlüssel bleibt der Datei-Hash, die Zusammenfassung damit sitzungsbezogen —
die Entscheidung dieses DDs gilt unverändert.

**Implementierung:** `bawue_dok.py` — `BODY_PROMPT_REDEPROTOKOLL`,
`_DOKTYP_PROMPT_MAP`, `enrich_dokument()` (Verzweigung `context_titel` /
`context_vorgang_vnr` / `context_drucksnr` für `Doktyp.REDEPROTOKOLL`).

**Tests:**

- Unit (`tests/unit/test_bawue_dok.py`):
  `TestSharedRedeprotokollNeutralSummary::test_two_bills_sharing_one_protocol_get_identical_summary`
  (Kern-Regression: zwei Vorgänge mit unterschiedlichem Titel/Drucksache und
  unterschiedlichem simuliertem Seitenfenster, aber identischem PDF-Hash, erhalten
  identische Zusammenfassung/Schlagworte/Kurztitel),
  `TestNarrowToRelevantSection::test_enrich_no_longer_narrows_redeprotokoll`
  (Abschnitts-Extraktion wird für Redeprotokolle nicht mehr aufgerufen),
  `TestNarrowToRelevantSection::test_enrich_omits_bill_context_for_redeprotokoll`
  (kein KONTEXT-Header, kein Vorgangstitel/-Drucksache im Prompt),
  `TestPromptForDoktyp::test_redeprotokoll_uses_dedicated_neutral_prompt`.
- Integration (`tests/integration/test_issue32_real_documents.py`,
  `TestEnrichedSummaryIsNeutralAcrossBills::test_two_bills_sharing_the_real_protocol_get_identical_summary`):
  reales Plenarprotokoll 17/12 (derselbe reale PDF, der Issue #32 begründete),
  zwei unterschiedliche Vorgangsidentitäten (Fischereigesetz 17/529,
  Open-Data-Gesetz 17/513) liefern über einen echten LLM-Aufruf byte-identische
  Zusammenfassung/Schlagworte/Kurztitel — end-to-end am realen Dokument bestätigt.
