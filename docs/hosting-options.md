# Hosting the BaWue Scraper

**Decision: GCP Cloud Run Jobs (`europe-west3`) + Upstash Redis (`eu-central-1`),
triggered by Cloud Scheduler, logs to Cloud Logging.**

*Prices checked 2026-09-02. Supersedes the earlier netcup VPS decision.*

## Why

The scraper is a batch job, not a server: `config.staging.toml` already sets `once = true`,
and the intended cadence is twice daily. Paying for a VPS 24/7 to run ~20 minutes a day is
paying for idle. Cloud Run Jobs bill only while a task executes.

It also buys real IaC. netcup has **no server-provisioning API** — their CCP webservice covers
DNS only, so ordering a VPS is a manual browser checkout that Terraform can never reproduce.
GCP has a first-class Terraform provider, so the entire GCP stack — job, scheduler, secrets,
IAM — is declared in the repo and applied from GitHub Actions. The Upstash databases are
created by hand and referenced as secrets.

`cloudbuild.yaml` in the repo root already targets a Cloud Run Job named `bawue-scraper` in
`europe-west3`, so this is a return to a previously scaffolded path, not a greenfield one.

The tradeoff accepted: two vendors instead of one, US-owned hyperscaler, and a variable bill
instead of a fixed one.

## Alternatives considered

| Option | €/mo | Verdict |
|---|---|---|
| **Cloud Run Jobs + Upstash** | **~2–5 variabel** | chosen — IaC end to end, no idle cost |
| netcup VPS 500 G12 | 5,91 | previously chosen; no provisioning API, pays for idle |
| Hetzner CX23 | ~7,13 | good API + Terraform provider, but still a 24/7 box |
| Strato VPS M | 8,00 + 9 setup | existing account, worst value, 12 mo lock-in |
| Contabo | ~4,50 | cheapest RAM, weakest reliability |

## Architecture

```
Cloud Scheduler (0 3,15 * * *)
    → Cloud Run Job `bawue-scraper-{staging,prod}`   2 vCPU / 4 GiB, once = true
        → Upstash Redis (one DB per environment, eu-central-1)
        → PaZuFa backend  (staging.api.pazufa.de / api.pazufa.de)
        → LLM API
    → Cloud Logging  (stdout, no sidecar)
```

4 GiB is the floor: the scraper is capped at 2 GB with `MAX_CONCURRENCY: 3`, and
Tesseract/poppler are memory-hungry. Cloud Run Jobs allow up to 32 GiB and a 24 h task
timeout, so there is headroom.

**Image**: Cloud Run cannot pull from Docker Hub. CI must additionally push to Artifact
Registry (`europe-west3-docker.pkg.dev/...`), or an Artifact Registry remote repository must
mirror the Hub image. The existing Docker Hub publish stays for the Raspberry Pi path.

## Redis — one Upstash DB per environment

Staging and production each get their own Upstash database, created manually in the console.
Full isolation, and no shared command budget. Upstash has **no logical databases**
(`SELECT n` is unsupported — only db 0 exists), so a shared instance would have needed key
prefixes; separate databases make that unnecessary.

One code change is required. `cache.py:31` constructs `redis.Redis(host=..., port=...)` with
no password and no TLS; Upstash requires both. Add a `cache.redis-url` / `REDIS_URL` config
prop and use `redis.Redis.from_url(url, decode_responses=True)`. Keep the existing host/port
props so the local Docker Compose Redis still works unchanged.

Use Upstash's **native TLS endpoint** (`rediss://default:<token>@<endpoint>.upstash.io:6379`),
not the REST endpoint — the Python `redis` client speaks the Redis protocol, not Upstash's
REST API.

Free tier per database: 256 MB, 500 K commands/month. Storage is not the constraint; command
volume is, at ~60 runs/month.

## Cost

Cloud Run tier-1 rates at 2 vCPU / 4 GiB work out to ~$0.21 per execution-hour. At 60 runs
per month:

| Run length | $/mo per environment |
|---|---|
| 10 min | ~2.10 |
| 20 min | ~4.20 |

Upstash free tier: €0. Cloud Logging free tier: 50 GiB/month ingest, 30-day retention, €0.

Cloud Run's monthly free tier (180 K vCPU-s / 360 K GiB-s) would cover roughly the first 25
execution-hours and make this nearly free — **verify it applies to Jobs and not only Services
before relying on it.** The figures above assume it does not.

Actual run duration is the whole cost driver and is not yet measured. Measure it on the first
staging runs before extrapolating to production.

## Logging — Cloud Logging

stdout from a Cloud Run Job is captured natively. No Alloy sidecar, no Grafana Cloud account,
no `logrotate` — this drops an entire moving part versus the VPS design, and 30-day retention
beats Grafana Cloud Free's 14 days.

The entrypoint's `| tee /app/locallogs/stdout.log` becomes redundant on Cloud Run (the
container filesystem is ephemeral) but is harmless and still serves the Docker Compose path.
The `locallogs/*.jsonl` API-object dumps written when `api-obj-log = "locallogs"` **do not
survive** a job execution — set `api-obj-log` to empty in the cloud config, or ship them to
GCS if they are wanted.

## Secrets

Secret Manager, referenced by the job definition — never baked into the image or the
Terraform state as plaintext:

`LTZF_API_KEY` · `LLM_PROVIDER_KEY` · `REDIS_URL` (contains the Upstash token) ·
`MATTERMOST_HOOK`

The Mattermost webhook is currently **hardcoded at `config.staging.toml:57` in a public
repo** and has no env override in `config.py` — it needs a `MATTERMOST_HOOK` prop and
rotation.

## Deployment — gcloud from GitHub Actions

No Terraform. `deploy/gcp/bootstrap.sh` is a one-time script (APIs, Artifact
Registry, service accounts, Secret Manager entries, Workload Identity Federation),
and `.github/workflows/deploy-staging.yml` uses `gcloud` directly — `run jobs
deploy` and `scheduler jobs create/update` are both create-or-update, so no state
file is needed to stay idempotent.

GitHub Actions authenticates via **Workload Identity Federation**, not a
long-lived service-account JSON key. `staging` and `production` are GitHub
Environments so production can require manual approval.

Cloud Run cannot pull from Docker Hub, and it has no bind mounts — so the deploy
workflow rebuilds the released image with the environment's TOML baked in
(`deploy/gcp/Dockerfile`, a two-line layer on top of the CI-built image) and
pushes it to Artifact Registry.

See `deploy/gcp/README.md` for the runbook.

## Deferred

- **Auto-deploy on release.** The staging workflow is `workflow_dispatch` only.
- **Production.** The same workflow with the environment names swapped.
- **`MATTERMOST_HOOK` config prop.** The webhook is config-file-only today, so it
  is baked into the image; it has no env override and the current URL is public.
