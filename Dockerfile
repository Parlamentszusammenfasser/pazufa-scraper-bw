ARG PYTHON_VERSION=3.13.3
ARG PYTHON_MINOR=3.13

# ---- Builder stage ----
FROM python:${PYTHON_VERSION}-slim AS builder

# build-essential provides cc/gcc for building native deps (e.g. python-bidi
# has no cp314 wheel yet, so it is compiled from Rust source via maturin).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.2

WORKDIR /app

COPY pyproject.toml poetry.lock poetry.toml ./
COPY vendor/pazufa-collector/ vendor/pazufa-collector/
COPY vendor/pazufa-scraper-core/ vendor/pazufa-scraper-core/

# Rewrite local path dependencies from ../ to vendor/
RUN sed -i 's|path = "\.\./pazufa-collector", develop = true|path = "vendor/pazufa-collector"|' pyproject.toml \
    && sed -i 's|path = "\.\./pazufa-scraper-core", develop = true|path = "vendor/pazufa-scraper-core"|' pyproject.toml \
    && sed -i 's|path = "\.\./pazufa-collector/oapicode", develop = true|path = "vendor/pazufa-collector/oapicode"|' pyproject.toml \
    && sed -i 's|url = "\.\./pazufa-collector"|url = "vendor/pazufa-collector"|' poetry.lock \
    && sed -i 's|url = "\.\./pazufa-scraper-core"|url = "vendor/pazufa-scraper-core"|' poetry.lock \
    && sed -i 's|url = "\.\./pazufa-collector/oapicode"|url = "vendor/pazufa-collector/oapicode"|' poetry.lock

RUN poetry config virtualenvs.create false \
    && poetry lock --regenerate \
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

COPY src/ src/
COPY config.sample.toml config.toml
ENV PYTHONPATH=/app/src

RUN chown -R app:app /app
USER app

ENTRYPOINT ["python", "-m", "collector", "--config-file", "config.toml", "--once"]
