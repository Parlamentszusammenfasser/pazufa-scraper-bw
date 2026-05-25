# Change Proposal: Remove `pazufa-collector` Dependency

> Status: **Draft v2 / Reviewed** — incorporates architect-review recommendations (R1–R15).
> Author: change-doc generated 2026-05-25, revised 2026-05-25 after architect review.

## 0. Revision History

- **v1 (2026-05-25)** — Initial analysis.
- **v2 (2026-05-25)** — Architect review folded in. Material changes:
  - Added §5 **Phase 0** (spec diff, backend version gate, type aliases) — blocker-class precondition work.
  - Added §5 Phase 1.2a **HTTP status handling shim** — without this, 422/429/401 fail silently (R1).
  - Fixed `kal_date_put` signature (R2).
  - Added §11 **Rollback & parallel run** and §12 **Observability during cutover** (R3, R11).
  - Phase 1 no longer claimed independent of Phase 2 (R4).
  - LOC and effort estimates bumped (R5, R7): runtime port ~500–700 LOC; total **10–15 engineer-days**.
  - Cache value format migration treated as its own work item (R6).
  - `pazufa-corelib` pinned to git SHA / wheel, not path-install in prod (R8).
  - Added §6 D1 option **6.1c** (inline pipeline, no abstract base) (R9).
  - §10 success criteria tightened to be grep-able and unambiguous (R10).
  - Added direct-dep audit step (R12) and Docker image deprecation timeline (R13).
  - Spec v0.2.2 → v0.2.3 diff promoted to Phase 0 deliverable (R14).
  - Backend version parity promoted to Phase 0 gate (R15).

## 1. Motivation

The BaWue scraper is currently a **plugin of the `pazufa-collector` framework**:
it runs via `python -m collector` and inherits abstract base classes from the
collector for scheduling, caching, and API upload. In practice this dependency
has caused recurring pain:

- **Instability** — the collector framework changes frequently (API shape,
  config keys, generated OpenAPI client, `__main__` behaviour). Several
  outages of the BaWue build have been traced to upstream collector changes,
  not to BaWue code.
- **Coupled OpenAPI stacks** — the collector ships an
  `openapi-generator-cli`-generated client (`openapi_client`, urllib3/Pydantic-v2,
  spec **v0.2.2**) while the newer `pazufa-scraper-core` ships an
  `openapi-python-client`-generated client (`pazufa_corelib.api_client`,
  httpx/attrs, spec **v0.2.3**). The BaWue code is wired to the older one
  exclusively, so it lags every spec bump.
- **Heavy footprint** — the collector pulls in `selenium`, `kreuzberg`,
  `pypdf`, `pillow`, `redis`, and a generated SDK that BaWue uses partially.
- **`scraper-core` already covers most of what we need** — LLM enrichment
  (`pazufa_corelib.llm`) is already used, and an httpx-based API client plus
  helpers exist in `pazufa_corelib.api_client` and `pazufa_corelib.api_helpers`
  but are currently **unused** by BaWue.

The goal is a **stable, self-contained BaWue scraper** that:

1. Depends only on `pazufa-scraper-core` (the shared lib, pinned to a git
   SHA or published wheel — not a path-install in prod, see §6 D5) and
   standard Python libraries.
2. Uses `pazufa_corelib.api_client` (httpx) for all backend traffic.
3. Owns its own entry point, config loader, cache, and scraper-orchestration
   loop (these are too BaWue-shaped to belong upstream; see §6).
4. Has no `vendor/pazufa-collector` and no `oapicode` directory.

## 2. Current Coupling Inventory

### 2.1 `from collector...` imports

| Source file                          | Imported symbol                              | Purpose                                     |
| ------------------------------------ | -------------------------------------------- | ------------------------------------------- |
| `bawue/config_loader.py`             | `CollectorConfiguration`                     | type-hint only (reads `config.config_file`) |
| `bawue/notifications.py`             | `CollectorConfiguration`                     | type-hint only                              |
| `bawue/bawue_vorgaenge_scraper.py`   | `CollectorConfiguration`, `VorgangsScraper`  | base class, ctor arg                        |
| `bawue/bawue_beteiligung_scraper.py` | `CollectorConfiguration`, `VorgangsScraper`  | base class, ctor arg                        |
| `bawue/bawue_sitzungen_scraper.py`   | `CollectorConfiguration`, `SitzungsScraper`  | base class, ctor arg                        |
| `bawue/bawue_dok.py`                 | `collector.scrapercache.ScraperCache`        | Redis cache for LLM semantics               |
| `tests/integration/conftest.py`      | `CollectorConfiguration`, `ScraperCache`     | integration test wiring                     |

### 2.2 `openapi_client` imports (the generated SDK shipped via the collector)

Used in **8 source files** and **6 test files**. Symbols touched:

- **Models** (used as data carriers throughout the scrapers and tests):
  `Vorgang`, `Sitzung`, `Dokument`, `Autor`, `Gremium`, `Parlament`, `Station`,
  `StationDokumenteInner`, `VgIdent`, `Doktyp`, `Stationstyp`, `Vorgangstyp`.
- **API client / config**:
  `openapi_client.ApiClient`, `openapi_client.Configuration`,
  `openapi_client.ApiException`,
  `openapi_client.api.collector_schnittstellen_api.CollectorSchnittstellenApi`
  (methods `vorgang_put`, `kal_date_put`) — used in `upload_throttle.py` and
  `bawue_sitzungen_scraper.py`.

### 2.3 Runtime entry point

```
python -m collector --config-file config.toml
```

→ `vendor/pazufa-collector/collector/__main__.py` calls
`CollectorConfiguration.load()`, then auto-discovers `*_scraper.py` files
from `config.scrapers_dir`, instantiates classes that subclass
`VorgangsScraper` / `SitzungsScraper`, and runs the cyclic scheduling loop.

### 2.4 Abstract base contract

`collector.interface.Scraper` (420 LOC) defines the pipeline
`process_lpurls → process_items → process_results` with abstract hooks
`listing_page_extractor`, `item_extractor`, `send_result`, `get_cached_result`,
`store_extracted_result`, `make_cache_key`, `log_item`. BaWue's three
scrapers implement these hooks today. The `VorgangsScraper` and
`SitzungsScraper` subclasses also provide default `send_result`
implementations that call the openapi SDK directly — these defaults are
overridden by BaWue (`upload_vorgang`, custom `send_result` for sitzungen).

### 2.5 Build / Make / Docker

- `Makefile`:
  - `install` rsyncs both `../pazufa-collector` and `../pazufa-scraper-core`
    into `vendor/`, regenerates `oapicode` via `npx openapi-generator-cli` if
    missing, then runs `poetry install`.
  - `run` invokes `python -m collector`.
  - `package` runs `install`, lint, format, test, then `docker build`.
- `pyproject.toml`:
  - `collector = {path = "../pazufa-collector", develop = true}`
  - `openapi-client = {path = "../pazufa-collector/oapicode", develop = true}`
  - `pazufa-corelib = {path = "../pazufa-scraper-core", develop = true}`
- `Dockerfile` / `docker-compose.yml`: ENTRYPOINT runs the collector module
  and image expects `vendor/pazufa-collector` to be present at build time.
- `README.md` documents the "clone pazufa-collector alongside" setup.

## 3. What `pazufa-scraper-core` Already Provides

| Capability                  | Module                                       | Notes                                                                                              |
| --------------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| LLM enrichment              | `pazufa_corelib.llm`                         | `LLMConnector` + result models + prompts. Already imported by BaWue.                               |
| HTTP API client             | `pazufa_corelib.api_client` (`AuthenticatedClient`, `Client`) | httpx, attrs, spec v0.2.3. **Not yet used by BaWue.**                                |
| Generated API models        | `pazufa_corelib.api_client.models.*`         | `Vorgang`, `Sitzung`, `Dokument`, `Autor`, `Gremium`, `Parlament`, `Station`, `VgIdent`, `Doktyp`, `Stationstyp`, `Vorgangstyp`, `Top`, `Lobbyregeintrag`, etc. |
| Header helpers              | `pazufa_corelib.api_helpers`                 | `format_if_modified_since` (replaces ad-hoc `If-Modified-Since` formatting).                       |
| Normalisation               | `pazufa_corelib.normalization`               | `hash`, `names`, `schlagworte`, `text`, `urls`, fuzzy matching. Currently unused — opportunity.    |
| Schlagworte / names models  | `pazufa_corelib.schlagworte_model`, `names_model` | Pydantic models for shared taxonomy.                                                          |
| API helper data model       | `pazufa_corelib.api_model`                   | Alternative datamodel-codegen Pydantic v2 models (parallel to `api_client.models`).                |

**What it does *not* provide today** (the gap the collector currently fills):

- A `CollectorConfiguration`-equivalent (TOML + env + CLI parser, with bootstrap
  of API client / cache / LLM).
- A `ScraperCache` (Redis wrapper for vorgang / dokument / html / raw).
- An abstract `Scraper` base class with the `run()` pipeline.
- A scheduling loop / `__main__` / plugin auto-discovery.

We have two options for these (§6).

> **Critical compatibility note on the corelib HTTP client.** The endpoint
> wrappers in `pazufa_corelib.api_client.api.*` build a `Response` and only
> `raise` on status codes outside the enumerated set when
> `Client.raise_on_unexpected_status=True`. The default is `False`. This
> means a 422 / 429 / 401 / 5xx response returns `parsed=None` silently —
> the call appears to succeed. The collector's `openapi_client.ApiException`
> contract (with `.status`) is *not* preserved by drop-in replacement. See
> §5 Phase 1.2a for the required shim.

## 4. Target Architecture

```
src/bawue/
  __main__.py            ← NEW: replaces `python -m collector`
  config.py              ← NEW: replaces CollectorConfiguration (TOML + env + CLI)
  cache.py               ← NEW: small Redis wrapper (replaces ScraperCache)
  pipeline.py            ← NEW: orchestrates listing → item → upload (replaces Scraper.run)
  api.py                 ← NEW: AuthenticatedClient + BawueApiError + per-endpoint helpers
  bawue_vorgaenge_scraper.py
  bawue_beteiligung_scraper.py
  bawue_sitzungen_scraper.py
  bawue_dok.py           ← uses bawue.cache, pazufa_corelib.llm
  upload_throttle.py     ← uses bawue.api (BawueApiError, not httpx exceptions directly)
  enum_mapper.py         ← uses pazufa_corelib.api_client.models enums
  ... (everything else stays)
```

Dependencies after migration:

```toml
[tool.poetry.dependencies]
python                = ">=3.13,<3.14"
# Pinned to a git SHA or published wheel — NOT a path install in prod (D5).
pazufa-corelib        = {git = "https://codeberg.org/PaZuFa/pazufa-scraper-core.git", rev = "<SHA>"}
httpx                 = ">=0.28"   # via corelib transitively, but pin explicitly
redis                 = "^7.4"
lxml                  = ">=6.0"
requests              = "^2.32"
toml                  = ">=0.10"
icalendar             = ">=7.0"
certifi               = ">=2026.0"
python-json-logger    = "^3.2"
litellm               = ">=1.83.0"
json-repair           = "^0.59"
python-dotenv         = ">=1.0"    # was transitively provided by collector
kreuzberg             = ">=4.7"    # was transitively provided by collector

[tool.poetry.group.dev.dependencies]
# Dev override allowing local path install for iteration:
# pazufa-corelib = {path = "../pazufa-scraper-core", develop = true}
```

## 5. Migration Plan (Phased)

Each phase is intended to leave `make test` green so the migration can be
shipped in small reviewable steps.

> **Important — Phase 1 is NOT independent of Phase 2.** The original draft
> claimed Phases 1 and 2 could land in either order. They cannot: BaWue's
> scrapers inherit from `collector.interface.VorgangsScraper` whose
> `send_result` is typed against `openapi_client.models.Vorgang`. Swapping
> the model class without also swapping the base class breaks the contract.
> Either land Phase 2 first, or use the type-alias adapter in Phase 0
> (recommended).

### Phase 0 — Preconditions (gates, not coding)

**Must complete before Phase 1 starts.**

0.1. **Spec diff artifact** (R14). Produce `docs/openapi_v022_to_v023_diff.md`
listing every model class with renamed fields, added required fields,
removed fields, and enum value changes between collector's spec v0.2.2 and
scraper-core's spec v0.2.3. Confirmed renames so far: `TouchedByInner` →
`TouchedByItem`. Reviewer signs off before Phase 1.

0.2. **Backend version parity** (R15). Verify the **production** backend's
OpenAPI version is ≥0.2.3. If not, coordinate the backend deploy first —
shipping v0.2.3 calls against a v0.2.2 backend will produce a 422 storm.
This is a precondition, not an open decision.

0.3. **Type alias adapter** (R4). Land a small, mechanical commit:

```python
# src/bawue/types.py — add at top, behind a feature flag
from openapi_client.models import Vorgang  # current
# After Phase 1.1 sweeps imports, flip to:
# from pazufa_corelib.api_client.models.vorgang import Vorgang
```

…and rewrite every `from openapi_client.models import Vorgang` site to
`from bawue.types import Vorgang` (similarly for Sitzung, Dokument, Autor,
Gremium, Parlament, Station, StationDokumenteInner, VgIdent, Doktyp,
Stationstyp, Vorgangstyp). This lets Phase 1.1 flip the alias **once**
instead of touching 14 files; rollback is one revert.

0.4. **`make_vorgang(**kw)` test helper** (D4 in original draft). Same
rationale: hide the constructor flavour behind a helper so the test sweep
becomes a search-and-replace, not a rewrite.

0.5. **Direct-dep audit** (R12). Run `rg 'kreuzberg|tesseract|selenium|pypdf|pillow|dotenv' src/ tests/` and decide for each: drop or add as direct dep. Confirmed today: `kreuzberg` is imported by `bawue_dok.py` (keep); `tesseract` is invoked by kreuzberg's OCR path (keep in Docker); `selenium`/`pypdf`/`pillow` have no BaWue call sites (drop from runtime image).

0.6. **Manifest audit** (part of R13). Grep deploy code now for the string
`collector` and `python -m collector` (Cloud Build, Woodpecker,
docker-compose, README, k8s, cron, `.env`). Produce a one-page list of
call-sites to update in Phase 3 so nothing is missed at cutover.

### Phase 1 — Swap OpenAPI clients (`openapi_client` → `pazufa_corelib.api_client`)

Largest blast radius. After Phase 0 the model swap is a single alias flip.

1.1. **Models**: flip the aliases in `bawue/types.py` to point at
`pazufa_corelib.api_client.models.*`. Differences to absorb:
   - `openapi-generator-cli` (Pydantic v2): `Vorgang(api_id=..., titel=...)`,
     serialisation via `model_dump_json()` / `Vorgang.from_json()`.
   - `openapi-python-client` (attrs): `Vorgang(api_id=..., titel=...)`,
     serialisation via `to_dict()` / `Vorgang.from_dict()`, **optional
     fields default to `UNSET`** (sentinel, not `None`).
   - Construction sites that pass `field=None` for optional fields will
     serialise differently than today: `UNSET` is omitted; `None` is
     emitted as JSON null. Add a unit test that round-trips a
     fully-populated `Vorgang` and asserts the key set matches a fixture
     captured from the current openapi_client output.

1.2. **API client + endpoints**:

```python
# Before:
with openapi_client.ApiClient(self.config.oapiconfig) as api_client:
    api = CollectorSchnittstellenApi(api_client)
    api.vorgang_put(str(self.scraper_id), item)

# After:
from pazufa_corelib.api_client import AuthenticatedClient
from pazufa_corelib.api_client.api.vorgang import vorgang_put
from pazufa_corelib.api_client.api.sitzung import kal_date_put

client = AuthenticatedClient(base_url=config.database_url, token=config.api_key,
                             prefix="", auth_header_name="X-API-Key",
                             raise_on_unexpected_status=True)  # see 1.2a
response = vorgang_put.sync_detailed(
    client=client, body=item, x_scraper_id=str(self.scraper_id)
)
```

   For Sitzungen, the correct signature is **positional** for the URL path
   parts:

```python
response = kal_date_put.sync_detailed(
    parlament,            # pazufa_corelib.api_client.models.parlament.Parlament
    datum,                # datetime.date — NOT str
    client=client,
    body=sitzungen,       # list[Sitzung], plural
    x_scraper_id=str(self.scraper_id),
)
```

   Today, `bawue_sitzungen_scraper.py:140` calls
   `kal_date_put(x_scraper_id=..., parlament=Parlament.BW, datum=item[0], sitzung=item[1])`.
   The migration must: (a) move `parlament` and `datum` to positional, (b) pass `datetime.date`
   (not str), (c) rename `sitzung` → `body`.

1.2a. **HTTP status handling shim** (R1, mandatory).
   The corelib client does **not** raise on 422/429/401/5xx by default; the
   default `raise_on_unexpected_status=False` returns `parsed=None`. The
   current `upload_throttle.with_upload_retry` is built on
   `exception.status == 429` and `e.status == 422/401`. To preserve this
   contract, introduce `bawue/api.py`:

```python
from dataclasses import dataclass
from pazufa_corelib.api_client import AuthenticatedClient
from pazufa_corelib.api_client.api.vorgang import vorgang_put as _vorgang_put
from pazufa_corelib.api_client.api.sitzung import kal_date_put as _kal_date_put

@dataclass
class BawueApiError(Exception):
    status: int
    body: bytes
    method: str  # "vorgang_put" / "kal_date_put"

def put_vorgang(client, scraper_id, item):
    r = _vorgang_put.sync_detailed(client=client, body=item, x_scraper_id=str(scraper_id))
    if r.status_code not in (201,):                    # 403 / 409 also acceptable per spec
        raise BawueApiError(int(r.status_code), r.content, "vorgang_put")
    return r

def put_kalender(client, scraper_id, parlament, datum, sitzungen):
    r = _kal_date_put.sync_detailed(parlament, datum,
                                    client=client, body=sitzungen,
                                    x_scraper_id=str(scraper_id))
    if r.status_code not in (201, 204):
        raise BawueApiError(int(r.status_code), r.content, "kal_date_put")
    return r
```

   `with_upload_retry` then catches `BawueApiError` and inspects
   `.status == 429`. Add unit tests that drive the helper against
   `mock_pazufa_server.py` and assert that 422 / 429 / 401 / 503 each
   raise `BawueApiError` with the right status.

1.3. **Cache value format migration** (R6 — dedicated work item).
   Today the collector caches Vorgang as
   `json.dumps(sanitize_for_serialization(item))`. After the swap, the
   attrs `to_dict()` output **is not byte-identical** to the Pydantic
   `sanitize_for_serialization` output:
   - `UNSET` fields are absent in attrs output; the Pydantic path emitted
     `null` for `None`-valued optionals.
   - Datetime / UUID stringification routes differ.
   - `Vorgang.from_dict()` will raise on keys it doesn't recognise.

   Two acceptable strategies:

   - **(a) Versioned cache keys (recommended)**. Change the prefix from
     `vg:` to `vg2:` (and `dok:` → `dok2:`, `html:` unchanged). Old entries
     are ignored; the first cycle after deploy will re-fetch every item.
     Predictable, easy to reason about, no compat shim.
   - **(b) Best-effort read of legacy entries**. On cache miss with old
     prefix, attempt `Vorgang.from_dict()`; on failure, log and re-fetch.
     More moving parts; only worth it if a full re-fetch is too expensive.

   Plan to ship (a). Document the one-time re-fetch in the change-log.

1.4. **Tests**: with the type aliases (0.3) and `make_vorgang` helper (0.4)
   in place, the test sweep is mostly mechanical. Expect to still hand-edit:
   - 5–10 fixture literals where `None` vs `UNSET` matters.
   - The 7 test files that mock `openapi_client.ApiException` — switch to
     mocking `BawueApiError`.

**Risk**: highest. Estimated touch: ~14 source/test files plus mock-server
shape compatibility. **Effort: 4–6 engineer-days.**

### Phase 2 — Replace `CollectorConfiguration`, `ScraperCache`, `Scraper` bases

2.1. **`bawue/config.py`** (~250–300 LOC) — port the parts of
   `CollectorConfiguration` BaWue actually uses:
   - Reads `config.toml` (path via `--config-file`), env vars, CLI args.
   - Fields actually consumed by BaWue (grep showed): `dry_run`, `once`,
     `linearize`, `collector_id`, `cycle_time_s`, `redis_host`, `redis_port`,
     `cache_documents`, `database_url`, `api_key`, `scrapers` (filter list),
     `api_obj_log`, `logfile`, `errorfile`, `parsewarn`, `llm_provider_key`,
     `llm_provider_base_url`, `llm_model`, `config_file` (path back-reference
     used by `load_toml_section`).
   - **Drop** `scrapers_dir` (auto-discovery is replaced by a static
     registry — see 2.4).
   - **Drop** `oapiconfig` (replaced by an `AuthenticatedClient` built in
     `bawue.api`).
   - Expose `cache: BawueCache` and `client: AuthenticatedClient` properties
     for downstream code.
   - Unit-tested independently.

2.2. **`bawue/cache.py`** (~100 LOC) — port `ScraperCache`. BaWue only uses:
   - `get_raw` / `store_raw` / `get_html` / `store_html` /
     `get_vorgang` / `store_vorgang` / clear.
   - Drop `store_dokument` / `get_dokument` (no BaWue call site —
     `bawue_dok.py` uses `store_raw` directly for LLM semantics).
   - Drop `DocumentBuilder` dependency entirely.
   - Use the `vg2:` / `dok2:` prefixes per Phase 1.3.

2.3. **`bawue/pipeline.py`** (~150–250 LOC) — replace the relevant parts of
   `collector.interface.Scraper` (which is 420 LOC, not "~120" as the v1
   draft claimed). Keep the same hooks BaWue's scrapers already implement
   (`listing_page_extractor`, `item_extractor`, `send_result`,
   `get_cached_result`, `store_extracted_result`, `make_cache_key`,
   `log_item`). The orchestration (`process_lpurls`, `process_items`,
   `process_results`, `run`) can be lifted with light edits.

   **Alternative** (see §6 D1 option 6.1c): skip this file entirely and
   inline the pipeline into the three scraper modules.

2.4. **`bawue/__main__.py`** (~80 LOC) — replace `python -m collector`:
   - Parse args, build `BawueConfig`, configure logging.
   - Static scraper registry (no `importlib` auto-discovery):
     ```python
     SCRAPERS = [BawueVorgaengeScraper, BawueBeteiligungScraper, BawueSitzungenScraper]
     ```
   - Filter by `config.scrapers` (case-insensitive prefix match — same
     semantics as today, ~10 LOC).
   - Cycle loop (`--once`, `cycle_time_s`, KeyboardInterrupt handling) lifted
     from `collector/__main__.py`.

   **Effort: 4–6 engineer-days** (the v1 draft's 2–3 was off by 2x because
   the ported LOC was off by 2x).

### Phase 3 — Update infrastructure

3.1. **`pyproject.toml`** — remove `collector`, `openapi-client`; pin
   `pazufa-corelib` to a git SHA (prod) with a dev-group path override;
   add direct `httpx`, `redis`, `python-dotenv`, `kreuzberg`. Bump `name`
   to e.g. `bawue-scraper`.

3.2. **`Makefile`** — drop the `rsync ../pazufa-collector` and the
   `npx openapi-generator-cli` step from `install`. Change `run` to
   `python -m bawue`.

3.3. **`Dockerfile` / `docker-compose.yml`** — drop the
   `vendor/pazufa-collector` copy step. Change ENTRYPOINT/CMD to
   `python -m bawue --config-file ...`. Drop `selenium`/`pypdf`/`pillow`
   per the Phase 0.5 audit. Keep tesseract (kreuzberg OCR).

3.4. **Image-tag deprecation plan** (R13). Publish the first
   post-migration image as `bawue-scraper:2.0.0`. Keep building
   `bawue-scraper:1.x` from the pre-migration branch for **30 days**
   so any forgotten cron / k8s manifest can be caught and migrated. After
   30 days remove the 1.x build job. Update the manifest list produced in
   Phase 0.6 *before* flipping prod to 2.0.0.

3.5. **`README.md`** — remove "clone pazufa-collector alongside";
   document `python -m bawue` as the entrypoint; explain that only
   `pazufa-scraper-core` is a sibling clone (for dev — prod is pinned).

3.6. **`docs/architecture.md`** — update the diagram and §1 to describe the
   new self-contained architecture.

### Phase 4 — Cleanup

4.1. Delete `vendor/pazufa-collector/` and its references in
   `.dockerignore`, `.aiignore`, `.claudeignore`.
4.2. Delete `tests/integration/conftest.py` imports from `collector.*` and
   rewrite against the new `bawue.config` / `bawue.cache`.
4.3. CI (`.woodpecker.yml`, `cloudbuild.yaml`) — remove any references to
   fetching or building the collector.
4.4. Delete `bawue/types.py` aliases if no longer needed (or keep them as a
   single import surface — preference call).

## 6. Open Decisions

These should be settled before starting Phase 1:

- **D1. Where do generic primitives live — local `bawue/`, inlined, or upstream?**

  - **6.1a (default)** keep `config.py`, `cache.py`, `pipeline.py` in
    `src/bawue/`. Locally-owned code is fine. ~500–700 LOC total.
  - **6.1b** upstream them into `pazufa-scraper-core` as a new
    `pazufa_corelib.runtime` subpackage. Slower but reduces total LOC
    across the org if a sibling scraper repo exists.
  - **6.1c** (newly considered) **inline the pipeline into the three
    scraper modules**. BaWue has only 3 concrete scrapers, and the
    `process_lpurls → process_items → process_results` flow is shallow
    (~80 LOC per scraper if inlined). Replace `__main__` with a 30-line
    `run_all.py`. Lowest total LOC and lowest abstraction debt; trades off
    code-reuse between scrapers for clarity. **Recommended unless a 4th
    scraper is on the near-term roadmap.**

- **D2. → Phase 0.2 gate.** Backend OpenAPI version parity is no longer an
  "open decision". It is a precondition.

- **D3. Re-use of `pazufa_corelib.normalization`**: BaWue currently
  hand-rolls organisation canonicalisation (`canonicalize_organisation` in
  `types.py`). The shared lib already has `normalization.names`,
  `normalization.text`. **Opportunity** to delete BaWue code by switching
  over, but out of scope for this migration — track separately.

- **D4. → Phase 0.4.** The `make_vorgang(**kw)` test helper is no longer
  an open decision. It's a Phase 0 deliverable.

- **D5. → Mandatory.** Pin `pazufa-scraper-core` to a git SHA or published
  wheel in production. Path-install is dev-only. This is enforced by §10
  Success Criterion C6.

## 7. Risk Register

| # | Risk                                                                                   | Likelihood | Impact | Mitigation                                                                                          |
| - | -------------------------------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------------------------------------------- |
| 1 | **Silent failure mode**: corelib client returns `parsed=None` on 4xx/5xx by default; current 429/422/401 retry/branch logic stops working. | High | **Critical** | Mandatory Phase 1.2a shim with `raise_on_unexpected_status=True` and `BawueApiError`. Unit tests that exercise each status against `mock_pazufa_server.py`. Counter metric per status code in `upload_throttle`. |
| 2 | OpenAPI spec v0.2.2 → v0.2.3 has field-shape diffs that break uploads.                | Medium     | High   | Phase 0.1 diff artifact gated by reviewer signoff; integration test against local backend in CI.    |
| 3 | attrs `UNSET` vs `None` causes silently-dropped optional fields on upload.            | Medium     | Medium | Round-trip serialisation test in Phase 1.1; capture a current fixture before flipping the alias.   |
| 4 | Cache value format incompatibility — old `vg:` JSON unreadable by new code.           | High       | Low    | Versioned key prefixes (`vg:` → `vg2:`); accept one-time re-fetch on first deploy cycle.            |
| 5 | Test suite churn (28 test files, ~10k LOC) larger than estimated.                     | Medium     | Medium | Phase 0.3 type-alias adapter + Phase 0.4 `make_vorgang` helper turn most test edits into search-and-replace. |
| 6 | Production deployment uses `python -m collector` in CI/cron; entry-point change misses a call site. | Medium | High | Phase 0.6 manifest audit produces the call-site list; Phase 3.4 image-tag deprecation keeps 1.x building for 30 days. |
| 7 | `kreuzberg` / `tesseract` were transitively provided by collector; missing at runtime. | Medium     | Medium | Phase 0.5 direct-dep audit; verify in CI Docker build.                                              |
| 8 | Production backend on spec v0.2.2 — v0.2.3 calls 422 on cutover.                       | Medium     | Critical | Phase 0.2 gate: refuse Phase 1 until backend version is verified ≥0.2.3.                          |
| 9 | path-install of corelib in prod means a broken upstream push breaks BaWue's image build. | Medium  | High   | §6 D5 mandatory: pin to git SHA / wheel. §10 C6 verifies it.                                       |
| 10| LLM enrichment relies on `collector.llm_connector` constructor signature.             | Low        | Low    | Verified: BaWue already imports `from pazufa_corelib.llm import LLMConnector`. No work here.        |

## 8. Out of Scope

- Re-architecting the LLM enrichment pipeline.
- Switching to async httpx end-to-end (current code is mostly sync inside
  `asyncio.to_thread`; keep that pattern).
- Schema migration in the backend (we follow the backend; we don't lead).
- Pulling `pazufa_corelib.normalization` into BaWue's organisation/keyword
  paths — tracked separately (see D3).

## 9. Estimated Effort

Revised after architect review.

| Phase | Description                                          | Effort (engineer-days) |
| ----- | ---------------------------------------------------- | ---------------------- |
| 0     | Spec diff, backend gate, type aliases, helper, audits | 1–2                    |
| 1     | OpenAPI client swap + status shim + cache format     | 4–6                    |
| 2     | Local config + cache + pipeline + `__main__`         | 4–6                    |
| 3     | Infra (Makefile, Dockerfile, README, deploy, image deprecation) | 1–2          |
| 4     | Cleanup, vendor removal, integration tests           | 2                      |
| **Total** |                                                  | **12–18 days**         |

Plus calendar time for the 30-day image deprecation window and the staging
parallel-run period (§11).

## 10. Success Criteria

Tightened to be grep-able and unambiguous.

1. **C1** — `rg -w 'collector|openapi_client|oapicode|oapiconfig|ApiException' src/ tests/ deploy/ Makefile Dockerfile* docker-compose*.yml` returns nothing.
2. **C2** — `vendor/pazufa-collector` does not exist; `Makefile install` does not reference it.
3. **C3** — Request-body parity: a diff harness at
   `tests/integration/test_cutover_parity.py` runs both code paths against
   the mock server and asserts JSON request bodies are byte-equal modulo
   key ordering, for at least one Vorgang and one Kalender entry per
   scraper.
4. **C4** — `make lint format test test-integration` green; `mypy --strict src/bawue/` green.
5. **C5** — `docker build -t bawue-scraper:2.0.0 .` succeeds without
   cloning `pazufa-collector` and without an `oapicode` directory.
6. **C6** — `pyproject.toml`'s `pazufa-corelib` entry uses `git` + `rev`
   (or a published wheel version), not `path = ...`, in the
   `[tool.poetry.dependencies]` block. Path install is permitted only in
   the dev group.
7. **C7** — CI (Woodpecker / Cloud Build) green; no manifest in §0.6's audit
   list still references `python -m collector` or `bawue-scraper:1.x`.
8. **C8** — Staging has run the 2.0.0 image for **≥7 consecutive days**
   with upload-count delta vs 7-day baseline within ±10% (§12).

## 11. Rollback & Parallel Run (NEW — R3)

A 7–10 day big-bang against production with no fallback was the v1 draft's
biggest blind spot. The migration must ship with all three of:

1. **Tag preservation.** The pre-migration commit is tagged
   `pre-corelib-cutover`. The Docker image at that tag is rebuilt for any
   urgent revert during the 30-day deprecation window (Phase 3.4).
2. **Parallel run in staging.** Phase 1 lands behind a `--client=corelib|openapi`
   flag in `upload_throttle.upload_vorgang` and in
   `bawue_sitzungen_scraper.send_result`, defaulting to `openapi`. The
   flag is flipped to `corelib` in staging only. Both code paths log
   identical request bodies (per C3 parity harness) and the new path's
   HTTP outcomes are compared to the old's daily for 7 days.
3. **Cutover gate.** Production default flips to `corelib` only after the
   staging delta is within ±10% (C8) for 7 consecutive days. The flag
   itself is removed in Phase 4.

If a regression appears post-cutover:

- **Hot revert** (≤30 days): redeploy `bawue-scraper:1.x`.
- **Late revert** (>30 days, after old image build is gone): re-flip the
  config flag if still present; otherwise revert to `pre-corelib-cutover`
  tag and rebuild.

## 12. Observability During Cutover (NEW — R11)

The silent-failure mode (Risk #1) means we cannot rely on the absence of
errors to declare success. Add **before** Phase 1 lands in staging:

1. **HTTP status counter** in `bawue/api.py` — emit a per-status counter
   (`bawue_api_status_total{method, status}`) on every call. Watch:
   `status=201` should dominate; any non-zero rate of `200` (would mean
   "endpoint changed and returned 200 instead of 201"), `400`, `422`,
   `429`, `5xx` over baseline is a regression.
2. **Upload-count delta watch**. The summary already logs published /
   failed / skipped counts (`run_report.py`, mattermost summary). Add a
   daily aggregate that compares yesterday's `published` to the
   trailing-7-day mean. Page on >10% deviation.
3. **Round-trip smoke test**. New
   `tests/integration/test_cutover_smoke.py` runs against staging on
   every deploy: PUT a synthetic `Vorgang`, GET it back via
   `vorgang_get_by_id`, assert payload equality. This catches "201
   returned, nothing inserted" silent failures.
4. **Mattermost cutover alert**. The first cycle after deploying 2.0.0
   posts a one-time summary tagged `[cutover]` with the per-status
   histogram and the delta vs baseline, so a human is forced to eyeball
   it.
