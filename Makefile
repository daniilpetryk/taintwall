SIGNER    := signer
# Tool versions live where Dependabot can update them:
#   hadolint, grype -> docker/tools.Dockerfile   (docker ecosystem)
#   govulncheck     -> tools/go.mod tool directive (gomod ecosystem)
#   pip-audit       -> dev dependency in pyproject (uv ecosystem)
tool_image = $(shell sed -n 's/^FROM \(.*\) AS $(1)$$/\1/p' docker/tools.Dockerfile)
HADOLINT  := $(call tool_image,hadolint)
GRYPE     := $(call tool_image,grype)

.DEFAULT_GOAL := help
.PHONY: help setup fmt lint test check cov diff-cov mutate audit hooks \
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

diff-cov: ## Changed lines vs origin/dev must be 100% covered (as CI does)
	git fetch -q origin dev
	uv run pytest -q --cov --cov-report=xml
	uv run diff-cover coverage.xml --compare-branch=origin/dev --fail-under=100

mutate: ## Mutation testing of core (slow; CI runs it nightly)
	uv run mutmut run
	uv run python scripts/mutation_score.py

audit: ## Known-vulnerability scan of locked Python and Go dependencies
	uv export --frozen --all-groups --no-emit-project --format requirements-txt \
		| uv run pip-audit --strict --disable-pip --require-hashes -r /dev/stdin
	go -C tools tool govulncheck -C ../$(SIGNER) ./...

hooks: ## Run every pre-commit hook on all files
	uv run pre-commit run --all-files

# --- per language ---------------------------------------------------------------

py-lint: ## Python: format, ruff (ALL), mypy + pyrefly, import contracts, deptry, duplicates
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pyrefly check
	uv run lint-imports
	uv run deptry src
	uv run pylint src

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
