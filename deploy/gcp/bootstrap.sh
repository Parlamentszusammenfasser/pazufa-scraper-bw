#!/usr/bin/env bash
# One-time GCP setup for the BaWue scraper. Run locally, once per project.
# Everything after this is done by .github/workflows/deploy-staging.yml.
#
#   gcloud auth login
#   PROJECT_ID=pazufa-bawue-scraper ./deploy/gcp/bootstrap.sh
#
# Optional: ALERT_EMAIL=you@example.org to get notified when a run fails.
#
# Idempotent: re-running it is safe, and it converges the WIF provider rather
# than skipping it — so tightening the attribute condition here takes effect.
#
# No Artifact Registry: Cloud Run pulls the released image straight from Docker
# Hub, so there is nothing to mirror and no registry to grant write access to.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-europe-west3}"
ENVIRONMENT="${ENVIRONMENT:-staging}"
GITHUB_REPO="${GITHUB_REPO:-Parlamentszusammenfasser/pazufa-scraper-bw}"
DOCKER_IMAGE="${DOCKER_IMAGE:-schneefisch/pazufa-bawue-scraper}"
ALERT_EMAIL="${ALERT_EMAIL:-}"

DEPLOYER_SA="bawue-deployer"
RUNTIME_SA="bawue-runtime"
SCHEDULER_SA="bawue-scheduler"
POOL="github"
PROVIDER="github-oidc"
JOB_NAME="bawue-scraper-${ENVIRONMENT}"

# An exported CLOUDSDK_CORE_PROJECT outranks `gcloud config set project`, so a
# stale one would silently apply this whole script to the wrong project.
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"

echo "==> Enabling APIs"
# cloudresourcemanager + iam are not on by default in a new project and are
# needed by the very next command, so this has to come first.
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  monitoring.googleapis.com

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

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
# Deployer: create/update the job and its schedule. run.developer rather than
# run.admin — the latter also grants setIamPolicy, i.e. the ability to make any
# Cloud Run resource publicly invokable.
grant "$DEPLOYER_SA" roles/run.developer
grant "$DEPLOYER_SA" roles/cloudscheduler.admin
# Needed to deploy a job that *runs as* the runtime SA, and to set the
# scheduler's OAuth identity.
for target in "$RUNTIME_SA" "$SCHEDULER_SA"; do
  gcloud iam service-accounts add-iam-policy-binding \
    "${target}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --member="serviceAccount:${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role=roles/iam.serviceAccountUser >/dev/null
done
# Scheduler: trigger job executions.
grant "$SCHEDULER_SA" roles/run.invoker

echo "==> Secrets"
# Access is granted per secret, not project-wide: a compromised staging runtime
# must not be able to read bawue-prod-* from the same project.
for name in ltzf-api-key llm-provider-key redis-url mattermost-hook; do
  secret="bawue-${ENVIRONMENT}-${name}"
  gcloud secrets describe "$secret" >/dev/null 2>&1 || \
    gcloud secrets create "$secret"
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor --condition=None >/dev/null
done

echo "==> Workload Identity Federation for GitHub Actions"
# Keyless auth — no long-lived service-account JSON in GitHub secrets.
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global \
    --display-name="GitHub Actions"

# Requiring a GitHub *environment* claim is what limits this to the reviewed
# deploy workflow: a token minted by an arbitrary branch's workflow carries no
# environment and is rejected. GitHub Environment protection rules then decide
# who and which branch may deploy.
WIF_ARGS=(
  --location=global
  --workload-identity-pool="$POOL"
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.environment=assertion.environment"
  --attribute-condition="assertion.repository == '${GITHUB_REPO}' && assertion.environment in ['staging', 'production']"
)
if gcloud iam workload-identity-pools providers describe "$PROVIDER" \
     --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" "${WIF_ARGS[@]}" >/dev/null
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" "${WIF_ARGS[@]}" \
    --issuer-uri="https://token.actions.githubusercontent.com" >/dev/null
fi

# Only this repository may impersonate the deployer.
gcloud iam service-accounts add-iam-policy-binding \
  "${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" >/dev/null

if [ -n "$ALERT_EMAIL" ]; then
  echo "==> Failure alert"
  # A scheduled job that fails silently is the classic failure mode here: the
  # Mattermost summary only fires if the scraper reaches the end, so a crash,
  # OOM or timeout produces no notification at all.
  CHANNEL="$(gcloud alpha monitoring channels list \
    --filter="displayName='bawue-${ENVIRONMENT}-alerts'" --format='value(name)' | head -1)"
  if [ -z "$CHANNEL" ]; then
    CHANNEL="$(gcloud alpha monitoring channels create \
      --display-name="bawue-${ENVIRONMENT}-alerts" \
      --type=email --channel-labels="email_address=${ALERT_EMAIL}" \
      --format='value(name)')"
  fi
  if [ -z "$(gcloud alpha monitoring policies list \
       --filter="displayName='${JOB_NAME} failed'" --format='value(name)')" ]; then
    gcloud alpha monitoring policies create --notification-channels="$CHANNEL" --policy-from-file=- <<POLICY
{
  "displayName": "${JOB_NAME} failed",
  "combiner": "OR",
  "conditions": [{
    "displayName": "task attempt failed",
    "conditionThreshold": {
      "filter": "metric.type=\"run.googleapis.com/job/completed_task_attempt_count\" AND resource.type=\"cloud_run_job\" AND resource.label.\"job_name\"=\"${JOB_NAME}\" AND metric.label.\"result\"=\"failed\"",
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0,
      "duration": "0s",
      "aggregations": [{"alignmentPeriod": "3600s", "perSeriesAligner": "ALIGN_SUM"}]
    }
  }]
}
POLICY
  fi
fi

cat <<SUMMARY

Done.

1) Set the secret values (they live only here — the deploy workflow just
   references them by name, so rotating one takes effect on the next run):

     printf '%s' 'VALUE' | gcloud secrets versions add bawue-${ENVIRONMENT}-ltzf-api-key    --data-file=-
     printf '%s' 'VALUE' | gcloud secrets versions add bawue-${ENVIRONMENT}-llm-provider-key --data-file=-
     printf '%s' 'VALUE' | gcloud secrets versions add bawue-${ENVIRONMENT}-redis-url        --data-file=-
     printf '%s' 'VALUE' | gcloud secrets versions add bawue-${ENVIRONMENT}-mattermost-hook  --data-file=-

2) GitHub → Settings → Secrets and variables → Actions → Variables, repository:

     GCP_PROJECT_ID       ${PROJECT_ID}
     GCP_REGION           ${REGION}
     DOCKER_IMAGE         ${DOCKER_IMAGE}
     WIF_PROVIDER         projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}
     WIF_SERVICE_ACCOUNT  ${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com

3) GitHub Environment "${ENVIRONMENT}" — variables:

     required: COLLECTOR_ID, LTZF_API_URL
     optional: WAHLPERIODE, WAHLPERIODE_START_DATE, PARLIS_REQUEST_DELAY_S,
               BETEILIGUNG_WAHLPERIODE   (unset = the image's config.toml default)

   Add a protection rule restricting deployment branches — the WIF condition
   requires an environment claim, so that rule is the actual deploy gate.
SUMMARY
