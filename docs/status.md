# Implementation Status: BaWue Scraper

## Completeness (as of March 2026)

| Category | Estimate | Notes |
|---|---|---|
| **Pflichtfunktionalität** | **~80 %** | Core fields complete; `tops=[]` and `nummer=0` for committees outstanding |
| **Optionale Funktionalität** | **~22 %** | Primary IDs and base data present; metadata and additional sources missing |

### Known Gaps — Required Fields

- `tops` in `Sitzung` always `[]` (Phase 3: Tagesordnungen-PDFs not yet parsed)
- `nummer` in `Sitzung` always `0` for committee sessions (no regex match in ICS feed)
- `verfassungsaendernd` always `False` (PARLIS does not expose this attribute)

### Known Gaps — Optional Fields

Missing fields: `kurztitel` (Vorgang/Dokument), `links` (Vorgang), `lobbyregister`, `schlagworte`
(Station/Dokument), `stellungnahmen`, `trojanergefahr`, `vorwort`, `zp_modifiziert` (Station), `gremium_federf`

Missing data sources: Gesetzblatt BaWue (`postparl-gsblt`), Kabinettsbeschlüsse STM

## Field Status Matrix

| Model | Field | Status | Note |
|---|---|---|---|
| Vorgang | `api_id` | ✅ Complete | `uuid5(NAMESPACE_URL, vorgangs_id)` |
| Vorgang | `titel` | ✅ Complete | From PARLIS / Beteiligungsportal |
| Vorgang | `typ` | ✅ Complete | Enum-mapped |
| Vorgang | `wahlperiode` | ✅ Complete | Fixed WP 17 |
| Vorgang | `verfassungsaendernd` | ⚠️ Partial | Always `False` (PARLIS does not provide this) |
| Vorgang | `initiatoren` | ✅ Complete | From Initiative field |
| Vorgang | `stationen` | ✅ Complete | From Fundstellen parsing |
| Station | `typ` | ✅ Complete | Context-aware enum mapping |
| Station | `dokumente` | ✅ Complete | PDF links from Fundstelle |
| Station | `zp_start` | ✅ Complete | From Fundstelle date (with fallbacks) |
| Station | `gremium` | ✅ Complete | From committee / Plenarprotokoll |
| Dokument | `titel` | ✅ Complete | Station type as fallback |
| Dokument | `volltext` | ⚠️ Framework | Framework pipeline (PyPDF + OCR + LLM) |
| Dokument | `hash` | ⚠️ Framework | Computed by framework |
| Dokument | `typ` | ✅ Complete | Enum-mapped |
| Dokument | `zp_modifiziert` | ✅ Complete | Fundstelle date |
| Dokument | `zp_referenz` | ✅ Complete | Fundstelle date |
| Dokument | `link` | ✅ Complete | PDF URL from Fundstelle |
| Dokument | `autoren` | ✅ Complete | From Fundstelle text, fallback to Initiative |
| Sitzung | `termin` | ✅ Complete | ICS DTSTART (Berlin TZ → UTC) |
| Sitzung | `gremium` | ✅ Complete | From ICS SUMMARY |
| Sitzung | `nummer` | ⚠️ Partial | Regex for Plenum; committees = `0` |
| Sitzung | `tops` | ❌ Missing | Always `[]` (Phase 3: Tagesordnungen-PDFs) |
| Sitzung | `public` | ✅ Complete | Always `True` |

## Feature Status

| Feature | Status | Notes |
|---|---|---|
| PARLIS search (Vorgänge) | Working | Automatic subdivision for large result sets |
| Vorgang extraction | Working | Title, type, initiative, Vorgangs-ID |
| Station extraction | Working | From Fundstellen parsing (date, type, committee, document links) |
| Enum mapping | Working | PARLIS terms → PaZuFa enumerations (Vorgangs-/Stations-/Dokumententyp) |
| Caching | Framework (Redis) | Automatic deduplication via pazufa-collector ScraperCache |
| API submission | Framework | Automatic via pazufa-collector API client (httpx) |
| Error tolerance | Framework | Single Vorgang failures do not stop the pipeline |
| Scheduling | Framework | Configurable via `cycle-time-s` in config.toml |
| PDF full-text extraction | Framework pipeline | PyPDF + Kreuzberg/EasyOCR + LLM (via pazufa-collector) |
| Document authors | Working | Extracted from Fundstelle text, fallback to Initiative |
| Beteiligungsportal | Working | Pre-parliamentary drafts (`preparl-regent` station with Entwurf PDFs) |
| Sitzungskalender Phase 1+2 | Working | ICS feed parsing; `nummer` extracted for Plenum via regex; `tops=[]` |
| PARLIS detail pages | Not implemented | Additional metadata from individual Vorgang detail pages |
| Kabinettsbeschlüsse (STM) | Not implemented | Signal source for new Regierungsentwürfe |
| Gesetzblatt publications | Not implemented | Post-parliamentary phase (`postparl-gsblt` station) |
| Sitzungskalender Phase 3 | Not implemented | TOPs from Tagesordnungen-PDFs; requires HTML scraping for blob URLs |

## Roadmap

| # | Feature | Priority | Description |
|---|---|---|---|
| 1 | ~~SitzungsScraper (ICS)~~ | ~~High~~ | ~~Phase 1+2 implemented~~ — `BawueSitzungenScraper` parses ICS feed, `nummer` extracted for Plenum |
| 2 | SitzungsScraper Phase 3 (TOPs) | Low | Enrich with Tagesordnungen-PDFs: scrape blob URLs from landtag-bw.de, parse TOPs from PDFs |
| 3 | ~~Beteiligungsportal~~ | ~~Supplementary~~ | ~~`BawueBeteiligungScraper` implemented~~ — `preparl-regent` station with Entwurf PDFs |
| 4 | Gesetzblatt BaWue | Supplementary | Capture publications (`postparl-gsblt`). Completes the legislative lifecycle after the parliamentary phase |
