NIXIE ?= nixie
# Development and tool uv calls go through the vendored helper, which cleans the
# environment, uses the global uv cache, runs offline first and goes online at
# most once. The release build uses plain `uv build` to match CI and let the
# isolated build environment resolve its own backend requirements. See
# https://github.com/leynos/shared-actions/tree/main/uv_gate for the helper contract.
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
# Keep nested pytest subprocesses within the 30-second per-test timeout.
PYTEST_WORKERS ?= 4
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
# Commit SHA of the df12-python-lints main commit that adds the df12-skylos and
# df12-duplication commands; the same pin provisions Pylint and ambrleaks.
DF12_PYTHON_LINTS_REF ?= 56ad71721c3f1f2164ec84b69150582488f1a927
DF12_PYTHON_LINTS = git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)
DF12_PYLINT = UV_PYTHON_PREFERENCE=only-managed $(UV_GATE) tool --python $(DF12_PYTHON) --from '$(DF12_PYTHON_LINTS)' -- pylint
AMBRLEAKS = UV_PYTHON_PREFERENCE=only-managed $(UV_GATE) tool --python $(DF12_PYTHON) --from '$(DF12_PYTHON_LINTS)' -- ambrleaks
# The dead-code gate is the installed df12-skylos command; its `skylos` extra
# carries the one Skylos pin. Skylos parses source with its own runtime AST, so
# the tool environment runs Python 3.14.
SKYLOS = $(UV_GATE) tool --python 3.14 --from \
	'df12-python-lints[skylos] @ $(DF12_PYTHON_LINTS)' -- df12-skylos
# The duplication gate is the installed df12-duplication command. It downloads
# the nose release named by [tool.nose] version and verifies it against the
# digest table shipped in the package, so no nose pin lives here.
DUPLICATION = $(UV_GATE) tool --from \
	'df12-python-lints[duplication] @ $(DF12_PYTHON_LINTS)' -- df12-duplication
WINDOWS_SMOKE_ARGS = tests/test_windows_environment.py \
	tests/test_windows_support_bdd.py \
	tests/test_named_pipe_server.py \
	tests/test_shim_stdin_timeout.py \
	--log-file=windows-ipc.log \
	--log-file-level=DEBUG \
	--log-file-format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
# Representative pytest-bdd modules, executed rather than collected. A
# dependency update can break step injection in every BDD module while
# `--collect-only` still exits zero, so this gate runs the scenarios.
# tests/test_bdd_gate_contract.py holds the target to this list and to the
# locked dev group.
BDD_MODULES = tests/test_command_double_record_bdd.py \
	tests/test_command_double_replay_bdd.py \
	tests/test_recording_session_bdd.py \
	tests/test_replay_session_bdd.py \
	tests/test_documentation_bdd.py \
	tests/test_pytest_plugin_bdd.py

.PHONY: help all clean build prepare build-release lint interrogate fmt check-fmt yamllint actionlint
.PHONY: markdownlint markdownlint-run nixie spelling skylos skylos-allow test test-workflow-contracts typecheck
.PHONY: duplication duplication-allow bdd
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
	rm -rf dist
	$(UV) build --out-dir dist

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

# Docstring presence for every Python file the repository owns; Ruff keeps style.
INTERROGATE_VERSION ?= 1.7.0
INTERROGATE = $(UV_GATE) tool --from interrogate@$(INTERROGATE_VERSION) -- interrogate
INTERROGATE_TARGETS ?= cmd_mox tests scripts examples conftest.py
# Level 2 prints a MISSED row per undocumented definition, which the
# `interrogate` target fails on: the rounded percentage alone passes a lone gap.
INTERROGATE_FLAGS ?= -vv --fail-under 100

# Interrogate rounds its percentage to one decimal, so `--fail-under 100` alone
# passes a lone gap in a large repository; fail on any MISSED row as well.
interrogate: ## Enforce complete Python docstring coverage
	@report="$$($(INTERROGATE) $(INTERROGATE_FLAGS) $(INTERROGATE_TARGETS))"; \
	  status=$$?; \
	  printf '%s\n' "$$report"; \
	  test "$$status" -eq 0 || exit "$$status"; \
	  if printf '%s\n' "$$report" | grep -q '| *MISSED *|'; then \
	    printf '%s\n' 'interrogate: undocumented definitions remain; see the MISSED rows above' >&2; \
	    exit 1; \
	  fi

lint: build yamllint actionlint interrogate skylos duplication ## Run linters
	$(RUFF) check
	$(PYLINT) $(PYLINT_TARGETS)
	$(DF12_PYLINT) --rcfile=pylintrc-df12.toml $(PYLINT_TARGETS)
	$(AMBRLEAKS) tests
	+$(MAKE) spelling

skylos: ## Detect dead production code
	$(SKYLOS) check --repository .

duplication: ## Run the blocking code-duplication gate
	$(DUPLICATION) install
	$(DUPLICATION) check

# Accept FIRST/SECOND/REASON only from the make command line. `cli_value`
# filters on `$(origin ...)` rather than `$(value ...)` because an ambient
# FIRST or REASON in the environment must not silently create an exception.
#
# `$(value ...)` stops make from expanding the variable, but not from writing
# its text into the recipe, where that text becomes part of the shell's own
# program. Every argument therefore reaches the recipe only through the
# environment, whose values a shell never re-expands as code, and the repeated
# options are rebuilt from that value at run time.
cli_value = $(if $(filter command line,$(origin $(1))),$(value $(1)))

duplication-allow: export DUPLICATION_FIRST = $(call cli_value,FIRST)
duplication-allow: export DUPLICATION_SECOND = $(call cli_value,SECOND)
duplication-allow: export DUPLICATION_REASON = $(call cli_value,REASON)
duplication-allow: ## Record one reasoned duplication exception
	@case "$${DUPLICATION_FIRST}" in *[![:space:]]*) ;; *) printf "Error: FIRST is required (path[::name])\\n" >&2; exit 2;; esac
	@case "$${DUPLICATION_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a duplication exception\\n" >&2; exit 2;; esac
	@case "$${DUPLICATION_SECOND}" in *[![:print:][:space:]]*) printf "Error: SECOND members must be printable text\\n" >&2; exit 2;; esac
	@$(DUPLICATION) install
	@set -f; set --; for key in $${DUPLICATION_SECOND}; do set -- "$$@" --second "$$key"; done; \
		$(DUPLICATION) allow --first "$${DUPLICATION_FIRST}" "$$@" \
			--reason "$${DUPLICATION_REASON}"

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception (SYMBOL=name REASON=text)
	$(SKYLOS) allow --repository . --symbol="$${SKYLOS_SYMBOL}" --reason="$${SKYLOS_REASON}"

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

test: build $(UV) $(VENV_TOOLS) test-workflow-contracts ## Run tests
	$(UV_GATE) run --group dev -- pytest -v -n $(PYTEST_WORKERS)

bdd: build ## Execute representative pytest-bdd scenarios
	$(UV_GATE) run --group dev -- pytest -v $(BDD_MODULES)

test-workflow-contracts: $(UV) ## Run the shared CV-005 CodeScene contracts
	$(CV005_CONTRACTS) check --repository .

windows-smoke: build $(UV) $(VENV_TOOLS) ## Run Windows smoke workflow and capture IPC logs
	$(UV_GATE) run --group dev -- pytest -v $(WINDOWS_SMOKE_ARGS)

YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint

# yamllint reads `.yamllint.yml`, which extends `.yamllint-estate.yml`, and
# resolves that path against the working directory, so it runs from the root.
# The file list is staged before the linter starts: if Git fails partway
# through, `&&` stops the recipe instead of linting a partial list.
yamllint: ## Lint every tracked YAML file strictly
	@tmp=$$(mktemp) || exit 1; \
	trap 'rm -f "$$tmp"' EXIT; \
	git ls-files -z '*.yml' '*.yaml' >"$$tmp" && \
	xargs -0 -r $(YAMLLINT) --strict -- <"$$tmp"

actionlint: ## Lint the GitHub Actions workflows, including their shell scripts
	$(ACTIONLINT)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
