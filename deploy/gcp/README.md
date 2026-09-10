# Deployment — GCP Cloud Run Jobs

The scraper runs as a **Cloud Run Job** triggered once a day by Cloud Scheduler,
caching to a per-environment **Upstash Redis** database. See
`../../docs/hosting-options.md` for why.

```
Cloud Scheduler (0 3 * * *, Europe/Berlin)
    → Cloud Run Job  bawue-scraper-staging   2 vCPU / 4 GiB, --once
        → Upstash Redis  (REDIS_URL, rediss://)
        → PaZuFa backend (staging.api.pazufa.de)
        → OpenAI
    → Cloud Logging
```

Two properties keep this small:

- **Nothing is built for the cloud.** Cloud Run pulls the multi-arch image
  `ci.yml` already published to Docker Hub. No Artifact Registry, no rebuild, no
  second push — the artifact that ran through lint, tests and Trivy is the
  artifact that runs.
- **No per-environment config file.** The image ships `config.sample.toml` as its
  `config.toml`; everything that differs per environment is an env var set on the
  job. Deploying production means the same image with different variables.

No Terraform: `bootstrap.sh` is a one-time script and the workflow uses `gcloud`
directly, which is create-or-update on every step.

## One-time setup

1. **Upstash** — create a Redis database in `eu-central-1`. Copy the connection
   string from the *Redis (TLS)* tab, **not** the REST tab:
   `rediss://default:<token>@<endpoint>.upstash.io:6379`

2. **GCP** — with `gcloud auth login` done:

   ```bash
   PROJECT_ID=pazufa-bawue-scraper ALERT_EMAIL=you@example.org ./deploy/gcp/bootstrap.sh
   ```

   Enables APIs, creates three service accounts, empty Secret Manager entries,
   Workload Identity Federation so GitHub Actions authenticates without a
   long-lived JSON key, and — if `ALERT_EMAIL` is set — an alert policy that
   emails you when a run fails. Re-runnable, and it *converges* the WIF provider
   rather than skipping it, so tightening the attribute condition takes effect.

3. **Secrets** — set the values; the script prints these commands with the right
   names. They live only in Secret Manager, so rotating one takes effect on the
   next run with no redeploy:

   ```bash
   printf '%s' 'VALUE' | gcloud secrets versions add bawue-staging-redis-url --data-file=-
   ```

   `ltzf-api-key` · `llm-provider-key` · `redis-url` · `mattermost-hook`

4. **GitHub** — repository *variables*:

   | Variable | Value |
   |---|---|
   | `GCP_PROJECT_ID` | e.g. `pazufa-bawue-scraper` |
   | `GCP_REGION` | `europe-west3` |
   | `DOCKER_IMAGE` | `schneefisch/pazufa-bawue-scraper` |
   | `WIF_PROVIDER` | printed by `bootstrap.sh` |
   | `WIF_SERVICE_ACCOUNT` | `bawue-deployer@<project>.iam.gserviceaccount.com` |

   Environment **`staging`** variables:

   | | Name |
   |---|---|
   | Required | `COLLECTOR_ID`, `LTZF_API_URL` |
   | Optional | `WAHLPERIODE`, `WAHLPERIODE_START_DATE`, `PARLIS_REQUEST_DELAY_S`, `BETEILIGUNG_WAHLPERIODE` |

   Optional ones left unset fall back to whatever the image's `config.toml` says.
   Add a protection rule restricting deployment branches — the WIF condition
   requires a GitHub environment claim, so that rule is the actual deploy gate.

## Deploying

Actions → **Deploy staging** → Run workflow → enter an image tag. Prefer an
explicit semver (`1.2.3`) over `latest`, so the job spec records what is running.

The workflow points the job at that Docker Hub tag, syncs the schedule, and — if
*smoke test* is left on — executes the job once and fails if it errors. That
execution also measures run duration, which is the whole cost driver.

Turn *smoke test* **off** for the very first deploy: with an empty cache that run
is a full-Wahlperiode backfill and will outlast the 45-minute step budget. Deploy,
then `gcloud run jobs execute` it yourself and follow the logs.

## Operating

```bash
REGION=europe-west3

# Run now, outside the schedule
gcloud run jobs execute bawue-scraper-staging --region $REGION

# Follow the last execution's logs
gcloud beta run jobs logs tail bawue-scraper-staging --region $REGION

# Logs for a specific execution
gcloud logging read \
  'resource.type=cloud_run_job AND resource.labels.job_name=bawue-scraper-staging' \
  --limit 100 --freshness 1d

# Pause / resume the schedule.
# A deploy resumes a paused job — pause again after redeploying if you meant it.
gcloud scheduler jobs pause  bawue-scraper-staging --location $REGION
gcloud scheduler jobs resume bawue-scraper-staging --location $REGION
```

## Notes

- **`api-obj-log` stays off.** It writes one JSONL line per Vorgang — a few
  hundred MB on a full run — and Cloud Run's filesystem is in-memory, so it would
  eat the task's memory budget for a file discarded when the task exits. It is off
  unless explicitly configured; the job sets no `API_OBJ_LOG`.
- **6 h task timeout.** The steady-state run is ~20 min, but the first run against
  an empty cache backfills the whole Wahlperiode. The timeout is per attempt.
- **Docker Hub is in the pull path.** Cloud Run caches a tag for up to an hour;
  a Hub outage at task-start time fails that run. If that becomes a problem, put
  an Artifact Registry remote repository in front of it and change only the
  `--image` value.
- **Production** reuses all of this: run `bootstrap.sh` with `ENVIRONMENT=prod`,
  copy the workflow, and swap `staging` → `prod` in `JOB_NAME`, `SECRET_PREFIX`,
  `ENVIRONMENT` and the GitHub environment name.
