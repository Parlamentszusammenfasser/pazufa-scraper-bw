# Implementation Status: BaWue Scraper

## Completeness (as of April 2026)

| Category                     | Estimate  | Notes                                                                                                                                                      |
|------------------------------|-----------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Pflichtfunktionalität**    | **~85 %** | Core fields complete; `volltext`/`hash` now also filled at scraper level (LLM). `tops=[]` and `nummer=0` for committees outstanding.                       |
| **Optionale Funktionalität** | **~50 %** | LLM füllt `zusammenfassung`, `schlagworte`, `kurztitel`, `meinung` auf Dokument-Ebene. `trojanergefahr` jetzt auf Station-Ebene gesetzt. Zusätzliche Datenquellen fehlen weiterhin. |

### Known Gaps — Required Fields

- `tops` in `Sitzung` always `[]` (Phase 3: Tagesordnungen-PDFs not yet parsed)
- `nummer` in `Sitzung` always `0` for committee sessions (no regex match in ICS feed)
- `verfassungsaendernd` always `False` (PARLIS does not expose this attribute)

### Known Gaps — Optional Fields

Bei aktivem LLM (`[llm]`): `zusammenfassung`, `schlagworte`, `kurztitel` und `meinung` werden auf **Dokument-Ebene**
gefüllt. `trojanergefahr` wird auf **Station-Ebene** gesetzt (max. Wert über alle Dokumente). Auf **Vorgang-Ebene** bleiben Lücken.

Missing fields: `kurztitel` (Vorgang — nur Beteiligungsportal), `links` (Vorgang), `lobbyregister`,
`schlagworte` (Station), `stellungnahmen`, `vorwort`, `zp_modifiziert` (Station), `gremium_federf`

Missing data sources: Gesetzblatt BaWue (`postparl-gsblt`), Kabinettsbeschlüsse STM

## Field Status Matrix

| Model    | Field                 | Status      | Note                                                                                 |
|----------|-----------------------|-------------|--------------------------------------------------------------------------------------|
| Vorgang  | `api_id`              | ✅ Complete  | `uuid5(NAMESPACE_URL, vorgangs_id)`                                                  |
| Vorgang  | `titel`               | ✅ Complete  | From PARLIS / Beteiligungsportal                                                     |
| Vorgang  | `typ`                 | ✅ Complete  | Enum-mapped                                                                          |
| Vorgang  | `wahlperiode`         | ✅ Complete  | Fixed WP 17                                                                          |
| Vorgang  | `verfassungsaendernd` | ⚠️ Partial  | Always `False` (PARLIS does not provide this)                                        |
| Vorgang  | `initiatoren`         | ✅ Complete  | From Initiative field                                                                |
| Vorgang  | `stationen`           | ✅ Complete  | From Fundstellen parsing                                                             |
| Station  | `typ`                 | ✅ Complete  | Context-aware enum mapping                                                           |
| Station  | `dokumente`           | ✅ Complete  | PDF links from Fundstelle                                                            |
| Station  | `zp_start`            | ✅ Complete  | From Fundstelle date (with fallbacks)                                                |
| Station  | `gremium`             | ✅ Complete  | From committee / Plenarprotokoll                                                     |
| Station  | `trojanergefahr`      | ✅ LLM       | Max across enriched documents (1–10). Requires `[llm]` config.                       |
| Dokument | `titel`               | ✅ Complete  | Station type as fallback                                                             |
| Dokument | `volltext`            | ✅ Complete* | Framework pipeline OR scraper-level extraction via `bawue_dok.py` (when LLM enabled) |
| Dokument | `hash`                | ✅ Complete* | Framework pipeline OR scraper-level SHA256 via `bawue_dok.py` (when LLM enabled)     |
| Dokument | `typ`                 | ✅ Complete  | Enum-mapped                                                                          |
| Dokument | `zp_modifiziert`      | ✅ Complete  | Fundstelle date                                                                      |
| Dokument | `zp_referenz`         | ✅ Complete  | Fundstelle date                                                                      |
| Dokument | `link`                | ✅ Complete  | PDF URL from Fundstelle                                                              |
| Dokument | `autoren`             | ✅ Complete  | From Fundstelle text, fallback to Initiative                                         |
| Dokument | `zusammenfassung`     | ✅ LLM       | LLM-generated summary (150–250 words). Requires `[llm]` config.                      |
| Dokument | `schlagworte`         | ✅ LLM       | LLM-generated keyword list. Requires `[llm]` config.                                 |
| Dokument | `kurztitel`           | ✅ LLM       | LLM-generated short title in plain language. Requires `[llm]` config.                |
| Dokument | `meinung`             | ✅ LLM       | LLM-generated opinion score (1–5). Only for Stellungnahme/Beschlussempfehlung.       |
| Sitzung  | `termin`              | ✅ Complete  | ICS DTSTART (Berlin TZ → UTC)                                                        |
| Sitzung  | `gremium`             | ✅ Complete  | From ICS SUMMARY                                                                     |
| Sitzung  | `nummer`              | ⚠️ Partial  | Regex for Plenum; committees = `0`                                                   |
| Sitzung  | `tops`                | ❌ Missing   | Always `[]` (Phase 3: Tagesordnungen-PDFs)                                           |
| Sitzung  | `public`              | ✅ Complete  | Always `True`                                                                        |

## Feature Status

| Feature                    | Status             | Notes                                                                                                                        |
|----------------------------|--------------------|------------------------------------------------------------------------------------------------------------------------------|
| PARLIS search (Vorgänge)   | Working            | Automatic subdivision for large result sets                                                                                  |
| Vorgang extraction         | Working            | Title, type, initiative, Vorgangs-ID                                                                                         |
| Station extraction         | Working            | From Fundstellen parsing (date, type, committee, document links)                                                             |
| Enum mapping               | Working            | PARLIS terms → PaZuFa enumerations (Vorgangs-/Stations-/Dokumententyp)                                                       |
| Caching                    | Framework (Redis)  | Automatic deduplication via pazufa-collector ScraperCache                                                                    |
| API submission             | Framework          | Automatic via pazufa-collector API client (httpx)                                                                            |
| Error tolerance            | Framework          | Single Vorgang failures do not stop the pipeline                                                                             |
| Scheduling                 | Framework          | Configurable via `cycle-time-s` in config.toml                                                                               |
| PDF full-text extraction   | Framework pipeline | PyPDF + Kreuzberg/EasyOCR + LLM (via pazufa-collector)                                                                       |
| Document authors           | Working            | Extracted from Fundstelle text, fallback to Initiative                                                                       |
| LLM document enrichment    | Working (optional) | PDF text extraction + LLM semantic metadata via `bawue_dok.py`. Enabled via `LLM_PROVIDER_KEY`. 3-tier graceful degradation. |
| JSON-comment parsing       | Working            | Primary PARLIS parsing path via embedded JSON comments. HTML/XPath as fallback (DD-014).                                     |
| Synthetic stations         | Working            | `parl-initiativ` after `preparl-regent` (DD-012), `parl-ablehnung` from "Aktueller Stand" (DD-010)                           |
| Upload throttle            | Working            | Adaptive rate limiting for API uploads with 429 retry (`upload_throttle.py`)                                                 |
| Beteiligungsportal         | Working            | Pre-parliamentary drafts (`preparl-regent` station with Entwurf PDFs)                                                        |
| Sitzungskalender Phase 1+2 | Working            | ICS feed parsing; `nummer` extracted for Plenum via regex; `tops=[]`                                                         |
| PARLIS detail pages        | Not implemented    | Additional metadata from individual Vorgang detail pages                                                                     |
| Kabinettsbeschlüsse (STM)  | Not implemented    | Signal source for new Regierungsentwürfe                                                                                     |
| Gesetzblatt publications   | Not implemented    | Post-parliamentary phase (`postparl-gsblt` station)                                                                          |
| Sitzungskalender Phase 3   | Not implemented    | TOPs from Tagesordnungen-PDFs; requires HTML scraping for blob URLs                                                          |

## Roadmap

| # | Feature                            | Priority          | Description                                                                                                                           |
|---|------------------------------------|-------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| 1 | ~~SitzungsScraper (ICS)~~          | ~~High~~          | ~~Phase 1+2 implemented~~ — `BawueSitzungenScraper` parses ICS feed, `nummer` extracted for Plenum                                    |
| 2 | SitzungsScraper Phase 3 (TOPs)     | Low               | Enrich with Tagesordnungen-PDFs: scrape blob URLs from landtag-bw.de, parse TOPs from PDFs                                            |
| 3 | ~~Beteiligungsportal~~             | ~~Supplementary~~ | ~~`BawueBeteiligungScraper` implemented~~ — `preparl-regent` station with Entwurf PDFs                                                |
| 4 | ~~LLM Document Enrichment~~        | ~~Optional~~      | ~~Implemented~~ — `bawue_dok.py` provides PDF text extraction + LLM semantic metadata (summary, keywords, short title, opinion score) |
| 5 | ~~JSON-Comment Parsing~~           | ~~Robustness~~    | ~~Implemented~~ — Primary PARLIS parsing via embedded JSON comments; HTML/XPath retained as fallback (DD-014)                         |
| 6 | Gesetzblatt BaWue                  | Supplementary     | Capture publications (`postparl-gsblt`). Completes the legislative lifecycle after the parliamentary phase                            |
| 7 | ~~`trojanergefahr` auf Station-Ebene~~ | ~~Medium~~    | ~~Implemented~~ — LLM-extrahierter Wert wird via `EnrichmentResult` an Station übergeben (max. über alle Dokumente)                  |
