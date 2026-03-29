## Plan: LLM Document Extraction for pazufa-bawue-scraper

## Context

The BaWue scraper creates Dokument objects with empty volltext, hash, zusammenfassung, and schlagworte fields. The
collector framework already provides LLMConnector (via self.config.llm_connector) and
DocumentBuilder base class, and the config already has [llm] section + LLM_PROVIDER_KEY env var — but nothing is wired
into the BaWue scraping pipeline.

The Bavaria scraper (by_dok.py) is the reference implementation: it downloads PDFs, extracts text (kreuzberg/OCR), then
uses LLM to extract structured metadata (title, authors, summary, keywords,
trojanergefahr/meinung).

## Approach

Mirror the Bavaria DocumentBuilder pattern for BaWue, with these adaptations:

- BaWue already has metadata from PARLIS HTML parsing (title, date, authors, drucksache) — use LLM primarily for body
  semantics (summary, keywords, trojanergefahr/meinung) and volltext/hash from PDF text
  extraction
- Graceful degradation: scraper works as today when no LLM key is configured
- Apply to Vorgänge and Beteiligung scrapers (Sitzungen has no PDFs)

**Document Type Mapping**

```
 ┌───────────────────────────────────┬─────────────────────────┬─────────────────────────┐
 │           BaWue Doktyp            │      Builder Class      │     Special Fields      │
 ├───────────────────────────────────┼─────────────────────────┼─────────────────────────┤
 │ ENTWURF, PREPARL_MINUS_ENTWURF    │ BwEntwurf               │ trojanergefahr          │
 ├───────────────────────────────────┼─────────────────────────┼─────────────────────────┤
 │ STELLUNGNAHME                     │ BwStellungnahme         │ meinung                 │
 ├───────────────────────────────────┼─────────────────────────┼─────────────────────────┤
 │ BESCHLUSSEMPF                     │ BwBeschlussempfehlung   │ meinung, trojanergefahr │
 ├───────────────────────────────────┼─────────────────────────┼─────────────────────────┤
 │ REDEPROTOKOLL                     │ BwRedeprotokoll         │ —                       │
 ├───────────────────────────────────┼─────────────────────────┼─────────────────────────┤
 │ ANFRAGE, ANTWORT, SONSTIG, others │ BwMitteilung (fallback) │ —                       │
 └───────────────────────────────────┴─────────────────────────┴─────────────────────────┘
```

## Step-by-Step Implementation

### Step 1: Create src/bawue/bawue_dok.py — Document builder classes

New file (~350 lines), mirrors `vendor/pazufa-collector/collector/scrapers/by_dok.py`:

BawueDokument(DocumentBuilder) — base class:

- Constructor takes typehint, url, session, config + PARLIS metadata (`parlis_titel`, `parlis_autoren`, 
  `parlis_drucksnr`, `parlis_zp`)
- extract_metadata(): kreuzberg PDF text extraction + SHA256 hash + PDF dates (identical to
  BayernDokument.extract_metadata())
- _build_fallback_output(): creates Dokument with text+hash but no LLM fields (for graceful degradation)
- to_dict()/from_dict(): Redis cache serialization

Subclasses — each implements extract_semantics():

- BwEntwurf: header extraction (kurztitel, enriched authors) + body (schlagworte, zusammenfassung, trojanergefahr)
- BwStellungnahme: header + body (schlagworte, zusammenfassung, meinung)
- BwBeschlussempfehlung: header + body (schlagworte, zusammenfassung, meinung, trojanergefahr)
- BwRedeprotokoll: header + body (schlagworte, zusammenfassung)
- BwMitteilung: header + body (schlagworte, zusammenfassung) — fallback for all other types

Factory function: builder_for_doktyp(doktyp: Doktyp) -> type[BawueDokument]

LLM prompts: German prompts adapted from Bayern (same structure, BaWue-specific terminology).

### Step 2: Make Vorgänge scraper document building async

Modify src/bawue/bawue_vorgaenge_scraper.py:

1. Add self._llm_enabled flag in __init__ (checks config.llm_provider_key)
2. Store self.session for PDF downloads
3. Change _build_dokumente from @staticmethod to async instance method
4. Make _build_station, _collect_stationen, _build_vorgang all async
5. In item_extractor: await self._build_vorgang(raw)

_build_dokumente new logic:

```
 async def _build_dokumente(self, fund, station_typ_str, station_typ, initiative, zp_start):
     pdf_url = fund.get("pdf_url", "")
     if not pdf_url:
         return []

     doc_typ = map_dokumententyp(...)
     autoren = _parse_autoren(...)
     drucksnr = fund.get("drucksache")

     if self._llm_enabled:
         builder_cls = builder_for_doktyp(doc_typ)
         builder = builder_cls(doc_typ, pdf_url, self.session, self.config)
         builder.with_parlis_metadata(
             titel=station_typ_str or "Dokument",
             autoren=autoren, drucksnr=drucksnr,
             zp_modifiziert=zp_start, zp_referenz=zp_start,
         )
         try:
             result = await builder.build()
             if result.output is not None:
                 return [StationDokumenteInner(result.output)]
         except Exception:
             logger.warning("Document extraction failed for %s, using plain document", pdf_url)

     # Fallback: plain document (current behavior)
     dok = Dokument(titel=station_typ_str or "Dokument", volltext="", hash="", ...)
     return [StationDokumenteInner(dok)]
```

### Step 3: Make Beteiligung scraper document building async

Modify src/bawue/bawue_beteiligung_scraper.py:

1. Add self._llm_enabled flag in `__init__`
2. Make _build_vorgang async
3. In item_extractor: await self._build_vorgang(slug, detail)
4. Replace inline Dokument(...) with builder pattern when LLM enabled

### Step 4: Update existing tests

Modify tests to handle async changes:

- Update fixtures: scrapers need _llm_enabled = False for existing tests
- Add pytest.mark.asyncio where needed for async methods
- Mock the session and config for LLM-disabled path

### Step 5: Add new tests for LLM path

tests/unit/test_bawue_dok.py:

- Test each builder subclass with mocked llm_connector.extract_info
- Test graceful degradation (LLM failure → fallback output)
- Test cache hit/miss paths
- Test builder_for_doktyp factory
- Test PARLIS metadata preservation (PARLIS data takes priority over LLM)

Update tests/unit/test_bawue_scraper.py:

- Test LLM-enabled path with mocked document builders
- Test LLM-disabled path (current behavior preserved)

### Step 6: Verify & lint

- `pytest --cov=bawue` — all tests pass
- `ruff check src/ tests/` — no lint errors
- Manual test with `python -m bawue.dry_run --limit 3` to verify end-to-end

## Key Files

```
 ┌────────────────────────────────────────┬────────────────────────────────────────────┐
 │                  File                  │                   Action                   │
 ├────────────────────────────────────────┼────────────────────────────────────────────┤
 │ src/bawue/bawue_dok.py                 │ NEW — Document builder classes             │
 ├────────────────────────────────────────┼────────────────────────────────────────────┤
 │ src/bawue/bawue_vorgaenge_scraper.py   │ MODIFY — async chain + LLM toggle          │
 ├────────────────────────────────────────┼────────────────────────────────────────────┤
 │ src/bawue/bawue_beteiligung_scraper.py │ MODIFY — async _build_vorgang + LLM toggle │
 ├────────────────────────────────────────┼────────────────────────────────────────────┤
 │ tests/unit/test_bawue_dok.py           │ NEW — Document builder tests               │
 ├────────────────────────────────────────┼────────────────────────────────────────────┤
 │ tests/unit/test_bawue_scraper.py       │ MODIFY — Update for async + LLM paths      │
 ├────────────────────────────────────────┼────────────────────────────────────────────┤
 │ tests/unit/test_beteiligung_scraper.py │ MODIFY — Update for async + LLM paths      │
 └────────────────────────────────────────┴────────────────────────────────────────────┘
```

## Reusable Components

```
 ┌──────────────────────────────────────┬────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────┐
 │              Component               │                        Location                        │                       Purpose                       │
 ├──────────────────────────────────────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
 │ DocumentBuilder                      │ vendor/pazufa-collector/collector/document_builder.py  │ Base ABC with download/extract/build lifecycle      │
 ├──────────────────────────────────────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
 │ LLMConnector.extract_info()          │ vendor/pazufa-collector/collector/llm_connector.py     │ LLM extraction with JSON validation, caching, retry │
 ├──────────────────────────────────────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
 │ CollectorConfiguration.llm_connector │ vendor/pazufa-collector/collector/config.py:310        │ Already initialized by framework                    │
 ├──────────────────────────────────────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
 │ CollectorConfiguration.cache         │ vendor/pazufa-collector/collector/config.py:214        │ Redis cache for LLM response caching                │
 ├──────────────────────────────────────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
 │ kreuzberg.extract_file()             │ Transitive dependency via collector                    │ PDF text extraction                                 │
 ├──────────────────────────────────────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
 │ extract_ocr_text()                   │ vendor/pazufa-collector/collector/tesseract_wrapper.py │ OCR fallback                                        │
 └──────────────────────────────────────┴────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────┘
```

## Graceful Degradation

Three tiers:

1. Full enrichment (LLM key set): volltext + hash + zusammenfassung + schlagworte + meinung/trojanergefahr
2. Text-only (LLM key missing or LLM fails): volltext + hash only (PDF downloaded + text extracted)
3. Metadata-only (PDF download fails): same as current behavior (empty volltext/hash)

Detection: `self._llm_enabled = bool(getattr(config, 'llm_provider_key', None))` in scraper `__init__`.

## Verification

1. Run `pytest` — all existing + new tests pass
2. Run `ruff check src/ tests/`
3. Run `python -m bawue.dry_run --limit 2` with `LLM_PROVIDER_KEY` set — verify enriched documents
4. Run `python -m bawue.dry_run --limit 2` without LLM key — verify graceful degradation
