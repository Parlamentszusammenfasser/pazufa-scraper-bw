# ---- Builder stage ----
FROM python:3.14.3-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.2

WORKDIR /app

COPY pyproject.toml poetry.lock ./
COPY vendor/pazufa-collector/ vendor/pazufa-collector/
COPY vendor/pazufa-collector-core/ vendor/pazufa-collector-core/

# Rewrite local path dependencies from ../ to vendor/
RUN sed -i 's|path = "\.\./pazufa-collector", develop = true|path = "vendor/pazufa-collector"|' pyproject.toml \
    && sed -i 's|path = "\.\./pazufa-collector-core", develop = true|path = "vendor/pazufa-collector-core"|' pyproject.toml \
    && sed -i 's|path = "\.\./pazufa-collector/oapicode", develop = true|path = "vendor/pazufa-collector/oapicode"|' pyproject.toml \
    && sed -i 's|url = "\.\./pazufa-collector"|url = "vendor/pazufa-collector"|' poetry.lock \
    && sed -i 's|url = "\.\./pazufa-collector-core"|url = "vendor/pazufa-collector-core"|' poetry.lock \
    && sed -i 's|url = "\.\./pazufa-collector/oapicode"|url = "vendor/pazufa-collector/oapicode"|' poetry.lock

RUN poetry config virtualenvs.create false \
    && poetry lock --regenerate \
    && poetry install --no-interaction --no-ansi --only main --no-root

# ---- Runtime stage ----
FROM python:3.14.3-slim

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
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ src/
COPY config.sample.toml config.toml
ENV PYTHONPATH=/app/src

RUN chown -R app:app /app
USER app

ENTRYPOINT ["python", "-m", "collector", "--config-file", "config.toml", "--once"]
