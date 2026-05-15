# Developer guide

This guide documents the local development checks for CmdMox. It is the source
of truth for running lint, understanding the current lint baseline, and knowing
where lint policy is configured.

## Linting

CmdMox uses a two-tier linting pipeline. Run it with:

```bash
make lint
```

The `lint` target first builds the development environment through `make build`.
It then runs the two lint tiers in order:

1. `ruff check`
2. PyPy-backed Pylint through `pylint-pypy-shim`

Ruff is the fast first tier. It enforces import order, pycodestyle and Pyflakes
rules, pathlib usage, docstring rules, pytest rules, selected Ruff preview
rules, and a broad set of code-health checks imported from Episodic.

Pylint is the slower second tier. It runs after Ruff because it catches
different classes of problems, especially logging format mistakes, pattern
matching issues, selected refactoring hints, environment and subprocess
footguns, and module or function shape limits. The Pylint tier is intentionally
focused: `pyproject.toml` disables all Pylint messages by default and then
enables only the selected messages that complement Ruff.

## Makefile lint variables

The `Makefile` exposes the lint runner through variables so developers and
Continuous Integration (CI) jobs can override the runtime without editing
project files.

| Variable | Default | Purpose |
| --- | --- | --- |
| `RUFF` | `$(UV_ENV) $(UV) run ruff` | Runs the Ruff command inside the `uv` environment. |
| `PYLINT_PYTHON` | `pypy` | Selects the Python interpreter used by `uv tool run` for Pylint. |
| `PYLINT_TARGETS` | `cmd_mox conftest.py examples tests` | Lists the directories and files linted by Pylint. |
| `PYLINT_PYPY_SHIM_REF` | `726d09f968b4d729ee4b29c71fc732e744854f3b` | Pins the shim repository revision. |
| `PYLINT_PYPY_SHIM` | `git+https://github.com/leynos/pylint-pypy-shim.git@$(PYLINT_PYPY_SHIM_REF)` | Identifies the shim package used by `uv tool run`. |
| `PYLINT_BASELINE_DISABLE` | Existing cmd-mox baseline | Temporarily disables legacy Pylint findings while keeping the second tier active. |
| `PYLINT` | `$(UV_ENV) $(UV) tool run --python $(PYLINT_PYTHON) --from '$(PYLINT_PYPY_SHIM)' pylint-pypy --disable=$(PYLINT_BASELINE_DISABLE)` | Builds the full PyPy-backed Pylint command. |

_Table 1: Makefile variables for the lint pipeline._

Override variables on the command line when a local investigation needs a
different target set or interpreter:

```bash
make lint PYLINT_TARGETS=cmd_mox/ipc PYLINT_PYTHON=pypy
```

Do not bypass `make lint` for normal validation. Running the target keeps the
Ruff and Pylint tiers ordered consistently with CI and preserves the shared
`uv` cache configuration.

## Episodic lint policy

CmdMox imports its lint posture from
[Episodic](https://github.com/leynos/episodic). The imported policy has three
goals:

- keep Ruff as the fast, broad, first-pass linter;
- use focused Pylint checks for problems that Ruff does not cover as well; and
- run Pylint under PyPy through the shared
  [pylint-pypy-shim](https://github.com/leynos/pylint-pypy-shim) approach.

The policy is adapted for CmdMox rather than copied blindly. CmdMox targets
Python 3.12 in `pyproject.toml`, while Episodic targets a newer interpreter.
Unsupported Ruff selectors are omitted, and the existing CmdMox baseline is
made explicit so the imported lint architecture can land without unrelated
behavioural refactors.

## `pyproject.toml` lint configuration

Most lint policy lives in `pyproject.toml`.

### Ruff tables

- `[tool.ruff]` sets shared Ruff behaviour, including `line-length = 88`,
  `preview = true`, and `target-version = "py312"`.
- `[tool.ruff.lint]` selects the imported rule families. The selection includes
  Pyflakes (`F`), pycodestyle (`E` and `W`), import ordering (`I`), pathlib
  checks (`PTH`), security checks (`S`), pytest checks (`PT`), documentation
  checks (`D`), annotation checks (`ANN`), Ruff-specific checks (`RUF`), and
  Pylint-compatible checks (`PLR`, `PLE`, and `PLW`).
- `extend-ignore` records conflicts and the current CmdMox baseline. Entries
  here should be removed when the corresponding code is cleaned up.
- `[tool.ruff.lint.per-file-ignores]` relaxes assertion and parameter-count
  rules in test and step files where pytest and behaviour-driven development
  (BDD) patterns need them.
- `[tool.ruff.lint.mccabe]` and `[tool.ruff.lint.pylint]` define complexity,
  argument-count, boolean-expression, and local-variable thresholds.
- `[tool.ruff.lint.flake8-import-conventions]` bans selected `from` imports
  and sets standard aliases such as `typing as typ` and
  `collections.abc as cabc`.
- `[tool.ruff.lint.flake8-tidy-imports.banned-api]` rejects deprecated
  `typing` collection aliases in favour of built-in collection types,
  `collections.abc`, `collections`, `contextlib`, or `re` as appropriate.
- `[tool.ruff.lint.pydocstyle]` keeps docstrings on the NumPy convention.

### Pylint tables

- `[tool.pylint.main]` enables recursive directory linting and sets the
  module-line ceiling.
- `[tool.pylint.design]` aligns Pylint design thresholds with the Ruff policy
  while keeping a wider legacy allowance where needed.
- `[tool.pylint."messages control"]` disables all messages by default, then
  enables only the selected second-tier checks. It also disables
  `syntax-error` because the managed PyPy runtime can parse a narrower grammar
  than the project source uses for modern type-alias syntax.

The `Makefile` currently supplies `PYLINT_BASELINE_DISABLE` in addition to the
`pyproject.toml` tables. That split is intentional: `pyproject.toml` documents
the desired selected Pylint policy, while the `Makefile` carries the temporary
project baseline required to keep the new tier actionable.

## Updating lint policy

When changing lint policy:

1. Update `pyproject.toml` or the relevant `Makefile` variable.
2. Document the behaviour change in this guide.
3. Run `make lint`.
4. If Markdown or ADR files changed, run `make markdownlint` and `make nixie`.
5. Remove baseline suppressions when the underlying code has been cleaned up.

Changes that add broad new rule families should explain whether failures are
fixed immediately or recorded as a visible baseline.
