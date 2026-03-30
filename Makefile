VENV := .venv/bin

.PHONY: help install test test-cov test-all test-integration lint lint-fix format run package clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies and vendor collector libs
	python3.14 -m venv .venv
	$(VENV)/pip install --upgrade pip poetry
	mkdir -p vendor
	rsync -a --exclude .git --ignore-existing ../pazufa-collector vendor/
	rsync -a --exclude .git --ignore-existing ../pazufa-collector-core vendor/
	$(VENV)/poetry install

test: ## Run unit tests
	$(VENV)/pytest

test-cov: ## Run tests with coverage
	$(VENV)/pytest --cov=bawue

test-all: ## Run all tests including integration
	$(VENV)/pytest -m ""

test-integration: ## Run integration tests only (requires backend)
	$(VENV)/pytest -m integration

lint: ## Lint source and tests
	$(VENV)/ruff check src/ tests/

lint-fix: ## Lint with auto-fix
	$(VENV)/ruff check --fix src/ tests/

format: ## Format source and tests
	$(VENV)/ruff format src/ tests/

run: ## Run the scraper
	$(VENV)/python -m collector --config-file config.toml

package: install lint format test ## Vendor collector and build Docker image
	docker build -t bawue-scraper .

clean: ## Remove .venv, __pycache__, .pytest_cache, locallogs, and MagicMock
	rm -rf .venv __pycache__ .pytest_cache locallogs MagicMock vendor
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
