# Phase 0 Audits

> Phase 0.5 (direct-dep), 0.6 (manifest), and 0.2 (backend parity gate)
> deliverables for [migration_remove_pazufa_collector.md](migration_remove_pazufa_collector.md).
> Generated 2026-06-28 from `rg` over `src/ tests/` and the infra files.

## 0.5 — Direct-dependency audit

Command: `rg 'kreuzberg|tesseract|selenium|pypdf|pillow|PIL|dotenv' src/ tests/`

| Dep | BaWue call site? | Currently provided by | Decision for Phase 3 |
| --- | ---------------- | --------------------- | -------------------- |
| `kreuzberg` | **Yes** — `bawue_dok.py:27` (`ExtractionConfig, OcrConfig, PageConfig, extract_file`) | transitively via `collector` | **Add as direct dep** (`pyproject` runtime). |
| `tesseract` | **Yes** (indirect) — `bawue_dok.py:446` `OcrConfig(backend="tesseract", ...)` | system pkg in Docker image | **Keep** the tesseract system package + `deu` language data in the image. |
| `selenium` | No call sites | collector (transitive) | **Drop** from runtime image. |
| `pypdf` | No call sites | collector (transitive) | **Drop** from runtime image. |
| `pillow` / `PIL` | No call sites | collector (transitive) | **Drop** from runtime image. |
| `python-dotenv` / `dotenv` | **No call sites** | collector (transitive) | ⚠️ **Plan deviation:** §4 of the plan lists `python-dotenv` as a direct dep to add ("was transitively provided by collector"). There is **no `import dotenv` in `src/` or `tests/`**. Either drop it, or confirm it is needed only by a deploy/entrypoint shell step (`.env` is read by docker-compose, not by Python). Recommend **not** adding it as a Python dep unless a `.env`-loading call site is introduced with the new `__main__`. |

Additional deps implied but not in current `[tool.poetry.dependencies]`
(currently transitive via `collector`), needed once the collector is removed:

| Dep | Why | Phase |
| --- | --- | ----- |
| `redis` | `bawue_dok.py` uses `collector.scrapercache.ScraperCache` (Redis). The ported `bawue/cache.py` will import `redis` directly. | 2 / 3 |
| `httpx` | The corelib `AuthenticatedClient` is httpx-based. | 1 / 3 |
| `aiohttp` | Used directly by all three scrapers (`import aiohttp`) — **already** effectively direct but not pinned in `pyproject`; pin it explicitly in Phase 3. | 3 |

## 0.6 — Manifest / call-site audit

Every place that references the collector framework, `python -m collector`,
`oapicode`, or `openapi-generator`. These are the cutover edit points for
Phase 3 / Phase 4.

| File | Line(s) | Reference | Phase-3 action |
| ---- | ------- | --------- | -------------- |
| `Makefile` | 13 | `rsync ../pazufa-collector vendor/` | Drop. |
| `Makefile` | 15–18 | generate `oapicode` via `openapi-generator-cli` | Drop. |
| `Makefile` | 48 | `run: python -m collector --config-file config.toml` | → `python -m bawue …`. |
| `Makefile` | 50 | `package` target comment "Vendor collector" | Update text. |
| `Dockerfile` | 32 | `COPY vendor/pazufa-collector/ /pazufa-collector/` | Drop. |
| `Dockerfile` | 61 | `COPY --from=builder /pazufa-collector …` | Drop. |
| `Dockerfile` | 71 | `ENTRYPOINT ["python","-m","collector", …]` | → `python -m bawue …`. |
| `docker-compose.yml` | 15 | entrypoint `python -m collector …` | → `python -m bawue …`. |
| `.woodpecker.yml` | 62–68 | clone collector + download & run `openapi-generator-cli` | Drop the whole `generate-openapi-client` step. |
| `.woodpecker.yml` | 119 | rsync collector into `vendor/` | Drop. |
| `README.md` | 8, 14, 22, 36, 42, 71, 184, 369, 395 | "clone pazufa-collector alongside", `python -m collector` | Rewrite (Phase 3.5). |
| `deploy/README.md` | 69 | `COLLECTOR_ID` env var doc | Keep concept, possibly rename (see note). |

**Not** framework references (leave as-is or handle in config port, **not** a
`python -m collector` cutover):

- `config.sample.toml:3`, `config.dev.toml:19`, `config.staging.toml:2`:
  `collector-uuid = …` — this is a **config key**, the scraper's own UUID, not
  the framework. `bawue/config.py` (Phase 2) must keep reading it (the plan
  calls the field `collector_id`). Decide in Phase 2 whether to rename the key
  to `scraper-uuid` (would need a config-file migration) or keep `collector-uuid`
  for compatibility. Recommend **keep the key name** to avoid touching deploys.
- `Dockerfile.backend-dev:14–23`: this builds a **Rust** backend test image
  (`rust-axum` generator). Unrelated to the BaWue client. Leave untouched.
- `cloudbuild.yaml`: no collector references found.

## 0.2 — Backend version parity gate (VERIFIED 2026-06-28)

**Status: GATE CLEARED.** Staging backend confirmed; one informational residual
(production URL) noted below.

Verified live against the staging backend (`https://staging.api.pazufa.de` — the
target in `.env.example`) on 2026-06-28:

| Probe | Result |
| ----- | ------ |
| `GET /status` (app/release version) | `version: 0.2.12`, commit `2a02910c`, `track_version: 0.0.5`, up since 2026-06-27 |
| `GET /openapi.json` → `info.version` (served **OpenAPI spec** version) | **`0.2.3`** — exactly the corelib v0.1.2 client target |
| OpenAPI document format | backend `openapi: 3.1.0` vs corelib `3.0.0` — expression format only, not API surface |

The two numbers are **different schemes**: `0.2.12` is the backend *application*
release; `0.2.3` is the *OpenAPI spec* it serves. The gate (spec ≥ 0.2.3) holds:
served spec == client spec == `0.2.3`. The original failure mode (backend
*behind* v0.2.3 → 422 storm, Risk #8) is therefore ruled out.

**Request-body schema parity** (backend served 0.2.3 vs corelib v0.1.2 bundled
`openapi.yaml`, normalising PascalCase↔snake_case schema names): **11 of the 12**
models the scraper carries are byte-identical on required fields, properties, and
enum values — `Vorgang, Sitzung, Dokument, Autor, Gremium, Parlament, VgIdent,
Doktyp, Stationstyp, Vorgangstyp, Top` all **OK**.

**One delta — `Station.trojanergefahr`:** the corelib v0.1.2 client carries an
optional `trojanergefahr` integer (1–10) on `Station`; the backend's served
0.2.3 spec omits it. The BaWue scraper actively populates this field (LLM
"hidden-purpose" score; `bawue_vorgaenge_scraper.py:766`,
`bawue_beteiligung_scraper.py:243`). **This is migration-neutral:** the
collector **v0.2.2** client in use *today* already carries and sends
`trojanergefahr` to the *same* backend (`vendor/pazufa-collector/openapi.yml:1667`),
so accept/ignore behaviour is unchanged by the client swap — Phase 1 adds no new
422 risk here. The backend's `Station` does not set `additionalProperties: false`,
so an unknown property is spec-legal to ignore.
> Informational (not a Phase-1 blocker): confirm the backend actually *persists*
> `trojanergefahr`. If it silently drops it, that is a pre-existing data-loss
> issue independent of this migration — file separately.

**corelib pin (v0.1.2)** — verified from the sibling repo
(`../pazufa-scraper-core`, remote `codeberg.org/PaZuFa/pazufa-scraper-core`):
- tag `v0.1.2` exists (commit `aac0953c`, 2026-06-16), `pyproject` version
  `0.1.2`, bundles `openapi.yaml` `info.version: 0.2.3`.
- The dev env path-installs the `0.1.1rc5` tree (HEAD `3e25ecd`). The
  `pazufa_corelib/api_client/` and `pazufa_corelib/llm/` trees — **the only
  corelib surface BaWue imports** (api_client models + `LLMConnector`) — are
  **byte-identical** between that rc5 tree and `v0.1.2`; `git diff HEAD v0.1.2`
  shows changes only under `normalization/`, tests, tooling, and the lockfile.
  BaWue imports nothing from `pazufa_corelib.normalization`, so pinning prod to
  `v0.1.2` (Phase 3.1) is a no-op for the Phase-1 surface and needs no dev-env
  re-checkout to start Phase 1.

| Gate | Status |
| ---- | ------ |
| Backend OpenAPI ≥ v0.2.3 (staging) | ✅ **VERIFIED** — served spec `0.2.3`, app `0.2.12` |
| Backend OpenAPI ≥ v0.2.3 (**production** URL) | ⬜ residual — prod URL not in repo; confirm it tracks staging before prod cutover |
| `pazufa-corelib` v0.1.2 spec == backend spec | ✅ **VERIFIED** — both `0.2.3`; api_client + llm identical to dev tree |
| Reviewer sign-off on `openapi_v022_to_v023_diff.md` | ✅ **APPROVED** 2026-06-28 (maintainer) |
