# CLAUDE.md — BaWue Scraper

Project context for the Landtag Baden-Württemberg scraper. The generic behavioral
guidelines in the parent `../CLAUDE.md` (Think Before Coding, Simplicity, Surgical
Changes, Goal-Driven) also apply — this file adds project-specific facts so you don't
need them re-posted each time.

## What this is

A **self-contained** scraper for the Baden-Württemberg state parliament
([Landtag BW](https://www.landtag-bw.de/)). It scrapes legislative proceedings and
submits them to the [Parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser)
(PaZuFa) platform via its Write-API v2 (`PUT /api/v2/vorgang`, `PUT /api/v2/kalender`).

- Parliament code `BW`, Wahlperiode **17**. Python **3.13**. Package `bawue` under `src/`.
- Owns its entry point (`bawue.__main__`), config loader, Redis cache, and scraping loop.
  Depends only on [pazufa-scraper-core](https://codeberg.org/PaZuFa/pazufa-scraper-core)
  ("corelib" — httpx API client, generated OpenAPI models, LLM enrichment), pinned to a git tag.
- **Hosted on Codeberg**; issues at https://codeberg.org/PaZuFa/pazufa-scraper-bw/issues.

Three scrapers (all in `src/bawue/`), run from a static registry:

| Scraper | Source | Produces |
|---|---|---|
| `BawueVorgaengeScraper` | PARLIS (`parlis.landtag-bw.de`, HTML/JSON-comment scraping) | `Vorgang` |
| `BawueBeteiligungScraper` | Beteiligungsportal BW | `Vorgang` (`preparl-regent` station) |
| `BawueSitzungenScraper` | ICS calendar feed | `Sitzung` |

## The mental model that matters most

A `Vorgang` (legislative process) is a list of **`Station`s** (lifecycle steps), each with a
`typ` (`Stationstyp`), a `zp_start` timestamp, a `gremium`, and `dokumente`. PARLIS gives
semi-structured "Fundstellen" text that `_build_vorgang` parses into Stationen.

Two backend behaviors drive most bugs — keep both in mind:

1. **Track validation sorts Stationen by `zp_start`, not list order**, and requires a
   canonical sequence (BW reuses the BY track unchanged, DD-016), e.g.
   `parl-initiativ → parl-vollvlsgn → parl-ausschber`. A Station whose `zp_start` puts it
   out of canonical order → **HTTP 400 Track validation failed**. Ordering helpers must
   therefore reason about `zp_start`, not list position (see issue #48).
2. **The backend merges/deduplicates** Stationen by `api_id`, or — if none — by shared
   document hash. Identical PDFs recur across Vorgänge (every Haushalt-Einzelplan cites the
   shared Staatshaushaltsgesetz Drucksache 17/1000), so hash-based matching collides across
   Vorgänge → **HTTP 500 duplicate key `rel_station_dokument_pkey`**. Every Station gets a
   Vorgang-scoped stable `api_id` (`_assign_stable_station_ids`, DD-028/DD-034, issue #47).

Related machinery in `bawue_vorgaenge_scraper.py`: synthetic Stationen (`parl-initiativ`
after `preparl-regbsl` DD-012; `parl-ablehnung` from "Aktueller Stand" DD-010), reading-round
consolidation (DD-024/DD-026), out-of-order ausschber retiming (DD-025), and
`_enforce_total_ordering` (bumps colliding `zp_start`s so different-typed Stationen never tie).

## Design decisions — read these first

`docs/design_decisions.md` records **every deliberate deviation** from PaZuFa conventions as
`DD-001 …` (also mirrored to the project wiki). It **opens with a scannable index table** (DD
number + trigger keywords + code symbols) — read that first to find the relevant DD without
loading the whole file, then jump to `## DD-NNN`. Before changing station mapping, ordering,
enum mapping, or synthetic-station logic: **find the relevant DD**, and when your change alters
or adds a rule, **add/update a DD** (and its index row) in the same change. Behavior in the code is
usually intentional and DD-justified — don't "fix" it without checking.

Other docs: `docs/architecture.md` (full component/data-flow reference),
`docs/status.md` (field-completeness matrix, roadmap, known gaps),
`docs/observed_station_types.md` (enum-coverage inventory).

## Working on an issue

TDD is the norm here, and each fixed issue leaves a **regression test**:

1. Reproduce with a failing test — unit tests in `tests/unit/` (fixtures in `tests/fixtures/`),
   integration in `tests/integration/`. Use existing helpers like `_make_raw_vorgang` /
   `scraper_build_vorgang` (see `tests/unit/test_bawue_scraper.py`).
2. Make a **surgical** fix; update/add the relevant DD if behavior rules change.
3. Keep the regression test named for the issue/Drucksache so it pins the fix.

Git workflow: branch `fix/issue-<N>-<slug>` off `main`, commit `fix: issue #<N> <summary>`,
PR into `main` on Codeberg. Don't commit or push unless asked.

## Commands

```bash
make install         # venv + poetry install (fetches corelib from its pinned git tag)
make test            # unit tests
make test-all        # unit + integration (integration needs a backend)
make lint            # ruff lint (CI enforces: ruff-lint + ruff-format + pytest + pip-audit)
make format          # ruff format (black-compatible)
make run             # run the scraper (needs config.toml + Redis)

# Dry run — no API/Redis, fast local diagnosis of the pipeline:
.venv/bin/python -m bawue.dry_run --scraper vorgaenge --limit 3 --verbosity 2
```

Config is 4-tier: defaults → TOML (`config.*.toml`) → env vars → CLI. LLM document
enrichment (`bawue_dok.py`) is **off by default** (needs `LLM_PROVIDER_KEY`).

## Git Commit

This repo uses semantic-releases and requires "conventional commit" messages.
Syntax:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Types: `feat`, `fix`, `build`, `chore`, `ci`, `docs`, `style`, `refactor`, `perf`, `test`  
Breaking changes: a commit that has a footer `BREAKING CHANGE:`

## Local dev data / logs

`locallogs/*.jsonl` are produced when `api-obj-log = "locallogs"` is set (see
`config.wp17.toml` / `config.dev.toml`). The file is named by collector UUID
(`…017.jsonl` = WP17). Each line is `json.dumps(item, default=str) + ",\n"` — i.e. one fully
built `Vorgang` per line as a Python `repr` string (**post** `_build_vorgang`, so stable
`api_id`s are already assigned). It's a data dump, not an error log. A local dev run for WP17
already exists; the mock backend (`mock_pazufa_server.py`) allows end-to-end runs without a
real PaZuFa backend.

## Key files (`src/bawue/`)

`bawue_vorgaenge_scraper.py` (PARLIS → Vorgang; station building, ordering, synthetic
stations, stable ids) · `parlis_client.py` / `parlis_parser.py` (PARLIS fetch + parse) ·
`enum_mapper.py` (PARLIS terms → PaZuFa enums, context-aware) · `bawue_dok.py` (PDF + LLM
enrichment) · `pipeline.py` (scraper base classes, run loop, `log_item`) ·
`bawue_beteiligung_scraper.py` / `bawue_sitzungen_scraper.py` / `ics_parser.py` ·
`config.py` / `config_loader.py` · `cache.py` (Redis) · `upload_throttle.py` (retry/429).
