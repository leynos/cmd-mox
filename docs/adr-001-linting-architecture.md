# Architectural decision record (ADR) 001: Two-tier linting architecture

## Status

Accepted. CmdMox uses Ruff as the first lint tier and PyPy-backed Pylint as the
second lint tier.

Amended 2026-08-27 to add a third lint tier; see
[Amendment (2026-08-27): third lint tier](#amendment-2026-08-27-third-lint-tier).

Amended 2026-09-25 to retire `pylint-pypy-shim` and to stop disabling
`syntax-error`; see
[Amendment (2026-09-25): plain Pylint on PyPy 3.12](#amendment-2026-09-25-plain-pylint-on-pypy-312).

## Date

2026-05-15.

## Context and problem statement

CmdMox needs a linting architecture that is fast enough for routine local use,
strict enough to catch common Python maintenance risks, and consistent with the
lint policy used by Episodic. Ruff already provides fast broad coverage, but
some checks remain more useful in Pylint, especially logging format validation,
pattern matching diagnostics, and selected refactoring or design warnings.

Pylint also needs to run in a way that matches the Episodic approach: through
the `pylint-pypy-shim` repository and under PyPy as a second-tier lint action
after Ruff.

## Decision drivers

- Keep `make lint` as the single developer entrypoint for lint validation.
- Preserve Ruff as the fast first pass.
- Add Pylint without making it a separate optional check that developers forget
  to run.
- Share the Episodic lint policy where it applies to CmdMox.
- Keep existing CmdMox lint debt visible without forcing unrelated refactors
  into the lint architecture change.
- Pin the shim revision so lint execution is reproducible.

## Options considered

### Ruff only

Ruff-only linting is fast and simple, but it loses the second-tier Pylint
checks that are useful for logging, matching, environment handling,
subprocesses, and design limits.

### Ruff plus ordinary Pylint in the project environment

Running Pylint directly from the project virtual environment would be familiar,
but it would not follow the shared Episodic execution model. It would also
couple the Pylint runtime and dependency resolution to the main project
environment more tightly than necessary.

### Ruff plus PyPy-backed Pylint through `pylint-pypy-shim`

Running Ruff first and then invoking Pylint through `uv tool run --python pypy`
and `pylint-pypy-shim` matches Episodic, keeps the second tier isolated, and
allows the shim revision to be pinned independently of CmdMox dependencies.

| Topic              | Ruff only               | Ordinary Pylint         | PyPy-backed shim               |
| ------------------ | ----------------------- | ----------------------- | ------------------------------ |
| Speed              | Fastest                 | Slower                  | Slower second tier             |
| Coverage           | Broad, but not complete | Broader than Ruff alone | Broader than Ruff alone        |
| Episodic alignment | Partial                 | Partial                 | Strong                         |
| Runtime isolation  | Simple                  | Coupled to project venv | Isolated through `uv tool run` |
| Reproducibility    | Project lockfile        | Project lockfile        | Pinned shim revision           |

_Table 1: Linting architecture options._

## Decision outcome

CmdMox adopts the Ruff plus PyPy-backed Pylint architecture.

The `lint` target runs `ruff check` first and then runs Pylint through
`pylint-pypy-shim`. Ruff and Pylint policy are configured in `pyproject.toml`,
while the Makefile defines the executable composition and the temporary
CmdMox-specific Pylint baseline.

## Consequences

- Developers continue to run one command: `make lint`.
- Ruff remains the fastest feedback path and blocks before Pylint starts.
- Pylint adds second-tier checks without becoming a separate manual workflow.
- The project carries an explicit baseline for existing findings. This makes
  future clean-up incremental rather than hiding the stricter policy.
- The managed PyPy runtime may lag the syntax used by CmdMox. This
  consequence was originally absorbed by disabling `syntax-error`; the
  2026-09-25 amendment reverses that, so a parse gap now fails the lint.

## Follow-up work

- Remove `PYLINT_BASELINE_DISABLE` entries as the corresponding modules are
  cleaned up.
- Revisit unsupported Ruff selectors when the pinned Ruff version changes.
- Keep `docs/developers-guide.md` synchronized with Makefile and
  `pyproject.toml` lint policy changes.

## Amendment (2026-08-27): third lint tier

CmdMox adds a third lint tier: an isolated CPython 3.14 Pylint pass configured
in `pylintrc-df12.toml`. The `lint` target installs the DF12 plugin from a
pinned immutable revision and enables its complete checker set. The same pinned
distribution also provides `ambrleaks`, which scans test snapshots after the
Pylint passes.

The driver for this amendment was to keep DF12 checker policy independent of
the temporary PyPy baseline.

Consequence: the DF12 plugin and snapshot scanner run on CPython 3.14 without
changing the PyPy-backed Pylint baseline.

## Amendment (2026-09-25): plain Pylint on PyPy 3.12

PyPy 8 implements Python 3.12, and uv provides it as a managed interpreter.
Pylint runs on it without the object-build patch that `pylint-pypy-shim`
supplied, so the second tier now runs a pinned Pylint directly:
`uv tool run --python pypy@3.12 --from 'pylint==$(PYLINT_VERSION)' pylint`. The
interpreter is pinned to `pypy@3.12` rather than a bare `pypy`, so a new PyPy
release cannot change the lint grammar without a commit.

The `syntax-error` disable is removed. While it was in place, every module the
PyPy 3.11 runtime could not parse (PEP 695 type aliases, for example) produced
no messages at all and the tier passed. Those modules were never linted.
Removing the disable makes a parse failure a lint failure, and the findings
that PyPy 3.12 then reported in previously skipped modules are fixed in the
same change.
