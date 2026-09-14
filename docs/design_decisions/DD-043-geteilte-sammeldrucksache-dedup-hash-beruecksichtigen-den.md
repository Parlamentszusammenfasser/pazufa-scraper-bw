[← Index](../design_decisions.md)

# DD-043: Geteilte Sammeldrucksache — Dedup & `hash_` berücksichtigen den `#page=N`-Anker (Issue #72)

**Datum:** 26.07.2026

**Kontext:** PARLIS führt mehrere eigenständige Dokumente unter *einer* Drucksache,
je Dokument mit eigenem `#page=N`-Anker im PDF. Beispiel V-170962
(Kommunalwahlrecht): Drucksache 17/4495 enthält zwei Änderungsanträge — Nr. 1 (SPD)
bei `#page=1`, der **tatsächlich angenommene** Nr. 2 (GRÜNE/CDU/SPD) bei `#page=5`.
Beide werden als Dokumente an dieselbe `parl-vollvlsgn`-Station angehängt (DD-001).

Zwei Deduplizierungen ließen den angenommenen Antrag verschwinden:

1. **Scraper-seitig:** `_dedup_drucks` schlüsselte allein auf `drucksnr`. Beide
   Anträge tragen 17/4495 → der zweite (`#page=5`) wurde stumm verworfen; nur
   `#page=1` erreichte das Backend (beobachtetes Symptom des Issues).
2. **Backend-seitig (latent):** `hash_` ist der SHA-256 der *ganzen PDF-Datei*
   (`extract_pdf_text`). Beide Anträge teilen dieselbe Datei → identischer Hash.
   Da das Backend `rel_station_dokument` (und den Semantik-Cache, DD-020) über den
   Hash schlüsselt, würde ein geteilter Hash die beiden Dokumente auf einer Station
   erneut zu einem kollabieren (bzw. HTTP500 `rel_station_dokument_pkey` auslösen,
   vgl. DD-034) — der Scraper-Fix allein bliebe wirkungslos.

**Entscheidung:** Der `#page=N`-Anker wird als Teil der Dokumentidentität behandelt:

- `_dedup_drucks` schlüsselt auf `(drucksnr, url-fragment)`. Dokumente mit gleicher
  Drucksache, aber unterschiedlichem Seitenanker bleiben beide erhalten; echte
  Duplikate (gleiche Drucksache **und** gleicher Anker) werden weiterhin entfernt.
- In `enrich_dokument` wird der Seitenanker in `doc_hash` eingefaltet
  (`sha256(f"{doc_hash}#page={page_hint}")`), sobald ein `#page=N` vorliegt — damit
  werden sowohl der persistierte `hash_` als auch der Cache-Schlüssel je Seite
  eindeutig.

**Bewusst ausgenommen — `REDEPROTOKOLL`:**

> ⚠️ **Korrigiert durch [DD-049](DD-049-ein-dokument-row-je-fundstelle-der-page-n-anker-gilt-auch.md) (Issue #25).** Der folgende Absatz war zu
> weit gefasst: er begründet nur `hash_` und den Cache-Schlüssel, übersieht aber, dass
> `titel`, `link` und `autoren` am geteilten Row mitfahren und dort **pro Vorgang**
> unterschiedlich sein müssen. Seit DD-049 trägt `hash_` den Anker auch für
> `REDEPROTOKOLL`; nur der Semantik-Cache-Schlüssel bleibt der reine Datei-Hash.
> Es gibt damit keinen doktyp-abhängigen Zweig mehr im persistierten `hash_`.

Ein Plenarprotokoll-PDF wird
*absichtlich* über alle in der Sitzung behandelten Vorgänge geteilt (DD-036/DD-037),
jeder Vorgang mit eigenem `#page=N`-Fenster. Dort ist **ein** geteilter Hash (ein
Dokument-Row, ein Cache-Eintrag) der Zweck. Für `REDEPROTOKOLL` bleibt `hash_`
daher der reine Datei-Hash. Die Ausnahme ist der einzige doktyp-abhängige Zweig; alle
anderen paginierten Dokumente (Änderungsanträge, ggf. Beschlussempfehlungen) erhalten
den anker-spezifischen Hash.

**Implementierung:** `bawue_vorgaenge_scraper.py` (`_dedup_drucks`, `urlparse`),
`bawue_dok.py` (`enrich_dokument`, nach `extract_pdf_text`).

**Tests:**

- Unit (`tests/unit/test_bawue_scraper.py::TestBuildVorgang`):
  `test_amendments_sharing_drucksache_keep_distinct_page_anchors` (zwei Anträge unter
  17/4495 mit `#page=1`/`#page=5` überleben beide auf der Plenarstation).
- Unit (`tests/unit/test_bawue_dok.py::TestEnrichDokument`):
  `test_page_anchored_siblings_get_distinct_hashes` (gleiche Datei, verschiedene
  Anker → verschiedene `hash_`), `test_redeprotokoll_page_anchor_also_changes_hash`
  (seit DD-049 gilt die Regel auch für REDEPROTOKOLL).
- Integration (`tests/integration/test_scraper_pipeline.py::TestSharedAmendmentDrucksache`):
  voller Pipeline-Lauf (PARLIS-Fixture → Mock-Backend) belegt, dass beide
  `#page`-Varianten das Backend erreichen.
