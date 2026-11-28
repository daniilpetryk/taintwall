SIGNER    := signer
# Tool versions live where Dependabot can update them:
#   hadolint, grype -> docker/tools.Dockerfile   (docker ecosystem)
#   govulncheck     -> tools/go.mod tool directive (gomod ecosystem)
#   pip-audit       -> dev dependency in pyproject (uv ecosystem)
tool_image = $(shell sed -n 's/^FROM \(.*\) AS $(1)$$/\1/p' docker/tools.Dockerfile)
HADOLINT  := $(call tool_image,hadolint)
GRYPE     := $(call tool_image,grype)

.DEFAULT_GOAL := help
.PHONY: help setup fmt lint test check cov diff-cov mutate bench-baseline bench-plans bench-census bench-check bench-craibench bench-test audit hooks \
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

# The benchmark projects keep their virtualenvs outside the repo: REUSE and
# other repo-wide scanners would otherwise read thousands of vendored files.
BENCH_CACHE ?= $(HOME)/.cache/taintwall

bench-baseline: ## AgentDojo banking baseline (spends OpenRouter credit; ATTACK, DRAWS, CACHE, MAX_COST)
	cd bench/agentdojo && UV_PROJECT_ENVIRONMENT=$(BENCH_CACHE)/bench-agentdojo-venv uv run python baseline.py \
		--mode all --attack $(or $(ATTACK),important_instructions) --draws $(or $(DRAWS),1) $(if $(CACHE),--cache $(CACHE)) \
		--max-cost $(or $(MAX_COST),1.0) --run-name $(or $(RUN),banking-haiku-5.5)

bench-plans: ## Stage 2 study: plans planners write for banking (spends credit; MODELS, RULES=1, MAX_COST)
	cd bench/agentdojo && UV_PROJECT_ENVIRONMENT=$(BENCH_CACHE)/bench-agentdojo-venv uv run python plans.py \
		--models $(or $(MODELS),anthropic/claude-haiku-5.5) --max-cost $(or $(MAX_COST),0.2) --run-name $(or $(RUN),natural) $(if $(RULES),--rules)

bench-planner: ## Stage 2: AgentDojo banking through the planner, no policies (spends credit; MODE, ATTACK, REPLAN, DRAWS, MAX_COST)
	cd bench/agentdojo && UV_PROJECT_ENVIRONMENT=$(BENCH_CACHE)/bench-agentdojo-venv uv run python planner_run.py \
		--mode $(or $(MODE),all) --attack $(or $(ATTACK),important_instructions) --replan $(or $(REPLAN),2 1) \
		--max-cost $(or $(MAX_COST),1.0) --draws $(or $(DRAWS),1) --run-name $(or $(RUN),planner-haiku-5.5)

bench-census: ## Constructs used and rejection reasons of collected plans (no model calls; RUN)
	cd bench/agentdojo && UV_PROJECT_ENVIRONMENT=$(BENCH_CACHE)/bench-agentdojo-venv uv run python plan_census.py \
		../results/agentdojo/plans/$(or $(RUN),natural)

bench-check: ## Static checker over collected plans (no model calls; RUN)
	cd bench/agentdojo && UV_PROJECT_ENVIRONMENT=$(BENCH_CACHE)/bench-agentdojo-venv uv run python plan_check.py \
		../results/agentdojo/plans/$(or $(RUN),natural)

bench-craibench: ## CrAIBench crypto suites (spends credit; MODE, SAMPLE, MEMORY=1, MAX_COST)
	cd bench/craibench && UV_PROJECT_ENVIRONMENT=$(BENCH_CACHE)/bench-craibench-venv uv run python baseline.py \
		--mode $(or $(MODE),utility) $(if $(SAMPLE),--sample $(SAMPLE)) $(if $(MEMORY),--memory-injection) \
		--max-cost $(or $(MAX_COST),0.5) --run-name $(or $(RUN),craibench-haiku-5.5)

bench-test: ## Types and tests of every benchmark project (no model calls), as CI's bench job
	@for project in common agentdojo craibench; do \
		echo "== bench/$$project"; \
		(cd bench/$$project && export UV_PROJECT_ENVIRONMENT=$(BENCH_CACHE)/bench-$$project-venv && \
			uv run --locked mypy --strict . && uv run --locked pytest -q) || exit 1; \
	done

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
