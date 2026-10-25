NIXIE ?= nixie
# Every uv call goes through the vendored helper, which cleans the environment,
# uses the global uv cache, runs offline first and goes online at most once. See
# https://github.com/leynos/shared-actions/tree/main/uv_gate for the contract.
UV_GATE ?= python3 scripts/uv_gate.py
PROJECT_ROOT := $(abspath $(dir $(firstword $(MAKEFILE_LIST))))
# The helper asks uv for its cache directory and takes the answer verbatim, so a
# colourized answer (uv wraps it when FORCE_COLOR is set) would become a cache
# path containing ANSI escapes and every later uv call would miss. Keep uv's
# output plain for all recipes, whatever the caller's terminal advertises.
export NO_COLOR := 1
UV ?= $(shell command -v uv 2>/dev/null || printf '%s' "$$HOME/.local/bin/uv")
TOOLS = $(UV)
# The helper finds uv on PATH, so put the directory of the selected uv (a caller's
# UV=..., or the fallback in $$HOME/.local/bin) on PATH for every recipe.
export PATH := $(patsubst %/,%,$(dir $(UV))):$(PATH)
VENV_TOOLS = pytest
MAKEUTIL = makeutil
# The CV-005 CodeScene contracts live in shared-actions and run from a full
# commit, so a fix is a pin bump. `.github/cv005.toml` holds this repository's
# only parameters.
CV005_CONTRACTS_REF ?= a38feb9be25755c30eca5bda96bd3786a5b89c6b
CV005_CONTRACTS = $(UV_GATE) tool --python 3.13 \
	--from 'git+https://github.com/leynos/shared-actions@$(CV005_CONTRACTS_REF)\#subdirectory=packages/cv005-contracts' \
	-- cv005-contracts
RUFF_VERSION ?= 0.16.4
TY_VERSION ?= 0.0.74
RUFF = $(UV_GATE) tool --from ruff@$(RUFF_VERSION) -- ruff
# ty runs as a pinned tool and reads the project's packages from the prepared
# virtualenv through --python, since an isolated tool environment would not see
# them.
TY = $(UV_GATE) tool --from ty@$(TY_VERSION) -- ty
TYPOS_VERSION ?= 1.48.0
TYPOS := $(UV_GATE) tool --from typos@$(TYPOS_VERSION) -- typos
PYLINT_PYTHON ?= pypy@3.12
PYLINT_VERSION ?= 4.0.9
PYLINT_TARGETS ?= cmd_mox conftest.py examples tests
# too-many-lines is enforced by the df12 tier at its 800-line review ceiling
# (pylintrc-df12.toml); too-many-arguments/too-many-positional-arguments
# remain for pytest-bdd step wrappers, mirroring the Ruff per-file ignores
# for tests/steps.
PYLINT_BASELINE_DISABLE = too-many-lines,too-many-arguments,too-many-positional-arguments
PYLINT = $(UV_GATE) tool --managed-python --python $(PYLINT_PYTHON) --from 'pylint==$(PYLINT_VERSION)' -- pylint --disable=$(PYLINT_BASELINE_DISABLE)
DF12_PYTHON ?= 3.14
# Commit SHA of the df12-python-lints v0.3.0 tag.
DF12_PYTHON_LINTS_REF ?= 4cf41736cce2f7ba2778882a5c629c044568a0e5
DF12_PYTHON_LINTS = git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)
DF12_PYLINT = UV_PYTHON_PREFERENCE=only-managed $(UV_GATE) tool --python $(DF12_PYTHON) --from '$(DF12_PYTHON_LINTS)' -- pylint
AMBRLEAKS = UV_PYTHON_PREFERENCE=only-managed $(UV_GATE) tool --python $(DF12_PYTHON) --from '$(DF12_PYTHON_LINTS)' -- ambrleaks
SKYLOS_VERSION = 4.33.2
# Skylos parses source using its own Python AST. Python 3.14 avoids phantom
# dead-code findings from newer syntax that older tool runtimes cannot parse.
SKYLOS_CLI = $(UV_GATE) tool --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' -- skylos
SKYLOS = $(SKYLOS_CLI) --config-file pyproject.toml
SKYLOS_PRODUCTION_TARGETS ?= cmd_mox
SKYLOS_EXCLUDE_FOLDERS ?= tests cmd_mox/unittests
SKYLOS_EXCLUDE_ARGS = $(foreach folder,$(SKYLOS_EXCLUDE_FOLDERS),--exclude $(folder))
SKYLOS_WHITELIST_LOCK ?= .skylos-whitelist.lock
# Keep this pin in sync with the CI install step and [tool.nose] in
# pyproject.toml; tests/test_toolchain_contract.py enforces the match.
NOSE_VERSION ?= 0.20.0
NOSE_TOOLS_DIR ?= .tools/nose
NOSE_BIN ?= $(NOSE_TOOLS_DIR)/nose
CARGO_BINSTALL ?= cargo-binstall
# The gate is PEP 723 tooling with its own dependencies and a CPython 3.14
# floor, so it needs a tool environment rather than the application
# virtualenv. It goes through `uv_gate tool` and not `uv_gate run` for two
# reasons: `run` always adds `--frozen`, which a PEP 723 script cannot satisfy
# without a committed script lockfile, and `run` never goes online, so a cold
# cache could never bootstrap the environment at all. `tool` warms once on a
# proven cache miss and refuses an unpinned spec, which is also why the
# interpreter is requested as `python@<version>` rather than a bare `--python`.
# tests/test_toolchain_contract.py keeps these pins equal to the script's own
# PEP 723 block, which stays the authored source for direct `uv run` use.
DUPLICATION_PYTHON ?= 3.14
CYCLOPTS_VERSION ?= 4.25.2
TOMLKIT_VERSION ?= 0.15.1
DUPLICATION_TEST_PYTEST ?= 9.0.2
DUPLICATION_TEST_HYPOTHESIS ?= 6.165.6
# A relative NOSE_BIN is resolved against the repository root by the gate.
DUPLICATION_GATE = NOSE_BIN=$(NOSE_BIN) $(UV_GATE) tool \
	--from 'python@$(DUPLICATION_PYTHON)' \
	--with cyclopts==$(CYCLOPTS_VERSION) --with tomlkit==$(TOMLKIT_VERSION) \
	-- python scripts/duplication_gate.py
DUPLICATION_TEST_PYTHON ?= $(DUPLICATION_PYTHON)
DUPLICATION_TEST_PYTEST ?= 9.0.2
DUPLICATION_TEST_HYPOTHESIS ?= 6.165.6
WINDOWS_SMOKE_ARGS = tests/test_windows_environment.py \
	tests/test_windows_support_bdd.py \
	tests/test_named_pipe_server.py \
	--log-file=windows-ipc.log \
	--log-file-level=DEBUG \
	--log-file-format="%(asctime)s %(levelname)s [%(name)s] %(message)s"

.PHONY: help all clean build prepare build-release lint fmt check-fmt
.PHONY: makeutil markdownlint markdownlint-run nixie spelling skylos-allow test test-workflow-contracts typecheck
.PHONY: install-nose duplication duplication-test duplication-allow
.PHONY: $(TOOLS) $(VENV_TOOLS)

.DEFAULT_GOAL := all

# `make fmt` and `make check-fmt` call mdtablefix directly. `--git` selects the
# Markdown files Git tracks and `--include-untracked` adds the untracked files
# Git does not ignore, so a new document is formatted before it is staged.
# Both modes need mdtablefix 0.6.0 or later; CI pins the version at the
# install-mdtablefix step.
MDTABLEFIX ?= mdtablefix
MDLINT ?= $(shell command -v markdownlint-cli2 2>/dev/null || printf '%s' "$$HOME/.bun/bin/markdownlint-cli2")
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences

all: build check-fmt test typecheck

prepare: $(UV) ## Install the locked development environment, offline first
	$(UV_GATE) prepare --group dev

build: prepare ## Build virtual-env and install deps

build-release: ## Build artefacts (sdist & wheel)
	uv build --out-dir dist

clean: ## Remove build artifacts
	rm -rf build dist *.egg-info \
	  .mypy_cache .pytest_cache .coverage coverage.* \
	  lcov.info htmlcov .venv
	rm -f .typos-oxendict-base.json .typos-oxendict-base.toml
	find . -type d -name '__pycache__' -print0 | xargs -0 -r rm -rf

define ensure_tool
	@command -v $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required, but not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

define ensure_tool_venv
	$(UV_GATE) run --group dev -- which $(1) >/dev/null 2>&1 || { \
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
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)
	$(MDLINT) --fix "**/*.md"

check-fmt: build ## Verify formatting
	$(RUFF) format --check
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

markdownlint-run: ## Run markdownlint-cli2 over the repository's Markdown
	$(MDLINT) "**/*.md"

lint: build install-nose ## Run linters
	$(RUFF) check
	$(PYLINT) $(PYLINT_TARGETS)
	$(DF12_PYLINT) --rcfile=pylintrc-df12.toml $(PYLINT_TARGETS)
	$(AMBRLEAKS) tests
	$(SKYLOS) $(SKYLOS_PRODUCTION_TARGETS) $(SKYLOS_EXCLUDE_ARGS) --category dead_code --gate --format concise --no-upload --no-provenance --no-grep-verify
	$(DUPLICATION_GATE) check
	+$(MAKE) spelling

install-nose: ## Install the pinned nose duplication detector
	@if [ "$$($(NOSE_BIN) --version 2>/dev/null)" = "nose $(NOSE_VERSION)" ]; then \
	  printf "nose %s already installed at %s\n" "$(NOSE_VERSION)" "$(NOSE_BIN)"; \
	else \
	  printf "Installing nose %s into %s\n" "$(NOSE_VERSION)" "$(NOSE_TOOLS_DIR)"; \
	  mkdir -p "$(NOSE_TOOLS_DIR)"; \
	  $(CARGO_BINSTALL) --no-confirm --install-path "$(NOSE_TOOLS_DIR)" \
	    --disable-strategies compile,quick-install \
	    --git https://github.com/corca-ai/nose 'nose-cli@$(NOSE_VERSION)'; \
	  test "$$($(NOSE_BIN) --version 2>/dev/null)" = "nose $(NOSE_VERSION)" || { \
	    printf "Error: %s did not report 'nose %s' after install\n" \
	      "$(NOSE_BIN)" "$(NOSE_VERSION)" >&2; \
	    exit 1; \
	  }; \
	fi

duplication: install-nose ## Run the blocking code-duplication gate
	$(DUPLICATION_GATE) check

duplication-test: ## Run the duplication-gate helper tests
	$(UV_GATE) tool --from 'python@$(DUPLICATION_TEST_PYTHON)' \
		--with pytest==$(DUPLICATION_TEST_PYTEST) \
		--with cyclopts==$(CYCLOPTS_VERSION) \
		--with tomlkit==$(TOMLKIT_VERSION) \
		--with hypothesis==$(DUPLICATION_TEST_HYPOTHESIS) \
		-- python -m pytest -c /dev/null --rootdir=. -p no:cacheprovider \
		scripts/tests/test_atomic_write.py \
		scripts/tests/test_duplication_gate.py \
		scripts/tests/test_duplication_gate_commands.py \
		scripts/tests/test_duplication_gate_make.py \
		scripts/tests/test_duplication_gate_persistence.py \
		scripts/tests/test_duplication_gate_properties.py \
		scripts/tests/test_make_install_nose.py \
		scripts/tests/test_nose_config_contract.py \
		scripts/tests/test_nose_detector.py

# Accept FIRST/SECOND/REASON (and skylos SYMBOL) only from the make command
# line. `cli_value` filters on `$(origin ...)` rather than `$(value ...)`
# because an ambient FIRST or REASON in the environment must not silently
# create an exception; the Skylos interface below predates this and keeps
# `$(value ...)` plus its own empty-value guards.
cli_value = $(if $(filter command line,$(origin $(1))),$(value $(1)))

duplication-allow: export DUPLICATION_FIRST = $(call cli_value,FIRST)
duplication-allow: export DUPLICATION_SECOND = $(call cli_value,SECOND)
duplication-allow: export DUPLICATION_REASON = $(call cli_value,REASON)
duplication-allow: ## Record one reasoned duplication exception
	@test -n "$${DUPLICATION_FIRST}" || { printf "Error: FIRST is required (path[::name])\\n" >&2; exit 2; }
	@test -n "$${DUPLICATION_REASON}" || { printf "Error: REASON is required for a duplication exception\\n" >&2; exit 2; }
	$(DUPLICATION_GATE) allow --first "$${DUPLICATION_FIRST}" \
		$(if $(call cli_value,SECOND),--second "$${DUPLICATION_SECOND}",) \
		--reason "$${DUPLICATION_REASON}"

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Add one named Skylos whitelist exception
	@case "$${SKYLOS_SYMBOL}" in *[![:space:]]*) ;; *) printf "Error: SYMBOL is required for a named whitelist exception\\n" >&2; exit 2;; esac
	@case "$${SKYLOS_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a named whitelist exception\\n" >&2; exit 2;; esac
	flock "$(SKYLOS_WHITELIST_LOCK)" env $(SKYLOS_CLI) whitelist "$${SKYLOS_SYMBOL}" --reason "$${SKYLOS_REASON}"

typecheck: build ## Run typechecking
	$(TY) --version
	# --extra-search-path duplicates tool.ty.environment.extra-paths because
	# ty 0.0.74 drops config-sourced extra paths when its resolver re-runs.
	# scripts/ is excluded here and checked by the second invocation below:
	# those files are PEP 723 tooling on a CPython 3.14 floor, so checking them
	# against the application's 3.12 assumption reports interpreter mismatches
	# rather than defects. scripts/ty.toml carries that pass's own settings.
	$(TY) check --python .venv --extra-search-path scripts --exclude scripts
	$(TY) check --python .venv --config-file scripts/ty.toml --extra-search-path scripts scripts

markdownlint: ## Lint Markdown files
	$(MAKE) markdownlint-run
	+$(MAKE) spelling

spelling: ## Enforce en-GB-oxendict spelling in Markdown prose
	@$(UV_GATE) run --group dev -- python scripts/generate_typos_config.py
	@set -eu; \
		markdown_paths=$$(mktemp); \
		trap 'rm -f "$$markdown_paths"' EXIT; \
		existing_paths=$$(mktemp); \
		trap 'rm -f "$$markdown_paths" "$$existing_paths"' EXIT; \
		git ls-files --cached --others --exclude-standard \
			--deduplicate -z -- '*.md' > "$$markdown_paths"; \
		$(UV_GATE) run --group dev -- python \
			"$(PROJECT_ROOT)/scripts/filter_existing_markdown.py" \
			< "$$markdown_paths" > "$$existing_paths"; \
		xargs -0 -r $(TYPOS) --config typos.toml --force-exclude < "$$existing_paths"

nixie: $(NIXIE) ## Validate Mermaid diagrams
	$(NIXIE) --no-sandbox

makeutil: ## Verify the Makefile parser used by contract tests
	$(call ensure_tool,$@)

test: build $(UV) $(VENV_TOOLS) makeutil test-workflow-contracts ## Run tests
	$(UV_GATE) run --group dev -- pytest -v -n auto

test-workflow-contracts: $(UV) ## Run the shared CV-005 CodeScene contracts
	$(CV005_CONTRACTS) check --repository .

windows-smoke: build $(UV) $(VENV_TOOLS) ## Run Windows smoke workflow and capture IPC logs
	$(UV_GATE) run --group dev -- pytest -v $(WINDOWS_SMOKE_ARGS)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
