SIGNER := signer

.DEFAULT_GOAL := help
.PHONY: help setup fmt lint test check py-lint py-test go-lint go-test clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python and dev dependencies, enable git hooks
	uv sync
	uv run pre-commit install

fmt: ## Format and autofix Python and Go
	uv run ruff format .
	uv run ruff check --fix .
	cd $(SIGNER) && golangci-lint fmt

lint: py-lint go-lint ## Lint and type-check both languages (no changes)

test: py-test go-test ## Run Python and Go tests

check: lint test ## Everything CI runs

py-lint: ## Python: format check, ruff, strict mypy
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy

py-test: ## Python: pytest
	uv run pytest

go-lint: ## Go: golangci-lint (incl. gofumpt, goimports)
	cd $(SIGNER) && golangci-lint run ./...

go-test: ## Go: tests with race detector
	cd $(SIGNER) && go test -race ./...

clean: ## Remove caches and build output
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist
