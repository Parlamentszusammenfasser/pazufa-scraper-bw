ARG PYTHON_VERSION=3.13.3
ARG PYTHON_MINOR=3.13

# ---- Builder stage ----
FROM python:${PYTHON_VERSION}-slim AS builder

# build-essential provides cc/gcc for building native deps (e.g. python-bidi
# has no cp314 wheel yet, so it is compiled from Rust source via maturin).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.2

WORKDIR /app

COPY pyproject.toml poetry.lock poetry.toml ./

# Place vendor packages at the sibling paths pyproject.toml already references:
#   ../pazufa-collector     →  /pazufa-collector     (from WORKDIR /app)
#   ../pazufa-scraper-core  →  /pazufa-scraper-core
#
# This avoids modifying pyproject.toml/poetry.lock, which would invalidate the
# content-hash and force poetry to re-solve — which fails because
# pazufa-collector's own pyproject.toml uses a PEP 508 "openapi_client @ oapicode"
# dep that poetry lock --regenerate cannot reconcile with our direct dep on it.
COPY vendor/pazufa-collector/ /pazufa-collector/
COPY vendor/pazufa-scraper-core/ /pazufa-scraper-core/

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main --no-root

# ---- Runtime stage ----
ARG PYTHON_VERSION
ARG PYTHON_MINOR
FROM python:${PYTHON_VERSION}-slim
ARG PYTHON_MINOR

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python${PYTHON_MINOR}/site-packages /usr/local/lib/python${PYTHON_MINOR}/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Vendor sources must be present at the same paths because pyproject.toml uses
# develop = true (editable installs), so .pth files in site-packages point here.
COPY --from=builder /pazufa-collector /pazufa-collector
COPY --from=builder /pazufa-scraper-core /pazufa-scraper-core

COPY src/ src/
COPY config.sample.toml config.toml
ENV PYTHONPATH=/app/src

RUN chown -R app:app /app
USER app

ENTRYPOINT ["python", "-m", "collector", "--config-file", "config.toml", "--once"]
