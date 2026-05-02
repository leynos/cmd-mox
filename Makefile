NIXIE ?= nixie
MDFORMAT_ALL ?= mdformat-all
UV ?= $(shell command -v uv 2>/dev/null || printf '%s' "$$HOME/.local/bin/uv")
TOOLS = $(MDFORMAT_ALL) $(NIXIE) $(UV)
VENV_TOOLS = pytest
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
RUFF = $(UV_ENV) $(UV) run ruff
TY = $(UV_ENV) $(UV) run ty
WINDOWS_SMOKE_ARGS = tests/test_windows_environment.py \
	tests/test_windows_support_bdd.py \
	--log-file=windows-ipc.log \
	--log-file-level=DEBUG \
	--log-file-format="%(asctime)s %(levelname)s [%(name)s] %(message)s"

.PHONY: help all clean build build-release lint fmt check-fmt
.PHONY: markdownlint markdownlint-run nixie test typecheck
.PHONY: $(TOOLS) $(VENV_TOOLS)

.DEFAULT_GOAL := all

all: build check-fmt test typecheck

.venv: pyproject.toml
	$(UV_ENV) $(UV) venv --clear

build: $(UV) .venv ## Build virtual-env and install deps
	$(UV_ENV) $(UV) sync --group dev

build-release: ## Build artefacts (sdist & wheel)
	python -m build --sdist --wheel

clean: ## Remove build artifacts
	rm -rf build dist *.egg-info \
	  .mypy_cache .pytest_cache .coverage coverage.* \
	  lcov.info htmlcov .venv
	find . -type d -name '__pycache__' -print0 | xargs -0 -r rm -rf

define ensure_tool
	@command -v $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required, but not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

define ensure_tool_venv
	$(UV_ENV) $(UV) run which $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required in the virtualenv, but is not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

ifneq ($(strip $(TOOLS)),)
$(TOOLS): ## Verify required CLI tools
	$(call ensure_tool,$@)
endif


ifneq ($(strip $(VENV_TOOLS)),)
.PHONY: $(VENV_TOOLS)
$(VENV_TOOLS): ## Verify required CLI tools in venv
	$(call ensure_tool_venv,$@)
endif

fmt: build ## Format sources
	$(RUFF) format
	$(RUFF) check --select I --fix
	$(MAKE) markdownlint-run MDARGS="--fix"

check-fmt: build ## Verify formatting
	$(RUFF) format --check
	$(MAKE) markdownlint-run

markdownlint-run: ## Run markdownlint-cli2 with pinned fallback
	@if command -v markdownlint-cli2 >/dev/null 2>&1; then \
	  markdownlint-cli2 $(MDARGS) '**/*.md'; \
	else \
	  npx --yes markdownlint-cli2@0.22.1 $(MDARGS) '**/*.md'; \
	fi

lint: build ## Run linters
	$(RUFF) check

typecheck: build ## Run typechecking
	$(TY) --version
	$(TY) check

markdownlint: ## Lint Markdown files
	$(MAKE) markdownlint-run

nixie: $(NIXIE) ## Validate Mermaid diagrams
	$(NIXIE) --no-sandbox

test: build $(UV) $(VENV_TOOLS) ## Run tests
	$(UV_ENV) $(UV) run pytest -v -n auto

windows-smoke: build $(UV) $(VENV_TOOLS) ## Run Windows smoke workflow and capture IPC logs
	$(UV_ENV) $(UV) run pytest -v $(WINDOWS_SMOKE_ARGS)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
