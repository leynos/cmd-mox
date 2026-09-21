# Architectural decision record (ADR) 001: Two-tier linting architecture

## Status

Accepted. CmdMox uses Ruff as the first lint tier and PyPy-backed Pylint as the
second lint tier.

Amended 2026-08-27 to add a third lint tier; see
[Amendment (2026-08-27): third lint tier](#amendment-2026-08-27-third-lint-tier).

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
- The managed PyPy runtime may lag the syntax used by CmdMox. The Pylint
  configuration disables `syntax-error` so parse gaps do not prevent useful
  checks on files that Pylint can analyse.

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

## Addendum — 2026-08-24: Skylos fourth Python lint tier

The original two-tier decision is historical. CmdMox now records Skylos as the
fourth Python lint tier, after Ruff, PyPy-backed Pylint, and the DF12
Pylint/ambrleaks tier. The spelling policy remains a separate quality gate.
Skylos is a blocking production dead-code scan: it scans production modules
only, excludes test paths, enables strict gate mode, and uses the local-only
flags
`--category dead_code --gate --format concise --no-upload --no-provenance
--no-grep-verify`.

The command-only `SKYLOS_CLI` macro runs Skylos with Python 3.14. Skylos parses
source with the AST implementation of its own runtime, so pinning Python 3.14
prevents phantom findings when newer Python syntax is present. Scan-only global
options, including `--config-file pyproject.toml`, remain in a separate macro
used by the lint target; this leaves the `whitelist` subcommand first in the
documented helper invocation.

Investigate every finding and remove genuine dead code. Model verified implicit
runtime callers with typed `[[tool.skylos.dead_code.entrypoints]]` rules first,
using a caller-specific reason. Add a documented allow-list entry only when an
entry-point rule cannot model the verified boundary. The helper is:

```bash
make skylos-allow SYMBOL=handler REASON="Loaded by plugin registry"
```

The `SYMBOL` name avoids WSL's `NAME` collision, and both variables are
required, including rejection of whitespace-only values. The helper serializes
its read-modify-write through `flock` on the ignored, repository-local
`.skylos-whitelist.lock`; tests may override that path when isolating the
helper from the checkout. Keep the caller-specific reason in the reviewed
`[tool.skylos.whitelist.documented]` configuration.
