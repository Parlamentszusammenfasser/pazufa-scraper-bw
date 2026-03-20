# BaWue Scraper

Collector plugin for the Baden-Württemberg state parliament ([Landtag BW](https://www.landtag-bw.de/)) for the
[Parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser) (PaZuFa) platform.

Scrapes legislative proceedings (Vorgänge) from PARLIS, pre-parliamentary drafts from the Beteiligungsportal, and
parliamentary sessions (Sitzungen) from the ICS calendar feed. Runs as a **framework-managed plugin** — the
[pazufa-collector](https://codeberg.org/PaZuFa/pazufa-collector) framework handles scheduling, caching (Redis), API
submission, and error tolerance automatically.

## Prerequisites

- Python 3.14+
- [pazufa-collector](https://codeberg.org/PaZuFa/pazufa-collector) cloned alongside this project
- Redis (for local dev: `docker-compose up -d`)
- Tesseract OCR with German language pack (for PDF extraction via framework pipeline)

## Setup

```bash
git clone <repo-url> pazufa-bawue-scraper
git clone https://codeberg.org/PaZuFa/pazufa-collector.git

cd pazufa-bawue-scraper
python3 -m venv .venv
make install

cp config.sample.toml config.toml
# Edit config.toml: set ltzf-api-url, ltzf-api-key, collector-uuid
```

## Usage

### Run

```bash
make run
# or: .venv/bin/python -m collector --config-file config.toml
```

### Dry Run (no API or Redis required)

Runs the scraper pipeline without posting to the API — useful for local diagnosis:

```bash
.venv/bin/python -m bawue.dry_run                                            # all scrapers, 7-day lookback
.venv/bin/python -m bawue.dry_run --scraper vorgaenge --limit 3 --verbosity 2  # quick PARLIS check
.venv/bin/python -m bawue.dry_run --scraper sitzungen                          # ICS calendar only
.venv/bin/python -m bawue.dry_run --json --limit 5                             # JSON output
```

| Option            | Default      | Description                                               |
|-------------------|--------------|-----------------------------------------------------------|
| `--scraper`       | `all`        | `vorgaenge`, `beteiligung`, `sitzungen`, or `all`         |
| `--vorgangstyp`   | *(all)*      | Limit to one PARLIS Vorgangstyp (e.g. `"Kleine Anfrage"`) |
| `--lookback-days` | 7            | Days to look back for PARLIS search                       |
| `--wahlperiode`   | 17           | Wahlperiode number                                        |
| `--limit`         | *(no limit)* | Max items per scraper                                     |
| `--verbosity`     | 0            | 0=summary, 1=type breakdown, 2=per-item detail            |
| `--json`          | off          | Output JSON instead of formatted text                     |
| `--ics-url`       | *(default)*  | Custom ICS calendar URL                                   |

### Mock Backend

For end-to-end testing with real API submission but without a running PaZuFa backend:

**1. Start Redis:**
```bash
docker-compose up -d
```

**2. Start the mock server:**
```bash
.venv/bin/python mock_pazufa_server.py --port 8080
```

**3. Configure `config.toml`:**
```toml
[backend]
ltzf-api-url = "http://127.0.0.1:8080"
ltzf-api-key  = "any-key-or-paste-a-real-jwt-here"
```

**4. Run:**
```bash
make run
```

The mock server logs every request with headers, JWT decode of `X-API-Key`, and pretty-printed JSON body (first 60
lines). Returns HTTP 201 for all valid requests; 401 if `X-API-Key` is missing.

## Development

```bash
make test             # Unit tests (fast, default)
make test-cov         # Tests with coverage
make test-all         # All tests including integration
make test-integration # Integration tests only (requires backend)
make lint             # Lint
make lint-fix         # Lint with auto-fix
make format           # Format code
make clean            # Remove .venv, __pycache__, .pytest_cache
```

Run `make help` to list all targets.

## Running against Staging

The `docker-compose.yml` runs the scraper with Redis and expects secrets in a `.env` file (git-ignored).

**1. Create `.env` from the example:**
```bash
cp .env.example .env
# Edit .env and fill in the real values
```

| Variable           | Description                                               |
|--------------------|-----------------------------------------------------------|
| `LTZF_API_URL`     | PaZuFa backend URL (e.g. `https://staging.api.pazufa.de`) |
| `LTZF_API_KEY`     | PaZuFa API key (`ltzf_...`)                               |
| `LLM_PROVIDER_KEY` | LLM provider API key (e.g. OpenAI `sk-...`)               |

**2. Start the stack:**
```bash
docker-compose up -d
```

This mounts `config.staging.toml` as the config file and injects secrets from `.env` into the container. The scraper re-runs every 5 minutes (`CYCLE_TIME_S=300`). Logs are persisted to `./locallogs/`.

**3. Watch logs:**
```bash
docker-compose logs -f scraper
```

## Docker

```bash
make package          # Vendor collector + build image
docker run bawue-scraper
```

The image includes Tesseract OCR. Redis should be provided as a separate service (e.g. via docker-compose).

## Configuration

4-tier precedence: Defaults → `config.toml` → Environment variables → CLI.

Key env vars: `LTZF_API_KEY`, `LTZF_API_URL`, `COLLECTOR_ID`, `REDIS_HOST`, `LLM_PROVIDER_KEY`

See [docs/anforderungen.md — Konfiguration](docs/anforderungen.md#konfiguration) for the full reference.

## Documentation

| Topic                                                      | File                                           |
|------------------------------------------------------------|------------------------------------------------|
| Requirements, data models, API, enumerations, data sources | [docs/anforderungen.md](docs/anforderungen.md) |
| Architecture, components, PARLIS strategy, enum mapping    | [docs/architecture.md](docs/architecture.md)   |
| Implementation status, field matrix, roadmap               | [docs/status.md](docs/status.md)               |

## Links

- [PaZuFa Organization](https://codeberg.org/PaZuFa)
- [parlamentszusammenfasser](https://codeberg.org/PaZuFa/parlamentszusammenfasser) — Main project
- [pazufa-collector](https://codeberg.org/PaZuFa/pazufa-collector) — Collector framework
- [pazufa-backend](https://codeberg.org/PaZuFa/pazufa-backend) — Backend service
- [PaZuFa backend OpenAPI Spec](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/openapi.yml)
- [Landtag BaWue](https://www.landtag-bw.de/)
