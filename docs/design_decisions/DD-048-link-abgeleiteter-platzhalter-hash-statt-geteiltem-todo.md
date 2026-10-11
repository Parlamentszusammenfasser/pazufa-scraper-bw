[← Index](../design_decisions.md)

# DD-048: Link-abgeleiteter Platzhalter-`hash_` statt geteiltem `TODO`-Marker

**Datum:** 02.08.2026

**Kontext:** Ein Dokument, dessen PDF nicht gelesen werden kann, wurde bisher mit
dem Literal `TODO` als `hash_` hochgeladen — ebenso **jedes** Dokument, wenn die
LLM-Anreicherung deaktiviert ist, was der Standardfall ist (`bawue_dok.py` braucht
`LLM_PROVIDER_KEY`). Betroffene Konstruktionsstellen: `_build_dokumente`
(`bawue_vorgaenge_scraper.py`) und der PDF-Loop in `bawue_beteiligung_scraper.py`.

`enrich_dokument` überschreibt den Platzhalter nur im Erfolgsfall. Es gibt vier
Pfade, auf denen er überlebt: Download-Fehler, leerer Extraktionstext, die äußere
`except`-Klausel — und der Fall „LLM aus", in dem `enrich_dokument` gar nicht erst
aufgerufen wird.

**Zwei Gründe, warum ein geteiltes Literal untragbar ist:**

1. **Kollision über Vorgangsgrenzen.** Das Backend matcht dokumenttragende
   Stationen ohne `api_id` über einen geteilten Dokument-Hash — genau der
   Mechanismus, der in [DD-028](DD-028-stabile-api-id-fuer-dokumentlose-stationen.md)/[DD-034](DD-034-stabile-api-id-fuer-alle-stationen-auch-dokumenttragende.md) zu HTTP 500
   `rel_station_dokument_pkey` führte. Mit `hash_ = "TODO"` sind **alle**
   unlesbaren Dokumente aller Vorgänge für das Backend dasselbe Dokument.
   Backend v0.3.0 verschärft das: Dokumente werden jetzt explizit über
   Hash-Gleichheit + `subdoc_id` gemerged.
2. **Validierung.** Backend v0.3.0 prüft `hash` auf Hex-Ziffern (40–64 Zeichen);
   die Corelib-Härtung (`Sha256Hex`) verlangt exakt 64. `TODO` verletzt beides.

**Entscheidung:** `placeholder_hash(link)` in `bawue/types.py` liefert
`sha256(link)` — 64 Zeichen Hex, deterministisch, pro Dokument verschieden. Der
Link ist die stabile Identität des Dokuments und trägt bereits den
`#page=N`-Anker, der Abschnitte einer geteilten Sammeldrucksache unterscheidet
([DD-043](DD-043-geteilte-sammeldrucksache-dedup-hash-beruecksichtigen-den.md)). Gesetzt wird er **an der Konstruktionsstelle**, nicht in den
Fallback-Zweigen von `enrich_dokument` — damit sind alle vier Pfade inklusive
„LLM aus" mit einer Änderung abgedeckt. Ein echter Inhalts-Digest ersetzt ihn,
sobald die Extraktion gelingt.

**Nachtrag (GitHub Issue #70, 10.10.2026):** Der Link allein genügt nicht. Ein
noch unveröffentlichtes Plenarprotokoll steht in PARLIS ohne `S. x–y`, also ohne
`#page`-Anker, in mehreren Vorgängen (Plenarprotokoll 18/11: V-247603 „Erste
Beratung", V-247045 „Zweite Beratung"). Gleicher Link → gleicher Platzhalter → das
Backend merged beide zu einem Dokument, der letzte Upload überschreibt `titel`.
PARLIS hasht deshalb `sha256(vorgangs_id | Fundstelle-raw | link)`: ein Dokument je
Fundstelle, stabil zwischen Läufen. Der Beteiligungsportal-Scraper behält
`sha256(link)` (seine PDFs gehören zu genau einem Prozess).

Das Backend (v0.3.3) ersetzt dabei keine Rows: ein Stations-Merge fügt Dokumente nur
hinzu (`execute_merge_station`), und ein Protokoll ohne `drucksnr` matcht nur über den
Hash. Ändert sich der Hash, hängt der alte Row weiter an der Station und muss manuell
gelöscht werden (`DELETE /api/v2/dokument/{api_id}`). Das gilt für den Umstieg auf
diesen Platzhalter ebenso wie für die bekannte Lücke: Ist das Protokoll lesbar, aber
noch ohne Anker, gilt der reine Datei-Hash ([DD-049](DD-049-ein-dokument-row-je-fundstelle-der-page-n-anker-gilt-auch.md)), und die Vorgänge teilen
wieder einen Row; trägt PARLIS `S. x–y` nach, kommt je Station ein Anker-Row dazu.

**Bewusst nicht geändert:** `volltext` trägt weiterhin `TODO_MARKER`. Das Feld ist
ein Pflicht-String, den das Backend nur auf „nicht leer" prüft; es ist keine
Identität und löst keine Merges aus.

**Nachweis:** Im letzten realen WP17-Lauf (`locallogs/…017.jsonl`) trugen 2 von
156 Dokumenten `hash_='TODO'` — beide dieselbe Plenarprotokoll-Seite
(`17_0139_28012026.pdf#page=36`), deren Extraktion fehlschlug. Bei deaktivierter
LLM-Anreicherung wäre die Quote 100 %.

**Migrationshinweis:** Bereits hochgeladene `TODO`-Dokumente wurden backendseitig
zu einem Objekt kollabiert. Nach dieser Änderung entstehen daraus eigenständige
Dokumente — erwartete, gewollte Korrektur, aber sichtbar als neue Objekte.

**Code:** `types.placeholder_hash`, `_build_dokumente` (Issue #70: `vorgang_id` über `_build_station`),
`bawue_beteiligung_scraper._build_vorgang`

**Tests:**
- `tests/unit/test_bawue_scraper.py::TestPlaceholderHash` — Hex-Form,
  Determinismus, Unterscheidbarkeit je Link und je `#page=N`-Anker; zwei Vorgänge
  teilen keinen Hash, auch nicht bei gleichem Link ohne Anker
  (`test_issue70_shared_unanchored_protocol_gets_one_hash_per_fundstelle`).
- `tests/unit/test_bawue_scraper.py::TestBuildVorgang::test_dokument_placeholders_when_llm_disabled`
  und `tests/unit/test_beteiligung_scraper.py::TestBuildVorgang::test_dokument_placeholders_when_llm_disabled`
  — `volltext` bleibt `TODO`, `hash_` ist ein gültiger Platzhalter.
