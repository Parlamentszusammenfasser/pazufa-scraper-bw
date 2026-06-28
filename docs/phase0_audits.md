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

## 0.2 — Backend version parity gate (OPEN — must verify before Phase 1)

**Status: cannot verify from this workspace — production access required.**

The plan (§5 Phase 0.2, Risk #8) requires confirming the **production** backend
serves OpenAPI **≥ v0.2.3** before any v0.2.3 client traffic is sent, or a 422
storm results on cutover.

Action owner must, before Phase 1 starts:
1. Hit the production backend's version/health endpoint (or check the deployed
   backend image tag) and record the served OpenAPI version here.
2. If `< 0.2.3`, coordinate the backend deploy first; this is a precondition,
   not a parallel task.

> Related caveat from the spec diff: the **vendored** `pazufa-corelib` is
> `0.1.1rc5` (pre-release), not the `v0.1.2` the plan pins. Confirm the pinned
> tag's bundled spec version matches the backend before Phase 1. See
> [openapi_v022_to_v023_diff.md](openapi_v022_to_v023_diff.md) §sources.

| Gate | Status |
| ---- | ------ |
| Backend OpenAPI ≥ v0.2.3 (prod) | ⬜ **PENDING verification** |
| `pazufa-corelib` pinned tag spec == backend spec | ⬜ **PENDING** (vendored tree is `0.1.1rc5`) |
| Reviewer sign-off on `openapi_v022_to_v023_diff.md` | ⬜ **PENDING** |
