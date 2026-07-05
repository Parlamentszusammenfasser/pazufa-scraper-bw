VENV := .venv/bin
# Bootstrap interpreter for `make install`. Resolve a python3.13 that is NOT the
# one inside .venv: make evaluates this at parse time, so with the venv active
# `command -v` would point into .venv — which `make clean` then deletes, breaking
# `install` on a combined `make clean package`. Strip .venv from PATH first.
PYTHON := $(shell PATH="$$(printf '%s' "$$PATH" | tr ':' '\n' | grep -v "$(CURDIR)/.venv" | paste -sd ':' -)" command -v python3.13 2>/dev/null || echo /opt/homebrew/bin/python3.13)

.PHONY: help install test test-cov test-all test-integration lint lint-fix format run package clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (corelib is fetched from its pinned git tag)
	$(PYTHON) -m venv .venv
	$(VENV)/pip install --upgrade pip poetry
	$(VENV)/poetry lock --check 2>/dev/null || $(VENV)/poetry lock --regenerate
	$(VENV)/poetry install

test: ## Run unit tests
	$(VENV)/pytest

test-cov: ## Run tests with coverage
	$(VENV)/pytest --cov=bawue

test-all: ## Run all tests including integration
	$(VENV)/pytest -m ""

test-integration: ## Run integration tests only (requires backend)
	$(VENV)/pytest -m integration

audit: ## Scan dependencies for known vulnerabilities (SCA)
	$(VENV)/pip-audit

lint: ## Lint source and tests
	$(VENV)/ruff check src/ tests/

lint-fix: ## Lint with auto-fix
	$(VENV)/ruff check --fix src/ tests/

format: ## Format source and tests
	$(VENV)/ruff format src/ tests/

run: ## Run the scraper
	$(VENV)/python -m bawue --config-file config.toml

package: install lint format test ## Build the Docker image
	docker build -t bawue-scraper .

compare-llm: ## Compare OpenAI vs Ollama output on sample documents
	$(VENV)/python scripts/compare_llm_providers.py

clean: ## Remove .venv, __pycache__, .pytest_cache, locallogs, and MagicMock
	rm -rf .venv __pycache__ .pytest_cache .kreuzberg locallogs MagicMock
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
