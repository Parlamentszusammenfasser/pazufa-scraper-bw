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
        git \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.2

WORKDIR /app

COPY pyproject.toml poetry.lock poetry.toml ./

# pazufa-corelib is a git-pinned dependency (see pyproject.toml), so poetry
# fetches it over the network at install time — hence `git` in the apt list
# above. No vendored sibling packages are copied any more.
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

# Copy installed Python packages from builder (corelib is a regular installed
# package now — the git dependency is not an editable install, so no vendored
# sources need to be present at runtime).
COPY --from=builder /usr/local/lib/python${PYTHON_MINOR}/site-packages /usr/local/lib/python${PYTHON_MINOR}/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ src/
COPY config.sample.toml config.toml
ENV PYTHONPATH=/app/src

RUN chown -R app:app /app
USER app

ENTRYPOINT ["python", "-m", "bawue", "--config-file", "config.toml", "--once"]
