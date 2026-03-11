#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_JOB="0 3 * * * cd ${SCRIPT_DIR} && docker compose run --rm scraper"

# 1. Check Docker is installed
if ! command -v docker &>/dev/null; then
  echo "ERROR: Docker is not installed."
  echo "Install it with: curl -fsSL https://get.docker.com | sh"
  exit 1
fi

if ! docker compose version &>/dev/null; then
  echo "ERROR: 'docker compose' plugin not found."
  echo "Install Docker with the Compose plugin: curl -fsSL https://get.docker.com | sh"
  exit 1
fi

# 2. Copy .env.example → .env if .env doesn't exist
if [ ! -f "${SCRIPT_DIR}/.env" ]; then
  cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
  echo "Created .env from .env.example — edit it with your API keys before running the scraper."
else
  echo ".env already exists, skipping copy."
fi

# 3. Build the scraper image
echo "Building scraper image..."
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" build scraper

# 4. Start Redis
echo "Starting Redis..."
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" up -d redis

# 5. Add crontab entry (idempotent)
if crontab -l 2>/dev/null | grep -qF "${SCRIPT_DIR}"; then
  echo "Crontab entry already present, skipping."
else
  (crontab -l 2>/dev/null; echo "${CRON_JOB}") | crontab -
  echo "Added crontab entry: ${CRON_JOB}"
fi

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit ${SCRIPT_DIR}/.env with your API keys"
echo "  2. Test manually: docker compose -f ${SCRIPT_DIR}/docker-compose.yml run --rm scraper"
echo "  3. The scraper will run automatically every day at 03:00"
