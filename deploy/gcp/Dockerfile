# Bakes an environment's TOML config into the released scraper image.
#
# Cloud Run has no bind mounts (Docker Compose mounts config.staging.toml over
# /app/config.toml), and the [bawue] / [beteiligung] / [gesetzblatt] /
# [notifications] sections are read straight off disk by config_loader.py with no
# env-var overrides — so the file has to be inside the image.
#
# The base is the exact multi-arch image CI already built, linted and tested; this
# only adds a config layer. Build context is the repo root.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG CONFIG_FILE=config.staging.toml
COPY --chown=app:app ${CONFIG_FILE} /app/config.toml

# api-obj-log writes one JSONL line per Vorgang — 306 MB on a full WP18 staging
# run. Cloud Run's filesystem is in-memory and counts against the task's memory
# limit, and the file is discarded when the task exits. Stripping it here keeps a
# single config.staging.toml in the repo instead of a near-duplicate cloud copy.
RUN sed -i '/^api-obj-log/d' /app/config.toml

# Base ENTRYPOINT already runs: python -m bawue --config-file config.toml --once
