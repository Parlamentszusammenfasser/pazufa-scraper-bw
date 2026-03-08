#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# One-time GCP project bootstrap for pazufa-bawue-scraper
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - A GCP billing account available
#
# Usage:
#   1. Fill in the placeholder values below (marked with <...>)
#   2. chmod +x deploy/setup.sh
#   3. ./deploy/setup.sh
# =============================================================================

PROJECT_ID="pazufa-bawue-scraper"
REGION="europe-west3"
REPO_NAME="pazufa"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/bawue-scraper"
JOB_NAME="bawue-scraper"
REDIS_INSTANCE="pazufa-cache"

# -- Placeholders: fill these in before running --
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"           # e.g. "012345-6789AB-CDEF01"
LTZF_API_KEY="${LTZF_API_KEY:-}"                 # PaZuFa backend API key
LLM_PROVIDER_KEY="${LLM_PROVIDER_KEY:-}"          # LLM API key for summarization
LTZF_API_URL="${LTZF_API_URL:-}"                 # e.g. "https://api.pazufa.example.com"
COLLECTOR_ID="${COLLECTOR_ID:-}"                 # e.g. "550e8400-e29b-41d4-a716-446655440000"

# -- Validation --
missing=()
[[ -z "$LTZF_API_KEY" ]] && missing+=("LTZF_API_KEY")
[[ -z "$LLM_PROVIDER_KEY" ]] && missing+=("LLM_PROVIDER_KEY")
[[ -z "$LTZF_API_URL" ]] && missing+=("LTZF_API_URL")
[[ -z "$COLLECTOR_ID" ]] && missing+=("COLLECTOR_ID")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: The following environment variables must be set:"
  printf '  - %s\n' "${missing[@]}"
  echo ""
  echo "Export them before running this script, e.g.:"
  echo "  export LTZF_API_KEY=sk-..."
  exit 1
fi

echo "==> Creating GCP project: ${PROJECT_ID}"
gcloud projects create "$PROJECT_ID" --name="PaZuFa BaWue Scraper" || echo "Project already exists, continuing..."
gcloud config set project "$PROJECT_ID"

if [[ -n "$BILLING_ACCOUNT" ]]; then
  echo "==> Linking billing account"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
else
  echo "WARNING: No BILLING_ACCOUNT set. Link billing manually:"
  echo "  gcloud billing projects link ${PROJECT_ID} --billing-account=YOUR_ACCOUNT_ID"
  echo ""
  read -rp "Press Enter once billing is linked..."
fi

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  redis.googleapis.com \
  vpcaccess.googleapis.com

echo "==> Creating Artifact Registry repository"
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" || echo "Repository already exists, continuing..."

echo "==> Creating secrets in Secret Manager"
echo -n "$LTZF_API_KEY" | gcloud secrets create LTZF_API_KEY --data-file=- || \
  echo -n "$LTZF_API_KEY" | gcloud secrets versions add LTZF_API_KEY --data-file=-
echo -n "$LLM_PROVIDER_KEY" | gcloud secrets create LLM_PROVIDER_KEY --data-file=- || \
  echo -n "$LLM_PROVIDER_KEY" | gcloud secrets versions add LLM_PROVIDER_KEY --data-file=-

echo "==> Creating VPC connector for Memorystore access"
gcloud compute networks vpc-access connectors create pazufa-connector \
  --region="$REGION" \
  --range="10.8.0.0/28" || echo "VPC connector already exists, continuing..."

echo "==> Creating Memorystore Redis instance (this may take a few minutes)"
gcloud redis instances create "$REDIS_INSTANCE" \
  --size=1 \
  --region="$REGION" \
  --tier=basic || echo "Redis instance already exists, continuing..."

REDIS_HOST=$(gcloud redis instances describe "$REDIS_INSTANCE" \
  --region="$REGION" --format="value(host)")
echo "Redis host: ${REDIS_HOST}"

echo "==> Building and pushing initial Docker image"
gcloud builds submit --tag "$IMAGE"

echo "==> Creating Cloud Run Job"
gcloud run jobs create "$JOB_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --memory=4Gi \
  --cpu=2 \
  --task-timeout=3600s \
  --max-retries=1 \
  --set-secrets="LTZF_API_KEY=LTZF_API_KEY:latest,LLM_PROVIDER_KEY=LLM_PROVIDER_KEY:latest" \
  --set-env-vars="LTZF_API_URL=${LTZF_API_URL},REDIS_HOST=${REDIS_HOST},REDIS_PORT=6379,COLLECTOR_ID=${COLLECTOR_ID}" \
  --vpc-connector=pazufa-connector

echo "==> Creating Cloud Scheduler trigger (daily at 03:00 CET)"
gcloud scheduler jobs create http "${JOB_NAME}-daily" \
  --location="$REGION" \
  --schedule="0 3 * * *" \
  --time-zone="Europe/Berlin" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
  --http-method=POST \
  --oauth-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com"

echo "==> Granting Cloud Build permission to deploy Cloud Run Jobs"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/run.developer"

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Test with:"
echo "  gcloud run jobs execute ${JOB_NAME} --region=${REGION}"
echo ""
echo "View logs:"
echo "  gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=${JOB_NAME}' --limit=50"
echo ""
