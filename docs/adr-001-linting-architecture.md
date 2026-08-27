# Architectural decision record (ADR) 001: Python linting architecture

## Status

Accepted. CmdMox uses Ruff as the first lint tier, PyPy-backed Pylint as the
second lint tier, and a strict Skylos dead-code scan as the final production
liveness check.

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

Ruff and Pylint do not model cross-module symbol liveness. CmdMox therefore
also needs a deterministic dead-code check that identifies unused production
symbols without letting test-only references keep them alive. Framework-style
dispatch, ctypes metadata, and the shim bootstrap include verified runtime
surfaces that require narrowly reasoned exceptions rather than a broad baseline.

## Decision drivers

- Keep `make lint` as the single developer entrypoint for lint validation.
- Preserve Ruff as the fast first pass.
- Add Pylint without making it a separate optional check that developers forget
  to run.
- Share the Episodic lint policy where it applies to CmdMox.
- Keep existing CmdMox lint debt visible without forcing unrelated refactors
  into the lint architecture change.
- Pin the shim revision so lint execution is reproducible.
- Detect genuine unused production symbols in the standard local and CI gate.
- Keep dead-code exceptions explicit, typed where possible, and reviewable in
  `pyproject.toml`.

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

CmdMox adopts Ruff, PyPy-backed Pylint, and Skylos as its source linting
architecture.

The `lint` target runs `ruff check` first and then runs Pylint through
`pylint-pypy-shim`. Ruff and Pylint policy are configured in `pyproject.toml`,
while the Makefile defines the executable composition and the temporary
CmdMox-specific Pylint baseline.

The final stage provisions Skylos 4.33.2 separately from the project
environment. It scans only `cmd_mox` with
`--category dead_code --gate
--format concise --no-upload --no-provenance --no-grep-verify`.
The existing Linux CI `make lint` step therefore enforces the same local,
non-interactive production scan. Strict mode fails the gate for unexplained
findings.

Verified runtime entry points are recorded with symbol type, fully qualified
name, and reason under `[tool.skylos.dead_code.entrypoints]`. Exceptions that
cannot describe an entry point are stored in both
`[tool.skylos.whitelist].names` and `[tool.skylos.whitelist.documented]`, with
a caller-specific reason. Symbols are grouped only when the same runtime caller
or lifecycle reaches all of them; separate records describe different callers.
This preserves a narrow, auditable distinction between real dead code and
static-analysis limits.

## Consequences

- Developers continue to run one command: `make lint`.
- Ruff remains the fastest feedback path and blocks before Pylint starts.
- Pylint adds second-tier checks without becoming a separate manual workflow.
- Skylos removes confirmed dead production code and blocks new unexplained
  findings locally and in Linux CI.
- Test references do not influence the dead-code graph, and the scan does not
  upload code, collect provenance, or invoke cloud analysis.
- Runtime false positives require reasoned, version-controlled entry-point or
  whitelist configuration; the contract test protects the reviewed symbols.
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
- Review Skylos exceptions whenever the runtime lifecycle, ctypes protocol, or
  bootstrap behaviour changes, and remove obsolete entries with the code that
  made them unnecessary.
- Update the pinned Skylos release only with a clean production scan and the
  complete lint contract test.

## Addendum — 2026-08-24: Skylos fourth Python lint tier

The original two-tier decision is historical. The current Python lint
architecture records Skylos as the fourth tier in the complete quality gate.
The pipeline now runs Ruff first, PyPy-backed Pylint second, the spelling
policy third, and Skylos fourth. This records the full project lint workflow
without altering the historical Pylint decision.
Skylos is blocking: it runs with the pinned Python 3.14 command-only CLI,
scans production modules while excluding test paths, and uses strict gate mode
for unexplained dead-code findings. Scan-only global options such as
`--config-file pyproject.toml` remain separate from that CLI macro so the
command-first `whitelist` helper can dispatch safely.

Investigate every finding and remove genuine dead code. Model implicit runtime
callers with typed entry-point rules first; use the documented allow list only
when an entry-point rule cannot describe the verified boundary. The helper is:

```bash
make skylos-allow SYMBOL=handler REASON="Loaded by plugin registry"
```

The `SYMBOL` name avoids WSL's `NAME` collision, and both variables are
required, including rejection of whitespace-only values. The helper
serializes its read-modify-write through `flock` on the ignored,
repository-local `.skylos-whitelist.lock`; tests may override that path when
isolating the helper from the checkout. Keep the caller-specific reason in the
reviewed `[tool.skylos.whitelist.documented]` configuration.
