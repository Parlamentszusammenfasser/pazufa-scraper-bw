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
python3.14 -m venv .venv
make install

cp config.sample.toml config.toml
# Edit config.toml: set ltzf-api-url, ltzf-api-key, collector-uuid...
```

## Build

### Application

After setup, install all dependencies and vendor the collector libraries:

```bash
make install
```

This creates the virtual environment, installs Poetry, vendors `pazufa-collector` and `pazufa-collector-core` from
sibling directories, and runs `poetry install`.

### Docker Image

Build the Docker image with all dependencies vendored and tests passing:

```bash
make package
```

This runs `install`, `lint`, `format`, and `test` before building the image. The resulting image is tagged
`bawue-scraper` and includes Tesseract OCR with the German language pack.

To build the Docker image directly (skipping lint/test):

```bash
docker build -t bawue-scraper .
```

The image uses a multi-stage build (Python 3.14-slim) — the builder stage installs dependencies, and the runtime
stage copies only the installed packages and source code.

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

## Running against Local Backend

For full end-to-end testing with a real backend and persistent data. Requires
`pazufa-backend` cloned alongside this repo (use the `dev-0.2.7` branch for
compatibility with OpenAPI spec v0.2.3):

```bash
git clone https://codeberg.org/PaZuFa/pazufa-backend.git ../pazufa-backend
cd ../pazufa-backend && git checkout dev-0.2.7 && cd -
```

**1. Start the dev stack** (first run compiles Rust + generates OpenAPI code — takes several minutes):

```bash
docker-compose -f docker-compose.dev.yml up -d --build
```

**2. Wait for the backend to be ready:**

```bash
docker-compose -f docker-compose.dev.yml logs -f pazufa-backend
# Ready when you see the backend accepting connections on port 80
# Or poll: curl -s http://127.0.0.1:8090/ping
```

**3. Create a collector API key** using the keyadder key (`dev-keyadder-key`):

```bash
curl -s -X POST http://127.0.0.1:8090/api/v2/auth \
  -H "X-API-Key: dev-keyadder-key" \
  -H "Content-Type: application/json" \
  -d '{"scope": "collector"}' | jq .
```

Copy the returned API key value (starts with `ltzf_`).

**4. Configure the scraper:**

Update `config.dev.toml` with your collector key:

```bash
# Edit config.dev.toml and paste your key into the ltzf-api-key field
```

> **Warning — `.env` overrides config files:** If you have a `.env` file (used
> for staging deployments), `load_dotenv()` loads it into the process
> environment, and environment variables take precedence over `config.dev.toml`.
> To ensure the scraper targets the local backend, either:
>
> - **Export the local values** before running:
>   ```bash
>   export LTZF_API_URL=http://127.0.0.1:8090
>   export LTZF_API_KEY=<your-local-collector-key>
>   ```
> - **Or rename/remove `.env`** while doing local development.

**5. Run the scraper against the local backend:**

```bash
.venv/bin/python -m collector --config-file config.dev.toml --once
```

**6. Inspect submitted data via the backend API:**

```bash
# List submitted Vorgänge:
curl -s "http://127.0.0.1:8090/api/v2/vorgang?parlament=BW" | jq '.[] | {id, titel}'

# Check Kalender entries:
curl -s "http://127.0.0.1:8090/api/v2/kalender?parlament=BW" | jq .

# Backend status:
curl -s http://127.0.0.1:8090/status | jq .
```

**7. (Optional) View results in the frontend:**

Clone and run the PaZuFa website alongside the dev stack:

```bash
git clone https://codeberg.org/flovar/pazufa-website.git ../pazufa-website
cd ../pazufa-website
npm install
echo "VITE_API_URL=http://127.0.0.1:8090" > .env.local
npm run dev
```

Open http://localhost:5173/pazufa-website/ — the site connects directly to the local backend.

**Tear down** (removes containers but keeps the PostgreSQL volume):

```bash
docker-compose -f docker-compose.dev.yml down
# To also wipe the database:
docker-compose -f docker-compose.dev.yml down -v
```

**Clean restart** (wipe all data and rebuild):

```bash
docker-compose -f docker-compose.dev.yml down -v
docker-compose -f docker-compose.dev.yml up -d --build
# Then re-create the collector API key (step 3)
```

| Service      | URL                   | Notes                             |
|--------------|-----------------------|-----------------------------------|
| Backend API  | http://127.0.0.1:8090 | REST API + `/ping`, `/status`     |
| PostgreSQL   | localhost:5432        | ltzf-user / ltzf-pass / ltzf      |
| Redis        | localhost:6379        | Cache for the scraper framework   |
| Keyadder key | `dev-keyadder-key`    | Used to create collector API keys |

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

**Always run `make lint` and `make format` after making changes** to ensure CI passes. The Woodpecker CI
pipeline checks both linting and formatting on every push and pull request.

### LLM Integration Tests

The scraper optionally enriches documents with LLM-extracted metadata (summary, keywords, short title). The
LLM integration tests download a real PDF from the Landtag BW website and call a real LLM API to verify the
full enrichment pipeline end-to-end.

**Requirements:**
- An LLM provider API key (e.g. OpenAI)
- Internet access (downloads PDFs from `landtag-bw.de`)

**Run:**

```bash
LLM_PROVIDER_KEY=sk-... pytest -m integration tests/integration/test_llm_extraction.py -s
```

Optionally set `LLM_MODEL` to override the default model (`gpt-4o-mini`):

```bash
LLM_PROVIDER_KEY=sk-... LLM_MODEL=gpt-4o pytest -m integration tests/integration/test_llm_extraction.py -s
```

The tests are skipped automatically when `LLM_PROVIDER_KEY` is not set, so `make test-integration` and CI
runs are unaffected.

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

**4. Rebuild the docker image:**
```bash
docker-compose build scraper && docker-compose up -d scraper
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
- [CI Pipeline](https://ci.codeberg.org/repos/16437) — Woodpecker CI build status
- [Landtag BaWue](https://www.landtag-bw.de/)
- [PaZuFa Staging Frontend](https://staging.pazufa.de/)
