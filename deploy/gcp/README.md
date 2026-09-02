# Deployment — GCP Cloud Run Jobs

The scraper runs as a **Cloud Run Job** triggered twice daily by Cloud Scheduler,
caching to a per-environment **Upstash Redis** database. See
`../../docs/hosting-options.md` for why.

```
Cloud Scheduler (0 3,15 Europe/Berlin)
    → Cloud Run Job  bawue-scraper-staging   2 vCPU / 4 GiB, --once
        → Upstash Redis  (REDIS_URL, rediss://)
        → PaZuFa backend (staging.api.pazufa.de)
        → OpenAI
    → Cloud Logging
```

No Terraform: `bootstrap.sh` is a one-time script and the workflow uses `gcloud`
directly, which is create-or-update on every step.

## One-time setup

1. **Upstash** — create a Redis database in `eu-central-1`. Copy the connection
   string from the *Redis (TLS)* tab, **not** the REST tab:
   `rediss://default:<token>@<endpoint>.upstash.io:6379`

2. **GCP** — with `gcloud auth login` done:

   ```bash
   PROJECT_ID=pazufa-bawue-scraper ./deploy/gcp/bootstrap.sh
   ```

   Enables APIs, creates the Artifact Registry repo, three service accounts,
   empty Secret Manager entries, and Workload Identity Federation so GitHub
   Actions authenticates without a long-lived JSON key. Re-runnable.

3. **GitHub** — the script prints the exact values. Repository *variables*:

   | Variable | Value |
   |---|---|
   | `GCP_PROJECT_ID` | e.g. `pazufa-bawue-scraper` |
   | `GCP_REGION` | `europe-west3` |
   | `WIF_PROVIDER` | printed by `bootstrap.sh` |
   | `WIF_SERVICE_ACCOUNT` | `bawue-deployer@<project>.iam.gserviceaccount.com` |

   Environment **`staging`**:

   | | Name |
   |---|---|
   | Variables | `COLLECTOR_ID`, `LTZF_API_URL` |
   | Secrets | `LTZF_API_KEY`, `LLM_PROVIDER_KEY`, `REDIS_URL` |

   `DOCKER_REPO` is already a repository secret (used by `ci.yml`).

## Deploying

Actions → **Deploy staging** → Run workflow → enter an image tag (`latest`, a
semver like `1.2.3`, or `main`).

The workflow rebuilds that released image with `config.staging.toml` baked in
(Cloud Run has no bind mounts and cannot pull from Docker Hub), pushes it to
Artifact Registry, syncs the GitHub secrets into Secret Manager, then
create-or-updates the job and its schedule.

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

# Pause / resume the schedule
gcloud scheduler jobs pause  bawue-scraper-staging --location $REGION
gcloud scheduler jobs resume bawue-scraper-staging --location $REGION
```

## Notes

- **`api-obj-log` is stripped** from the baked config. It writes one JSONL line
  per Vorgang — 306 MB on a full WP18 run — and Cloud Run's filesystem is
  in-memory, so it would consume the task's memory budget for a file that is
  discarded when the task exits. Local Docker Compose runs are unaffected.
- **The Mattermost webhook** is still hardcoded in `config.staging.toml` and gets
  baked into the image. `config.py` has no env override for it — it needs a
  `MATTERMOST_HOOK` config prop, and the current URL should be rotated since the
  repo is public.
- **Production** reuses all of this: copy the workflow, swap `staging` → `prod`
  in `JOB_NAME`, `SECRET_PREFIX` and the environment name, and point
  `CONFIG_FILE` at a production TOML.
