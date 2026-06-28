# Deployment — Raspberry Pi

An alternative to GCP: run the scraper at home on a Raspberry Pi using Docker + cron. No cloud services, no billing.

## Architecture

```
cron (host, daily 03:00)
    → docker compose run scraper
        → Redis (Docker, persistent volume)
        → PaZuFa Backend API  [submission]
        → LLM API             [summarization]
```

The CI pipeline builds a multi-platform image (`linux/amd64`, `linux/arm/v7`, `linux/arm64`) and pushes it to Docker Hub. The Pi pulls the pre-built image — no build toolchain needed on the device.

## Prerequisites

- Raspberry Pi 3B (armv7) or newer with Docker installed
- Docker Compose plugin (ships with modern Docker installs)

Install Docker on the Pi:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER  # then log out and back in
```

## Codeberg CI secrets (one-time setup)

Before the pipeline can push to Docker Hub, add three secrets in Codeberg:
**Repo → Settings → Secrets → Add secret**

| Secret name       | Value                                         |
|-------------------|-----------------------------------------------|
| `docker_username` | Your Docker Hub username                      |
| `docker_password` | Docker Hub **access token** (not your password) — create at hub.docker.com → Account Settings → Security |
| `docker_repo`     | Full image path, e.g. `froeser/pazufa-scraper-bw` |

After the first successful CI run on `main`, the image will be available at `docker.io/<docker_repo>:main`.

## Image tags

| Trigger                        | Docker tags produced                          |
|-------------------------------|-----------------------------------------------|
| `git tag v1.2.3 && git push --tags` | `1.2.3`, `1.2`, `1`, `latest`        |
| push to `main`                | `main`, `main-<sha8>`                         |
| push to feature branch        | `dev-<sha8>`                                  |

Use a versioned tag (e.g. `1.2.3`) on the Pi for stability. Use `latest` for always-current.

## Setup on the Pi

1. Copy `.env.example` to `.env` in `deploy/raspberry-pi/`:

   ```bash
   cd deploy/raspberry-pi
   cp .env.example .env
   nano .env
   ```

2. Fill in `.env`:

   | Variable           | Description                                      |
   |--------------------|--------------------------------------------------|
   | `DOCKER_IMAGE`     | e.g. `froeser/pazufa-scraper-bw:latest`       |
   | `LTZF_API_KEY`     | PaZuFa backend API key                           |
   | `LLM_PROVIDER_KEY` | LLM API key for summarization                    |
   | `LTZF_API_URL`     | PaZuFa backend base URL                          |
   | `COLLECTOR_ID`     | Unique collector identifier (UUID)               |

3. Run the setup script:

   ```bash
   chmod +x setup.sh
   ./setup.sh
   ```

   This will:
   - Validate `DOCKER_IMAGE` is set
   - Pull the image from Docker Hub
   - Start Redis as a background service
   - Add a daily 03:00 crontab entry

## Operations

### Trigger a run manually

```bash
cd deploy/raspberry-pi
docker compose run --rm scraper
```

### View logs from the last run

```bash
cd deploy/raspberry-pi
docker compose logs scraper
```

### Check Redis is running

```bash
cd deploy/raspberry-pi
docker compose ps
```

### Update to a newer image

```bash
cd deploy/raspberry-pi
# Edit .env to update DOCKER_IMAGE tag if needed
docker compose pull scraper
```

### Local Mac testing

To test a specific image tag locally before deploying to the Pi:

```bash
# In the repo root, override the image used by docker-compose.yml
DOCKER_IMAGE=froeser/pazufa-scraper-bw:main docker compose up scraper
```

Or edit `docker-compose.yml` in the repo root to replace `bawue-scraper:latest` with your Docker Hub image name.

## Releasing a new version

```bash
git tag v1.2.3
git push origin v1.2.3
```

CI runs all quality gates (audit, lint, tests) and then builds + pushes the multi-platform image with semver tags. On the Pi:

```bash
cd deploy/raspberry-pi
# Update DOCKER_IMAGE in .env to e.g. froeser/pazufa-scraper-bw:1.2.3
docker compose pull scraper
```

## Cost

$0/month (beyond electricity — ~2–5W idle for a Pi 3B).
