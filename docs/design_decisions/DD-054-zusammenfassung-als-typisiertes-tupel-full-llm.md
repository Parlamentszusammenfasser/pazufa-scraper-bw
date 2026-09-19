[← Index](../design_decisions.md)

# DD-054: `Dokument.zusammenfassung` als typisiertes Tupel `full-llm` (GitHub Issue #42, Schritt 1)

**Datum:** 19.09.2026

**Kontext:** Seit Spec 0.2.5 (DD-051) darf `Dokument.zusammenfassung` statt eines Strings
eine Liste von `Zusammenfassungstupel(typ, inhalt)` sein. Ein String wird vom Backend als
Typ `full` gespeichert („ohne Herkunftsangabe"). Unsere Zusammenfassung stammt aber immer
vom LLM — dafür ist der reservierte Typ `full-llm` vorgesehen.

**Entscheidung:**

1. `enrich_dokument` sendet die (sanitisierte, DD-027) LLM-Zusammenfassung als
   `[Zusammenfassungstupel(typ="full-llm", inhalt=…)]`; ohne Zusammenfassung wie bisher `None`.
2. Nur der reservierte Typ `full-llm`, **kein Modellname** im Typ (z. B. `full-llm:gpt-5-nano`):
   jeder Modellwechsel erzeugte sonst einen neuen Typ, und alte Zusammenfassungen blieben stehen.
3. Leser der Zusammenfassung (Kurztitel-Input, DD-053) holen den Text über
   `zusammenfassung_text(dok)`. Der Kurztitel-Prompt und damit der `vorgang-kurztitel:`-Cache-Key
   bleiben unverändert.
4. Prompts und `_prompt_fingerprint` bleiben unverändert — der `llm-semantics:`-Cache bleibt
   gültig. Teil-Zusammenfassungen (Issue #42, Schritt 2) folgen separat.

**Backend-Verhalten / Rollout:** Das Backend merged Zusammenfassungen **je Typ**
(`ON CONFLICT (dok_id, zf_typ) DO UPDATE`, `db/merge/execute.rs`) und löscht keine. Bereits
gespeicherte BW-Dokumente behalten ihren `full`-Eintrag; wird ein Dokument erneut gesendet,
kommt `full-llm` mit demselben Text hinzu und die API liefert beide. Das Backend-Team wird um
ein einmaliges Aufräumen der alten `full`-Einträge von BW-Dokumenten gebeten (Kommentar in
Issue #42). Neu gesendet werden Dokumente erst nach Änderung am Record bzw. Löschen der
`vg2:`-Einträge (DD-052).

**Code:** `bawue_dok.ZUSAMMENFASSUNG_TYP`, `_llm_zusammenfassung`, `zusammenfassung_text`,
`enrich_dokument`; `_initiativ_zusammenfassung`; `bawue_beteiligung_scraper._build_vorgang`

**Tests:** `test_bawue_dok.py::TestIssue42TypedZusammenfassung`,
`test_bawue_scraper.py::TestBuildVorgang` (`…_kurztitel_…`),
`test_beteiligung_scraper.py::…::test_kurztitel_generated_from_titel_and_document_summary`,
`tests/integration/test_llm_extraction.py`
