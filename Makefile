SIGNER    := signer
HADOLINT  := hadolint/hadolint:v2.14.0@sha256:27086352fd5e1907ea2b934eb1023f217c5ae087992eb59fde121dce9c9ff21e
GRYPE     := anchore/grype:v0.122.0@sha256:013e16ae96bd64108973e3e9fecec63f7ff276358d34c46c25d33e2aac96539f
GOVULN    := golang.org/x/vuln/cmd/govulncheck@v1.9.0
PIP_AUDIT := pip-audit==2.10.1

.DEFAULT_GOAL := help
.PHONY: help setup fmt lint test check cov audit hooks \
        py-lint py-test go-lint go-test lint-docker \
        docker-build docker-check scan scan-images clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

# --- everyday -----------------------------------------------------------------

setup: ## Install Python and dev dependencies, enable git hooks
	uv sync
	uv run pre-commit install

fmt: ## Format and autofix Python and Go
	uv run ruff format .
	uv run ruff check --fix .
	cd $(SIGNER) && golangci-lint fmt

lint: py-lint go-lint ## Lint and type-check both languages (no changes)

test: py-test go-test ## Run Python and Go tests

check: lint test ## Everything CI runs on code

cov: ## Python tests with coverage report
	uv run pytest --cov --cov-report=term

audit: ## Known-vulnerability scan of locked Python and Go dependencies
	uv export --frozen --all-groups --no-emit-project --format requirements-txt \
		| uvx $(PIP_AUDIT) --strict --disable-pip --require-hashes -r /dev/stdin
	cd $(SIGNER) && go run $(GOVULN) ./...

hooks: ## Run every pre-commit hook on all files
	uv run pre-commit run --all-files

# --- per language ---------------------------------------------------------------

py-lint: ## Python: format, ruff (ALL), mypy + pyrefly, import contracts, deptry
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pyrefly check
	uv run lint-imports
	uv run deptry src

py-test: ## Python: pytest
	uv run pytest

go-lint: ## Go: go.mod tidy check, golangci-lint (78 linters + formatters)
	cd $(SIGNER) && go mod tidy -diff
	cd $(SIGNER) && golangci-lint run ./...

go-test: ## Go: tests with race detector
	cd $(SIGNER) && go test -race ./...

# --- docker -------------------------------------------------------------------------

lint-docker: ## Lint Dockerfiles: hadolint plus BuildKit build checks
	@for f in docker/*.Dockerfile; do \
		echo "hadolint $$f"; \
		docker run --rm -i -v "$(CURDIR)/.hadolint.yaml:/.config/hadolint.yaml:ro" $(HADOLINT) < $$f || exit 1; \
		echo "build --check $$f"; \
		docker build --quiet --check -f $$f . || exit 1; \
	done

docker-build: ## Build signer, app and dev images
	docker compose build signer app dev

docker-check: ## Run `make check` inside the dev container as the host user
	docker compose run --rm --user "$$(id -u):$$(id -g)" dev sh -c 'uv sync --quiet && make check'

scan: docker-build scan-images ## Build, then scan images for fixable High/Critical CVEs (Grype)

scan-images: ## Scan already-built signer and app images (used by CI)
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	for img in taintwall-signer taintwall-app; do \
		echo "grype $$img"; \
		docker save "$${img}:latest" -o "$$tmp/$$img.tar" && \
		docker run --rm -v "$$tmp:/scan:ro" -v "$(CURDIR)/.grype.yaml:/.grype.yaml:ro" \
			$(GRYPE) "docker-archive:/scan/$$img.tar" --config /.grype.yaml || exit 1; \
	done

clean: ## Remove caches and build output
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist
