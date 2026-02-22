FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy dependency manifests first for layer caching
COPY pyproject.toml poetry.lock* ./

# The collector framework and its openapi-client are expected as local path deps.
# In Docker, we copy them into the build context.
COPY vendor/pazufa-collector/ vendor/pazufa-collector/

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main

# Copy application code
COPY src/ src/
COPY config.toml .

ENTRYPOINT ["python", "-m", "collector", "--config-file", "config.toml"]
