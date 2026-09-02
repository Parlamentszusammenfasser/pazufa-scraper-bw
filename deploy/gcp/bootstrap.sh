#!/usr/bin/env bash
# One-time GCP setup for the BaWue scraper. Run locally, once per project.
# Everything after this is done by .github/workflows/deploy-staging.yml.
#
#   gcloud auth login
#   PROJECT_ID=pazufa-bawue-scraper ./deploy/gcp/bootstrap.sh
#
# Idempotent: re-running it is safe.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-europe-west3}"
ENVIRONMENT="${ENVIRONMENT:-staging}"
GITHUB_REPO="${GITHUB_REPO:-Parlamentszusammenfasser/pazufa-scraper-bw}"

REPO_NAME="pazufa"
DEPLOYER_SA="bawue-deployer"
RUNTIME_SA="bawue-runtime"
SCHEDULER_SA="bawue-scheduler"
POOL="github"
PROVIDER="github-oidc"

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com

echo "==> Artifact Registry"
gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker --location="$REGION" \
    --description="PaZuFa scraper images"

echo "==> Service accounts"
for sa in "$DEPLOYER_SA" "$RUNTIME_SA" "$SCHEDULER_SA"; do
  gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$sa"
done

grant() {  # grant <sa> <role>
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${1}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="$2" --condition=None >/dev/null
}

echo "==> IAM"
# Deployer: push images, create/update the job and scheduler, add secret versions.
grant "$DEPLOYER_SA" roles/run.admin
grant "$DEPLOYER_SA" roles/artifactregistry.writer
grant "$DEPLOYER_SA" roles/secretmanager.secretVersionAdder
grant "$DEPLOYER_SA" roles/cloudscheduler.admin
# Needed to deploy a job that *runs as* the runtime SA, and to set the
# scheduler's OAuth identity.
for target in "$RUNTIME_SA" "$SCHEDULER_SA"; do
  gcloud iam service-accounts add-iam-policy-binding \
    "${target}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --member="serviceAccount:${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role=roles/iam.serviceAccountUser >/dev/null
done
# Runtime: read the secrets the job mounts as env vars.
grant "$RUNTIME_SA" roles/secretmanager.secretAccessor
# Scheduler: trigger job executions.
grant "$SCHEDULER_SA" roles/run.invoker

echo "==> Secrets (empty — the deploy workflow adds versions from GitHub)"
for name in ltzf-api-key llm-provider-key redis-url; do
  secret="bawue-${ENVIRONMENT}-${name}"
  gcloud secrets describe "$secret" >/dev/null 2>&1 || \
    gcloud secrets create "$secret" --replication-policy=automatic
done

echo "==> Workload Identity Federation for GitHub Actions"
# Keyless auth — no long-lived service-account JSON in GitHub secrets.
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global \
    --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository == '${GITHUB_REPO}'"

# Only this repository may impersonate the deployer.
gcloud iam service-accounts add-iam-policy-binding \
  "${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" >/dev/null

cat <<SUMMARY

Done. Set these as GitHub Actions *variables* (Settings → Secrets and variables
→ Actions → Variables), on the repository:

  GCP_PROJECT_ID       ${PROJECT_ID}
  GCP_REGION           ${REGION}
  WIF_PROVIDER         projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}
  WIF_SERVICE_ACCOUNT  ${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com

And on GitHub Environment "${ENVIRONMENT}":

  variables: COLLECTOR_ID, LTZF_API_URL
  secrets:   LTZF_API_KEY, LLM_PROVIDER_KEY, REDIS_URL
SUMMARY
