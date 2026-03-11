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

## Prerequisites

- Raspberry Pi with Docker installed (ARM64 / armv7)
- Docker Compose plugin (ships with modern Docker installs)

Install Docker on the Pi:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER  # then log out and back in
```

## Setup

1. Clone the repository on the Pi and navigate to the deploy directory:

   ```bash
   git clone https://github.com/schneefisch/pazufa-bawue-scraper.git
   cd pazufa-bawue-scraper/deploy/raspberry-pi
   ```

2. Run the setup script:

   ```bash
   chmod +x setup.sh
   ./setup.sh
   ```

   This will:
   - Copy `.env.example` → `.env` (if `.env` doesn't exist)
   - Build the Docker image
   - Start Redis as a background service
   - Add a daily 03:00 crontab entry

3. Edit `.env` with your API keys:

   ```bash
   nano .env
   ```

   | Variable           | Description                          |
   |--------------------|--------------------------------------|
   | `LTZF_API_KEY`     | PaZuFa backend API key               |
   | `LLM_PROVIDER_KEY` | LLM API key for summarization        |
   | `LTZF_API_URL`     | PaZuFa backend base URL              |
   | `COLLECTOR_ID`     | Unique collector identifier (UUID)   |

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

### Rebuild after a code update

```bash
cd deploy/raspberry-pi
git pull
docker compose build scraper
```

## Cost

$0/month (beyond electricity — ~2–5W idle for a Pi 4/5).
