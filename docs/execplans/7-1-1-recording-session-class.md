# Implement RecordingSession Class with Fixture Persistence

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / Big Picture

Roadmap item XII-A introduces the core recording infrastructure for Record
Mode. After this change a developer can create a `RecordingSession`, feed it
`(Invocation, Response)` pairs captured from passthrough spies, and persist
them as a versioned JSON fixture file. Environment variables are filtered to a
safe, portable subset before writing. The fixture file can later be loaded and
round-tripped through `FixtureFile.from_dict(fixture.to_dict())` without data
loss.

Observable success: new unit tests in
`cmd_mox/unittests/test_recording_session.py` and
`cmd_mox/unittests/test_fixture_file.py` pass; a new BDD scenario in
`features/recording_session.feature` passes; `make test`, `make lint`,
`make check-fmt`, and `make typecheck` all succeed; `docs/usage-guide.md`
documents the recording session API; and the XII-A roadmap checkbox is marked
done.

## Constraints

- Python 3.12+ only (`from __future__ import annotations` in every file).
- Zero new external dependencies (JSON is stdlib).
- Public API and data models follow the design spec Section IX normative
  contract (field names, types, method signatures from Sections 9.3, 9.5.1,
  9.5.3).
- Fixture JSON schema version is `"1.0"`.
- `@dataclass(slots=True)` for all new data models.
- Unit tests colocated in `cmd_mox/unittests/`; BDD features in `features/`;
  step defs in `tests/steps/`.
- Tests written before implementation (AGENTS.md workflow).
- Existing tests must not break.
- No modifications to `PassthroughCoordinator` or `CommandDouble` in this
  plan (integration is XII-A follow-on items, not in scope).
- Scrubber class does not exist yet (XII-C). `RecordingSession` accepts
  `scrubber: Scrubber | None = None`. For MVP, `None` means no scrubbing.
  Define a minimal `Scrubber` Protocol in `cmd_mox/record/scrubber.py` so the
  type annotation is valid and future XII-C work can provide a concrete
  implementation.
- Quality gates: `make check-fmt`, `make typecheck`, `make lint`, `make test`
  must all pass.

## Tolerances (Exception Triggers)

- Scope: if implementation requires modifying more than 15 files or 800 net
  new lines (excluding tests), stop and escalate.
- Interface: if any existing public API signature must change, stop and
  escalate.
- Dependencies: if a new external dependency is required, stop and escalate.
- Iterations: if quality gates still fail after 3 targeted fix attempts, stop
  and escalate with captured logs.
- Ambiguity: if the design spec is ambiguous on a normative point, document
  the interpretation in Decision Log and proceed with the conservative choice.

## Risks

- Risk: The `Scrubber` type does not exist yet, so type annotations for the
  `scrubber` parameter may cause issues. Severity: low. Likelihood: medium.
  Mitigation: Define a minimal `Scrubber` Protocol with a single
  `scrub(recording) -> recording` method. This satisfies the type checker and
  provides a stable interface for XII-C.

- Risk: `make typecheck` has a pre-existing baseline of ~60 diagnostics in
  untouched files (observed in prior execplan). New code must not add to this
  baseline. Severity: medium. Likelihood: medium. Mitigation: Run typecheck
  early and compare diagnostic count against baseline. Only new diagnostics
  attributable to this change are blockers.

- Risk: `duration_ms` field in `RecordedInvocation` requires timing data that
  `RecordingSession.record()` does not inherently have from its
  `(Invocation, Response)` arguments. Severity: low. Likelihood: certain.
  Mitigation: Accept `duration_ms` as an optional keyword argument to
  `record()` defaulting to `0`. The caller (future PassthroughCoordinator
  integration) can supply measured timing. Document this design decision.

- Risk: Environment variable filtering logic may duplicate or conflict with
  existing `_is_sensitive_env_key()` in `cmd_mox/expectations.py`. Severity:
  low. Likelihood: medium. Mitigation: Reuse `_is_sensitive_env_key()` by
  importing it. Add command-prefix matching and allowlist logic as new helpers
  in the record module.

## Progress

- [x] (2026-02-25) Drafted ExecPlan.
- [x] (2026-02-25) Write unit tests for `RecordedInvocation`, `FixtureMetadata`,
  `FixtureFile` (red). 10 tests in `test_fixture_file.py`.
- [x] (2026-02-25) Write unit tests for `RecordingSession` lifecycle (red). 13
  tests in `test_recording_session.py`.
- [x] (2026-02-25) Write unit tests for environment variable subset filtering
  (red). 8 tests in `test_env_filter.py`.
- [x] (2026-02-25) Implement data models in `cmd_mox/record/fixture.py`.
- [x] (2026-02-25) Implement `Scrubber` Protocol in
  `cmd_mox/record/scrubber.py`.
- [x] (2026-02-25) Implement `RecordingSession` in
  `cmd_mox/record/session.py`.
- [x] (2026-02-25) Implement env filtering in `cmd_mox/record/env_filter.py`.
- [x] (2026-02-25) Create `cmd_mox/record/__init__.py` with public exports.
- [x] (2026-02-25) Confirm unit tests pass (green). 31/31 pass.
- [x] (2026-02-25) Write BDD feature file `features/recording_session.feature`.
  3 scenarios.
- [x] (2026-02-25) Write BDD step definitions in
  `tests/steps/recording_session.py`.
- [x] (2026-02-25) Write BDD test binding in
  `tests/test_recording_session_bdd.py`.
- [x] (2026-02-25) Confirm BDD tests pass. 3/3 pass.
- [x] (2026-02-25) Update `docs/usage-guide.md` with recording session API docs.
- [x] (2026-02-25) Record design decisions in design document. 3 decisions added
  (9.10.5, 9.10.6, 9.10.7).
- [x] (2026-02-25) Mark XII-A roadmap items as done in
  `docs/cmd-mox-roadmap.md`.
- [x] (2026-02-25) Run full quality gates. All pass (typecheck: 2 pre-existing
  diagnostics in `expectations.py`, not from this change).

## Surprises & Discoveries

- The existing `_is_sensitive_env_key()` in `expectations.py` only checks for
  substring matches against `SENSITIVE_ENV_KEY_TOKENS` (secret, token, api_key,
  password). This missed `*_KEY` patterns (e.g. `GITHUB_KEY`). The
  `env_filter.py` module adds the `_SECRET_ENV_KEY_RE` regex (originally from
  `ipc/models.py`) which matches KEY, TOKEN, SECRET, PASSWORD, CREDENTIALS,
  PASS, and PWD as word segments in underscore-separated env var names.

- The ruff lint rules ICN001 (require `import datetime as dt`) and TC001/TC003
  (move application/stdlib imports to TYPE_CHECKING blocks) required several
  iterations to get right. Runtime imports for `Path` in `FixtureFile.save()`
  and `.load()` had to use inline `from pathlib import Path as _Path` since the
  top-level import is in the TYPE_CHECKING block.

- The typecheck baseline shifted from ~60 diagnostics (observed in prior
  execplan) to just 2, both in `cmd_mox/expectations.py`. The `ty` checker
  version is 0.0.18.

- S105/S106 (hardcoded password) ruff rules are only triggered on assignment
  and comparison expressions involving variable names containing sensitive
  tokens, not on dictionary literals. This means `noqa: S105` is needed on
  assertions like `assert result["MY_SECRET_KEY"] == "supersecret"` but not on
  dict entries like `{"SECRET_TOKEN": "s3cret"}`.

## Decision Log

- Decision: Accept `duration_ms` as an optional keyword argument to
  `RecordingSession.record()` defaulting to `0`, rather than measuring time
  internally. Rationale: The `RecordingSession` receives pre-built
  `(Invocation, Response)` pairs. Timing is the responsibility of the caller
  (the passthrough coordinator, which brackets the real command execution).
  Accepting it as a parameter keeps the session stateless with respect to
  timing and avoids coupling to execution infrastructure. Date/Author:
  2026-02-25 / DevBoxer agent.

- Decision: Define `Scrubber` as a `typing.Protocol` with a single `scrub()`
  method rather than an abstract base class. Rationale: Protocols enable
  structural subtyping, allowing any object with a matching `scrub()` method to
  satisfy the type constraint without inheritance. This keeps the XII-A MVP
  independent of the XII-C concrete implementation. Date/Author: 2026-02-25 /
  DevBoxer agent.

- Decision: Retrieve the CmdMox version at runtime via
  `importlib.metadata.version("cmd-mox")` with a fallback to `"unknown"`.
  Rationale: The project does not expose `__version__` in
  `cmd_mox/__init__.py`; the version is only in `pyproject.toml`. The
  `importlib.metadata` API is the standard way to access installed package
  versions. Date/Author: 2026-02-25 / DevBoxer agent.

## Outcomes & Retrospective

### Outcomes

All acceptance criteria met:

- `make test`: 580 passed, 12 skipped. 34 new tests (10 fixture + 13 session +
  8 env filter + 3 BDD scenarios).
- `make lint`: All checks passed.
- `make check-fmt`: All checks passed.
- `make typecheck`: 2 pre-existing diagnostics in `expectations.py`; zero new
  diagnostics from this change.
- `make markdownlint`: 0 errors.
- `docs/usage-guide.md`: Contains "Recording sessions (fixture capture)"
  section with lifecycle docs and code examples.
- `docs/cmd-mox-roadmap.md`: XII-A sub-items marked `[x]`.
- Fixture JSON roundtrip verified by `test_from_dict_roundtrip` and
  `test_save_and_load` tests.
- Environment filtering verified by 8 unit tests and 1 BDD scenario.

### Retrospective

- The TDD red-green workflow was effective. Writing 31 tests first ensured
  comprehensive coverage and caught the `GITHUB_KEY` filtering gap immediately.
- The `Scrubber` Protocol approach worked cleanly; zero type errors from the
  optional scrubber parameter.
- Lint iteration took 3 rounds due to unfamiliarity with the ICN001 datetime
  aliasing convention and the nuances of S105 trigger conditions. Future work
  should check `.rules/python-typing.md` conventions before writing code.
- Net new files: 11. Net modified existing files: 3. Well within the 15-file
  tolerance.

## Context and Orientation

### Project overview

CmdMox is a Python-native command mocking library. It intercepts external
command invocations via PATH-based shim scripts and an IPC bus, providing
stub/mock/spy test doubles. The project follows a record-replay-verify
lifecycle.

### Key existing files

- `cmd_mox/ipc/models.py` -- `Invocation` (command, args, stdin, env, stdout,
  stderr, exit_code) and `Response` (stdout, stderr, exit_code, env).
  `Invocation.to_dict()` provides JSON-compatible serialization.
- `cmd_mox/expectations.py` -- `SENSITIVE_ENV_KEY_TOKENS` tuple and
  `_is_sensitive_env_key(key)` helper for env key sensitivity detection.
- `cmd_mox/passthrough.py` -- `PassthroughCoordinator` with
  `finalize_result()` where future recording hooks will attach.
- `cmd_mox/controller.py` -- `CmdMox` controller, `Phase` enum
  (RECORD/REPLAY/VERIFY).
- `cmd_mox/errors.py` -- Exception hierarchy rooted at `CmdMoxError`.
- `cmd_mox/__init__.py` -- Public API surface.
- `docs/python-native-command-mocking-design.md` Section IX -- Normative spec
  for Record Mode.
- `docs/cmd-mox-roadmap.md` -- Roadmap with XII-A checkboxes.
- `docs/usage-guide.md` -- User-facing documentation.

### Existing test patterns

Unit tests live in `cmd_mox/unittests/test_*.py`. They use `@dataclass`
fixtures, `pytest.raises`, and parametrized tests. BDD tests use
`features/*.feature` Gherkin files bound through `tests/test_*_bdd.py` files
that import step definitions from `tests/steps/*.py`.

### Build targets

- `make test` -- `pytest -v -n auto`
- `make lint` -- `ruff check`
- `make check-fmt` -- `ruff format --check` + `mdformat --check`
- `make typecheck` -- `ty`
- `make fmt` -- auto-format

## Plan of Work

### Stage A: Scaffolding and Test Shells (Red Phase)

Create the `cmd_mox/record/` package directory and empty module files so
imports resolve. Then write all unit tests against the not-yet-implemented
classes. Tests should fail (red phase).

### Stage B: Implement Data Models (Green Phase, Part 1)

Implement `ScrubbingRule` dataclass and `Scrubber` Protocol, then implement
`RecordedInvocation`, `FixtureMetadata`, and `FixtureFile` data models with
full serialization. Implement `filter_env_subset()`.

### Stage C: Implement RecordingSession (Green Phase, Part 2)

Implement `RecordingSession` with `start()`, `record()`, and `finalize()`
lifecycle methods.

### Stage D: BDD Tests

Write feature file, step definitions, and scenario bindings.

### Stage E: Documentation and Roadmap

Update usage guide, design document, and roadmap.

### Stage F: Quality Gates

Run all quality gates and fix any issues.

## Concrete Steps

1. Create `cmd_mox/record/` directory and `__init__.py`.
2. Create stub files: `fixture.py`, `session.py`, `scrubber.py`,
   `env_filter.py`.
3. Write `cmd_mox/unittests/test_fixture_file.py` (6 tests).
4. Write `cmd_mox/unittests/test_recording_session.py` (10 tests).
5. Write `cmd_mox/unittests/test_env_filter.py` (6 tests).
6. Run `make test` -- confirm new tests fail, existing tests pass.
7. Implement `cmd_mox/record/scrubber.py` (Protocol + ScrubbingRule).
8. Implement `cmd_mox/record/fixture.py` (3 dataclasses + serialization).
9. Implement `cmd_mox/record/env_filter.py` (filter function + constants).
10. Run fixture and env filter tests -- confirm green.
11. Implement `cmd_mox/record/session.py` (RecordingSession class).
12. Update `cmd_mox/record/__init__.py` with exports.
13. Run all unit tests -- confirm green.
14. Write `features/recording_session.feature` (3 scenarios).
15. Write `tests/steps/recording_session.py` (step definitions).
16. Write `tests/test_recording_session_bdd.py` (scenario binding).
17. Run BDD tests -- confirm green.
18. Update `docs/usage-guide.md` with recording session section.
19. Update `docs/python-native-command-mocking-design.md` with decisions.
20. Mark XII-A sub-items done in `docs/cmd-mox-roadmap.md`.
21. Run full quality gates: `make check-fmt`, `make typecheck`, `make lint`,
    `make test`, `make markdownlint`.
22. Fix any issues, re-run gates.
23. Update ExecPlan to COMPLETE.

## Validation and Acceptance

The change is accepted when all of the following are true:

- `make test` passes with all new tests green (expect ~22 new tests: 6
  fixture + 10 session + 6 env filter + 3 BDD scenarios).
- `make lint` passes with no new warnings.
- `make check-fmt` passes.
- `make typecheck` does not introduce new diagnostics beyond the pre-existing
  baseline.
- `docs/usage-guide.md` contains a "Recording sessions" section with example
  code.
- `docs/cmd-mox-roadmap.md` XII-A "RecordingSession" sub-items are checked.
- Fixture JSON roundtrip: `FixtureFile.from_dict(fixture.to_dict())` produces
  an equivalent object.
- Fixture file written to disk is valid JSON matching the v1.0 schema.
- Environment filtering excludes `PATH`, `HOME`, `*_SECRET`, `*_TOKEN` etc.
  and includes allowlisted and command-prefix keys.

## Idempotence and Recovery

All steps are safe to re-run. Test files can be overwritten. The
`cmd_mox/record/` package is entirely new, so no existing code is at risk.

If a quality gate fails:

- Fix only the reported issue.
- Re-run the failing gate.
- Then re-run all gates before marking complete.

## Artifacts and Notes

### New files created

- `cmd_mox/record/__init__.py`
- `cmd_mox/record/fixture.py`
- `cmd_mox/record/session.py`
- `cmd_mox/record/scrubber.py`
- `cmd_mox/record/env_filter.py`
- `cmd_mox/unittests/test_fixture_file.py`
- `cmd_mox/unittests/test_recording_session.py`
- `cmd_mox/unittests/test_env_filter.py`
- `features/recording_session.feature`
- `tests/steps/recording_session.py`
- `tests/test_recording_session_bdd.py`

### Existing files modified

- `docs/usage-guide.md` -- new "Recording sessions" section
- `docs/python-native-command-mocking-design.md` -- design decisions added
- `docs/cmd-mox-roadmap.md` -- XII-A checkboxes marked done
- `docs/execplans/7-1-1-recording-session-class.md` -- this plan (created)

## Interfaces and Dependencies

No new external dependencies.

### New public interfaces

In `cmd_mox/record/fixture.py`:

```python
@dc.dataclass(slots=True)
class RecordedInvocation:
    sequence: int
    command: str
    args: list[str]
    stdin: str
    env_subset: dict[str, str]
    stdout: str
    stderr: str
    exit_code: int
    timestamp: str          # ISO8601
    duration_ms: int

    def to_dict(self) -> dict[str, t.Any]: ...

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> RecordedInvocation: ...


@dc.dataclass(slots=True)
class FixtureMetadata:
    created_at: str         # ISO8601
    cmdmox_version: str
    platform: str
    python_version: str
    test_module: str | None = None
    test_function: str | None = None

    def to_dict(self) -> dict[str, t.Any]: ...

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> FixtureMetadata: ...

    @classmethod
    def create(
        cls,
        *,
        test_module: str | None = None,
        test_function: str | None = None,
    ) -> FixtureMetadata: ...


@dc.dataclass(slots=True)
class FixtureFile:
    version: str
    metadata: FixtureMetadata
    recordings: list[RecordedInvocation]
    scrubbing_rules: list[ScrubbingRule]

    def to_dict(self) -> dict[str, t.Any]: ...

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> FixtureFile: ...

    def save(self, path: Path) -> None: ...

    @classmethod
    def load(cls, path: Path) -> FixtureFile: ...
```

In `cmd_mox/record/session.py`:

```python
class RecordingSession:
    def __init__(
        self,
        fixture_path: Path | str,
        *,
        scrubber: Scrubber | None = None,
        env_allowlist: list[str] | None = None,
        command_filter: str | list[str] | None = None,
    ) -> None: ...

    def start(self) -> None: ...

    def record(
        self,
        invocation: Invocation,
        response: Response,
        *,
        duration_ms: int = 0,
    ) -> None: ...

    def finalize(self) -> FixtureFile: ...
```

In `cmd_mox/record/scrubber.py`:

```python
@dc.dataclass(slots=True)
class ScrubbingRule:
    pattern: str
    replacement: str
    applied_to: list[str] = dc.field(
        default_factory=lambda: ["env", "stdout", "stderr"]
    )
    description: str = ""

    def to_dict(self) -> dict[str, t.Any]: ...

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> ScrubbingRule: ...


class Scrubber(t.Protocol):
    def scrub(
        self, recording: RecordedInvocation
    ) -> RecordedInvocation: ...
```

In `cmd_mox/record/env_filter.py`:

```python
def filter_env_subset(
    env: dict[str, str],
    *,
    command: str = "",
    allowlist: list[str] | None = None,
    explicit_keys: list[str] | None = None,
) -> dict[str, str]: ...
```
