# ---- Builder stage ----
FROM python:3.12.9-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.1.1

WORKDIR /app

COPY pyproject.toml poetry.lock ./
COPY vendor/pazufa-collector/ vendor/pazufa-collector/

# Rewrite local path dependencies from ../pazufa-collector to vendor/pazufa-collector
RUN sed -i 's|path = "\.\./pazufa-collector"|path = "vendor/pazufa-collector"|' pyproject.toml \
    && sed -i 's|path = "\.\./pazufa-collector/oapicode"|path = "vendor/pazufa-collector/oapicode"|' pyproject.toml \
    && sed -i 's|url = "\.\./pazufa-collector"|url = "vendor/pazufa-collector"|' poetry.lock \
    && sed -i 's|url = "\.\./pazufa-collector/oapicode"|url = "vendor/pazufa-collector/oapicode"|' poetry.lock

RUN poetry config virtualenvs.create false \
    && poetry lock --regenerate \
    && poetry install --no-interaction --no-ansi --only main --no-root

# ---- Runtime stage ----
FROM python:3.12.9-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ src/
COPY config.sample.toml config.toml

RUN chown -R app:app /app
USER app

ENTRYPOINT ["python", "-m", "collector", "--config-file", "config.toml", "--once"]
