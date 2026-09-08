# Hosting the BaWue Scraper

**Decision: GCP Cloud Run Jobs (`europe-west3`) + Upstash Redis (`eu-central-1`),
triggered daily by Cloud Scheduler, logs to Cloud Logging.**

*Prices checked 2026-09-02. Supersedes the earlier netcup VPS decision.*
Runbook: [`deploy/gcp/README.md`](../deploy/gcp/README.md).

## Why

The scraper is a batch job, not a server: it runs with `--once` and the intended
cadence is daily. Paying for a VPS 24/7 to run ~20 minutes a day is paying for idle.
Cloud Run Jobs bill only while a task executes.

The runner-up was a VPS. netcup has **no server-provisioning API** — their CCP
webservice covers DNS only, so ordering a VPS is a manual browser checkout that no
tooling can reproduce. On GCP the whole stack is a `gcloud` script in the repo
(`deploy/gcp/bootstrap.sh`) plus a workflow, both idempotent. The Upstash databases
are created by hand and referenced as secrets.

The tradeoff accepted: two vendors instead of one, a US-owned hyperscaler, and a
variable bill instead of a fixed one.

## Alternatives considered

| Option | €/mo | Verdict |
|---|---|---|
| **Cloud Run Jobs + Upstash** | **~0–2 variabel** | chosen — scriptable end to end, no idle cost |
| netcup VPS 500 G12 | 5,91 | previously chosen; no provisioning API, pays for idle |
| Hetzner CX23 | ~7,13 | good API + Terraform provider, but still a 24/7 box |
| Strato VPS M | 8,00 + 9 setup | existing account, worst value, 12 mo lock-in |
| Contabo | ~4,50 | cheapest RAM, weakest reliability |

## Architecture

```
Cloud Scheduler (0 3 * * *)
    → Cloud Run Job `bawue-scraper-{staging,prod}`   2 vCPU / 4 GiB, --once
        → Upstash Redis (one DB per environment, eu-central-1)
        → PaZuFa backend  (staging.api.pazufa.de / api.pazufa.de)
        → LLM API
    → Cloud Logging  (stdout, no sidecar)
```

4 GiB is the floor: the scraper is capped at 2 GB with `MAX_CONCURRENCY: 3`, and
Tesseract/poppler are memory-hungry. Cloud Run Jobs allow up to 32 GiB and a 168 h
task timeout, so there is headroom.

**Image**: Cloud Run pulls the multi-arch image `ci.yml` already publishes to Docker
Hub — no Artifact Registry, no cloud-specific rebuild. Google recommends an Artifact
Registry remote repository in front of Docker Hub for higher availability; that is a
one-line change to `--image` if Hub availability ever bites.

**Config**: the image ships `config.sample.toml` as its `config.toml`, and everything
environment-specific is an env var on the job. Cloud Run has no bind mounts, so the
alternative would have been baking a per-environment TOML into a derived image — a
second artifact to build, scan and keep in sync, for four scalar values.

## Redis — one Upstash DB per environment

Staging and production each get their own Upstash database, created manually in the
console. Full isolation, and no shared command budget. Upstash has **no logical
databases** (`SELECT n` is unsupported — only db 0 exists), so a shared instance would
have needed key prefixes; separate databases make that unnecessary.

Use Upstash's **native TLS endpoint** (`rediss://default:<token>@<endpoint>.upstash.io:6379`),
not the REST endpoint — the Python `redis` client speaks the Redis protocol. The
`cache.redis-url` / `REDIS_URL` config prop exists for exactly this: host/port carries
neither TLS nor auth, so the URL form is required, and it takes precedence when set.

Free tier per database: 256 MB storage, 500 K commands/month, **10 GB/month egress**.
Storage is not the constraint. Command volume is not either at ~30 runs/month — but
the pipeline issues a `GET` per listed item on every run, cached ones included, so
egress is the one to watch. Measure it on the first staging runs.

## Cost

`europe-west3` is a **Tier 2** region, and Cloud Run Jobs bill **instance-based**
(all jobs do, unlike services). The instance-based free tier — 240 K vCPU-seconds and
450 K GiB-seconds per month — is the number that matters here.

At 2 vCPU / 4 GiB, 30 runs/month:

| Run length | vCPU-s | GiB-s | vs. free tier |
|---|---|---|---|
| 10 min | 36 K | 72 K | ~16 % |
| 20 min | 72 K | 144 K | ~32 % |

So staging alone is **€0**. Two caveats before treating that as permanent:

- The free tier is per **billing account**, aggregated across projects. Staging plus
  production roughly doubles the usage — still inside the cap at these run lengths,
  but not with much room if runs get longer.
- The free tier is applied as a discount computed at **Tier 1** rates, so a Tier 2
  region owes the delta. Confirm the Tier 2 rate in the console before writing a
  figure down.

Upstash free tier: €0. Cloud Logging free tier: 50 GiB/month ingest, 30-day
retention, €0.

Actual run duration is the whole cost driver and is not yet measured — the deploy
workflow's smoke test executes the job once and reports it.

## Logging — Cloud Logging

stdout from a Cloud Run Job is captured natively. No Alloy sidecar, no Grafana Cloud
account, no `logrotate` — this drops an entire moving part versus the VPS design, and
30-day retention beats Grafana Cloud Free's 14 days.

The `locallogs/*.jsonl` API-object dumps are off unless `api-obj-log` is configured,
and the job does not set it: the container filesystem is in-memory, so those writes
would consume the task's own memory budget for a file discarded when it exits.

## Secrets

Secret Manager, referenced by name from the job definition — never baked into an
image and never copied through CI:

`LTZF_API_KEY` · `LLM_PROVIDER_KEY` · `REDIS_URL` (contains the Upstash token) ·
`MATTERMOST_HOOK`

Each is readable only by the runtime service account, granted per secret rather than
project-wide. GitHub Actions never sees a value, so rotation is a
`gcloud secrets versions add` that takes effect on the next run without a redeploy.

> **The Mattermost webhook committed at `config.sample.toml:73` was published to a
> public repo and baked into every Docker Hub image. It has been replaced with a
> placeholder here — the URL itself still needs rotating.**

## Deferred

- **Auto-deploy on release.** The staging workflow is `workflow_dispatch` only.
- **Production.** The same workflow with the environment names swapped.
- **Artifact Registry remote repository** in front of Docker Hub, if Hub
  availability or rate limits ever affect a run.
