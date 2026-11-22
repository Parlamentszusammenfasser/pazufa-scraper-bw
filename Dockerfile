# ---- Builder stage ----
FROM python:3.14.10-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS builder

# build-essential provides cc/gcc as a fallback for native deps that ship no
# wheel for this Python version (currently none do).
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
FROM python:3.14.10-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

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
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ src/
COPY config.sample.toml config.toml
ENV PYTHONPATH=/app/src

RUN chown -R app:app /app
USER app

# Logged at startup (issue #84); CI passes the release or the sha-<sha7> tag. Set last so a
# new version doesn't invalidate the layers above.
ARG SCRAPER_VERSION=dev
ENV SCRAPER_VERSION=$SCRAPER_VERSION

ENTRYPOINT ["python", "-m", "bawue", "--config-file", "config.toml"]
