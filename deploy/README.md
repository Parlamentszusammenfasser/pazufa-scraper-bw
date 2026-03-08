# Deployment — GCP Cloud Run Jobs

The scraper runs as a **Cloud Run Job** in `europe-west3` (Frankfurt), triggered daily by Cloud Scheduler.

## Architecture

```
Cloud Scheduler (daily 03:00 CET)
    → Cloud Run Job (bawue-scraper)
        → PARLIS (parlis.landtag-bw.de)   [scraping]
        → Memorystore Redis               [caching]
        → PaZuFa Backend API              [submission]
        → LLM API                         [LLM summarization]

Cloud Build (on push to main)
    → Artifact Registry → Cloud Run Job (image update)
```

## Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud` CLI)
- A GCP billing account
- API keys for PaZuFa backend (`LTZF_API_KEY`) and LLM provider (`LLM_PROVIDER_KEY`)

## Initial Setup

1. Export the required environment variables:

   ```bash
   export BILLING_ACCOUNT="012345-6789AB-CDEF01"
   export LTZF_API_KEY="your-pazufa-api-key"
   export LLM_PROVIDER_KEY="sk-..."
   export LTZF_API_URL="https://api.pazufa.example.com"
   export COLLECTOR_ID="550e8400-e29b-41d4-a716-446655440000"
   ```

2. Run the bootstrap script:

   ```bash
   chmod +x deploy/setup.sh
   ./deploy/setup.sh
   ```

   This creates the GCP project, enables APIs, sets up Artifact Registry, Secret Manager, Memorystore Redis, a VPC connector, builds and deploys the initial image, creates the Cloud Run Job, and configures the daily schedule.

3. Set up the Cloud Build trigger (GitHub → Cloud Build):

   ```bash
   gcloud builds triggers create github \
     --repo-name=pazufa-bawue-scraper \
     --repo-owner=schneefisch \
     --branch-pattern="^main$" \
     --build-config=cloudbuild.yaml
   ```

## Secrets & Environment Variables

| Variable         | Source         | Description                                |
|------------------|----------------|--------------------------------------------|
| `LTZF_API_KEY`      | Secret Manager | PaZuFa backend API key                     |
| `LLM_PROVIDER_KEY`  | Secret Manager | LLM API key for summarization              |
| `LTZF_API_URL`      | Env var        | PaZuFa backend base URL                    |
| `REDIS_HOST`        | Env var        | Memorystore Redis IP (set by setup script) |
| `REDIS_PORT`        | Env var        | Redis port (6379)                          |
| `COLLECTOR_ID`      | Env var        | Unique collector identifier                |

Secrets are injected via `--set-secrets` (mounted as env vars at runtime). To update a secret:

```bash
echo -n "new-value" | gcloud secrets versions add LTZF_API_KEY --data-file=-
```

## Operations

### Manually trigger a run

```bash
gcloud run jobs execute bawue-scraper --region=europe-west3
```

### View recent executions

```bash
gcloud run jobs executions list --job=bawue-scraper --region=europe-west3
```

### View logs

```bash
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=bawue-scraper" \
  --limit=50
```

### Update job configuration

```bash
gcloud run jobs update bawue-scraper \
  --region=europe-west3 \
  --memory=4Gi \
  --set-env-vars="KEY=VALUE"
```

## CI/CD

On every push to `main`, Cloud Build:

1. Builds the Docker image
2. Pushes it to Artifact Registry (tagged with the short commit SHA)
3. Updates the Cloud Run Job to use the new image

The job itself is **not** executed by Cloud Build — it only updates the image. The next scheduled trigger (or a manual execution) will use the new image.

## Cost Estimate (monthly)

| Resource                                 | Estimate       |
|------------------------------------------|----------------|
| Cloud Run Job (1x/day, ~15min, 4GB/2CPU) | ~$2–5          |
| Memorystore Redis (1GB basic)            | ~$35           |
| Artifact Registry                        | < $1           |
| Cloud Build                              | Free tier      |
| Secret Manager                           | < $1           |
| Cloud Scheduler                          | Free tier      |
| **Total**                                | **~$40/month** |
