VENV := .venv/bin

.PHONY: help install test test-cov test-all test-integration lint lint-fix format run package clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies via Poetry
	poetry install

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

package: ## Vendor collector and build Docker image
	mkdir -p vendor
	cp -r ../pazufa-collector vendor/pazufa-collector
	docker build -t bawue-scraper .

clean: ## Remove .venv, __pycache__, and .pytest_cache
	rm -rf .venv __pycache__ .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
