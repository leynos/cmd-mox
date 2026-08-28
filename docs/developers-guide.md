# Developer guide

This guide documents the local development checks for CmdMox. It is the source
of truth for running lint, understanding the current lint baseline, and knowing
where lint policy is configured.

## Linting

CmdMox uses a three-tier linting pipeline. Run it with:

```bash
make lint
```

The `lint` target first builds the development environment through
`make build`. It then runs the three lint tiers in order:

1. `ruff check`
2. PyPy-backed Pylint through `pylint-pypy-shim`
3. CPython 3.14 Pylint with `df12_python_lints`, followed by `ambrleaks`

Ruff is the fast first tier. It enforces import order, pycodestyle and Pyflakes
rules, pathlib usage, docstring rules, pytest rules, selected Ruff preview
rules, and a broad set of code-health checks imported from Episodic.

Pylint is the slower second tier. It runs after Ruff because it catches
different classes of problems, especially logging format mistakes, pattern
matching issues, selected refactoring hints, environment and subprocess
footguns, and module or function shape limits. The Pylint tier is intentionally
focused: `pyproject.toml` disables all Pylint messages by default and then
enables only the selected messages that complement Ruff.

The DF12 tier is isolated from the project environment and uses uv-managed
CPython 3.14. Its separate `pylintrc-df12.toml` enables every DF12 checker
against CmdMox's supported Python 3.12 source baseline. `ambrleaks` then scans
the test snapshot area for unredacted values.

## Makefile lint variables

The `Makefile` exposes the lint runner through variables so developers and
Continuous Integration (CI) jobs can override the runtime without editing
project files.

| Variable                  | Default                                                                                                                            | Purpose                                                                           |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `RUFF`                    | `$(UV_ENV) $(UV) run ruff`                                                                                                         | Runs the Ruff command inside the `uv` environment.                                |
| `PYLINT_PYTHON`           | `pypy`                                                                                                                             | Selects the Python interpreter used by `uv tool run` for Pylint.                  |
| `PYLINT_TARGETS`          | `cmd_mox conftest.py examples tests`                                                                                               | Lists the directories and files linted by Pylint.                                 |
| `PYLINT_PYPY_SHIM_REF`    | `726d09f968b4d729ee4b29c71fc732e744854f3b`                                                                                         | Pins the shim repository revision.                                                |
| `PYLINT_PYPY_SHIM`        | `git+https://github.com/leynos/pylint-pypy-shim.git@$(PYLINT_PYPY_SHIM_REF)`                                                       | Identifies the shim package used by `uv tool run`.                                |
| `PYLINT_BASELINE_DISABLE` | Existing cmd-mox baseline                                                                                                          | Temporarily disables legacy Pylint findings while keeping the second tier active. |
| `PYLINT`                  | `$(UV_ENV) $(UV) tool run --python $(PYLINT_PYTHON) --from '$(PYLINT_PYPY_SHIM)' pylint-pypy --disable=$(PYLINT_BASELINE_DISABLE)` | Builds the full PyPy-backed Pylint command.                                       |
| `DF12_PYTHON`             | `3.14`                                                                                                                             | Selects CPython for the isolated DF12 tooling tier.                               |
| `DF12_PYTHON_LINTS_REF`   | `9c835f35b0f1690597ade799c9c6a30bc5922959`                                                                                         | Pins the immutable DF12 lint and `ambrleaks` revision.                            |
| `DF12_PYTHON_LINTS`       | `git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)`                                                     | Identifies the common source for the DF12 Pylint plugin and `ambrleaks`.          |
| `DF12_PYLINT`             | uv-isolated Pylint under CPython 3.14                                                                                              | Runs the enabled DF12 checker set with `pylintrc-df12.toml`.                      |
| `AMBRLEAKS`               | uv-isolated `ambrleaks` under CPython 3.14                                                                                         | Scans tracked test snapshot files for unredacted values.                          |

_Table 1: Makefile variables for the lint pipeline._

Override variables on the command line when a local investigation needs a
different target set or interpreter:

```bash
make lint PYLINT_TARGETS=cmd_mox/ipc PYLINT_PYTHON=pypy
```

Do not bypass `make lint` for normal validation. Running the target keeps the
Ruff, PyPy Pylint, DF12 Pylint, and snapshot-leak tiers ordered consistently
with CI and preserves the shared `uv` cache configuration.

## Snapshot testing

The development dependency group includes
[syrupy](https://github.com/syrupy-project/syrupy), which provides the
`snapshot` fixture used by the IPC model and server callback tests. These tests
compare serialized payloads with the tracked `.ambr` files in
`tests/__snapshots__`.

Run the snapshot tests with the normal test command:

```bash
uv run pytest tests/test_ipc_models_unit.py tests/test_ipc_server_callbacks.py
```

When an intentional payload change requires new expected output, update the
snapshots explicitly and review the resulting `.ambr` diff:

```bash
uv run pytest tests/test_ipc_models_unit.py tests/test_ipc_server_callbacks.py \
  --snapshot-update
```

Run the focused tests again without `--snapshot-update` before committing so
that the committed snapshots are verified rather than rewritten.

## Property-based testing

The development dependency group includes
[Hypothesis](https://hypothesis.readthedocs.io/) (`hypothesis>=6`), which
generates inputs for property-based tests: rather than asserting a fixed
table of examples, a property states an invariant that must hold across a
broad input space.

`cmd_mox/unittests/test_command_double_matches.py` uses it to exercise
`CommandDouble.matches`, generating command-name pairs to check that a double
never matches an invocation for a different command.

Run the affected test module with the normal test command:

```bash
uv run pytest cmd_mox/unittests/test_command_double_matches.py
```

Hypothesis complements the snapshot tests above rather than replacing them:
snapshots pin exact serialized output, while properties assert relations that
should hold for every generated input.

## IPC request dispatch

The shared IPC request pipeline keeps transport handling separate from request
dispatch:

- `_REQUEST_HANDLERS` maps each wire `kind` to its validator and the public
  handler-method name.
- `_request_pipeline` parses and validates a request before dispatching it.
- `_execute_request` resolves and calls `handle_invocation` or
  `handle_passthrough_result` on the actual server instance.
- This virtual dispatch is intentional. Do not replace it with fixed
  module-level processors that bypass subclass overrides.
- Direct `_request_pipeline` tests cover parsing, validation, and response
  encoding.
- Socket-level tests cover transport-to-hook dispatch.
- Each request emits exactly one bounded dispatch record with `operation`,
  `kind`, `outcome`, `duration_ms`, an `invocation_id` only for passthrough
  results, and an `error_category` only on failure. Payloads, arguments,
  standard streams, environments, socket paths, and exception messages must
  never be logged.

`CommandDouble.matches` must reject an `Invocation` whose `command` differs
from `CommandDouble.name`. It performs that command-name check before
expectation matching and must not invoke expectation matching for a different
command. This prevents a double from accepting an invocation owned by another
command.

## Spelling policy

The lint and Markdown gates run a pinned `typos` release with British English
and Oxford `-ize` conventions. Before checking maintained Markdown, the
generator refreshes the shared estate dictionary into an untracked local cache
only when the authority is newer and merges `typos.local.toml`. The generated
`typos.toml` is reviewed and committed so a clean network-restricted checkout
can still enforce the last known-good policy.

Add repository-only proper names or quoted upstream terms to
`typos.local.toml`; never edit generated entries in `typos.toml` by hand.

## Episodic lint policy

CmdMox imports its lint posture from
[Episodic](https://github.com/leynos/episodic). The imported policy has three
goals:

- keep Ruff as the fast, broad, first-pass linter;
- use focused Pylint checks for problems that Ruff does not cover as well; and
- run Pylint under PyPy through the shared
  [pylint-pypy-shim](https://github.com/leynos/pylint-pypy-shim) approach.

The DF12 extension preserves that baseline while adding an isolated CPython
3.14 pass. Its plugin is deliberately configured separately so baseline Pylint
debt cannot hide DF12 house-style and suppression-explanation checks.

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
  checks (`D` and `DOC`), asynchronous-code checks (`ASYNC`), annotation checks
  (`ANN`), Ruff-specific checks (`RUF`), and Pylint-compatible checks (`PLR`,
  `PLE`, and `PLW`).
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
- `[tool.ruff.lint.pydoclint]` applies complete-docstring checks without
  requiring boilerplate for intentional one-line private-helper summaries.

### Pylint tables

- `[tool.pylint.main]` enables recursive directory linting and sets the
  module-line ceiling.
- `[tool.pylint.design]` aligns Pylint design thresholds with the Ruff policy
  while keeping a wider legacy allowance where needed.
- `[tool.pylint."messages control"]` disables all messages by default, then
  enables only the selected second-tier checks. It also disables `syntax-error`
  because the managed PyPy runtime can parse a narrower grammar than the
  project source uses for modern type-alias syntax.

The `Makefile` currently supplies `PYLINT_BASELINE_DISABLE` in addition to the
`pyproject.toml` tables. That split is intentional: `pyproject.toml` documents
the desired selected Pylint policy, while the `Makefile` carries the temporary
project baseline required to keep the new tier actionable.

`pylintrc-df12.toml` is the separate configuration for the CPython 3.14 DF12
tier. It loads `df12_python_lints`, analyses the Python 3.12 source baseline,
disables the general Pylint catalogue, and enables the complete DF12 checker
list explicitly. This keeps the DF12 policy auditable and independent of the
temporary PyPy Pylint baseline.

## Updating lint policy

When changing lint policy:

1. Update `pyproject.toml` or the relevant `Makefile` variable.
2. Document the behaviour change in this guide.
3. Run `make lint`.
4. If Markdown or ADR files changed, run `make markdownlint` and `make nixie`.
5. Remove baseline suppressions when the underlying code has been cleaned up.

Changes that add broad new rule families should explain whether failures are
fixed immediately or recorded as a visible baseline.

## Workflow pins and Dependabot

Dependabot owns the upgrade of GitHub Actions and reusable workflows, including
calls into `leynos/shared-actions`. Contract tests that assert a caller's exact
commit SHA create a lockstep dependency: every time Dependabot opens a bump PR,
the test fails until a human edits the pinned constant to match. That defeats
the purpose of automated dependency updates and turns a routine bump into a
manual chore.

Contract tests may still verify the _shape_ of a reusable-workflow caller. They
must not verify the specific SHA value.

- Do assert the workflow references the correct reusable workflow path.
- Do assert the ref is pinned to a full 40-character commit SHA, not a
  mutable branch such as `main` or `rolling`.
- Do assert the expected `on:` triggers, least-privilege `permissions:`, and
  the inputs the caller relies on.
- Do not hard-code the current SHA value as an expected string. Match it with
  a pattern instead.
- Do not fail a test purely because Dependabot bumped the pinned SHA.

```python
import re

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

def test_uses_pinned_full_sha(caller_step):
    ref = caller_step["uses"].split("@")[-1]
    assert SHA_RE.match(ref), f"expected a 40-hex commit SHA, got {ref!r}"
```

If a workflow's behaviour genuinely depends on a feature only present from a
particular commit onwards, express that as a comment or a changelog note, not
as a test assertion on the SHA string.
