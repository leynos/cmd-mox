# Add `.record()` Method to CommandDouble

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / Big Picture

Roadmap item XII-A requires a fluent `.record()` method on `CommandDouble` so
that passthrough spy invocations are automatically captured to reusable JSON
fixture files. After this change a developer can write:

```python
spy = mox.spy("git").passthrough().record("fixtures/git.json")
```

and every passthrough invocation of `git` will be recorded to the fixture file
when `verify()` runs. The recording infrastructure (`RecordingSession`,
`FixtureFile`, environment filtering, `Scrubber` protocol) already exists in
`cmd_mox/record/`; this plan wires it into the public fluent API.

Observable success: `mox.spy("git").passthrough().record(path)` creates and
starts a `RecordingSession`; `PassthroughCoordinator.finalize_result()` feeds
each completed passthrough result into the double's recording session;
`CmdMox.verify()` finalizes all recording sessions and writes fixture files to
disk. New unit tests in `cmd_mox/unittests/test_command_double_record.py` and
`cmd_mox/unittests/test_passthrough_record.py` pass, a new BDD scenario in
`features/command_double_record.feature` passes, and all quality gates succeed.

## Constraints

- Python 3.12+ only (`from __future__ import annotations` in every file).
- Zero new external dependencies.
- Public API follows the design spec Section 9.8.2 normative contract: method
  signature `record(fixture_path, *, scrubber=None, env_allowlist=None)`.
- `CommandDouble` uses `__slots__`; the new `_recording_session` attribute must
  be added to the slots tuple.
- Recording integration lives in `PassthroughCoordinator.finalize_result()`
  per the roadmap's explicit requirement (coordinator-based, not
  controller-only).
- Session finalization must occur before environment teardown in
  `_finalize_verification()`.
- Existing tests must not break.
- Tests written before implementation (AGENTS.md TDD workflow).
- Quality gates: `make check-fmt`, `make typecheck`, `make lint`, `make test`
  must all pass.

## Tolerances (Exception Triggers)

- Scope: if implementation requires modifying more than 10 files or 400 net new
  lines (excluding tests), stop and escalate.
- Interface: if any existing public API signature must change, stop and
  escalate.
- Dependencies: if a new external dependency is required, stop and escalate.
- Iterations: if quality gates still fail after 3 targeted fix attempts, stop
  and escalate with captured logs.
- Ambiguity: if the design spec is ambiguous on a normative point, document the
  interpretation in Decision Log and proceed with the conservative choice.

## Risks

- Risk: The `Scrubber` type is a Protocol in `cmd_mox/record/scrubber.py` with
  no concrete implementation yet (XII-C). Tests need a mock scrubber. Severity:
  low. Likelihood: certain. Mitigation: Define a trivial test-only scrubber
  class in the test file (same pattern used in `test_recording_session.py`).

- Risk: `make typecheck` may flag `double._recording_session` access in
  `passthrough.py` because `_recording_session` is not visible to the type
  checker from `TYPE_CHECKING` imports. Severity: medium. Likelihood: medium.
  Mitigation: Add `_recording_session` to the `CommandDouble` class with proper
  type annotation; use `hasattr()` guard or direct attribute access since the
  slot is always initialized.

- Risk: Thread safety in `PassthroughCoordinator.finalize_result()` — the
  recording call happens after releasing the lock. Severity: low. Likelihood:
  low. Mitigation: The recording session's `record()` method operates on
  per-double state that is not shared across threads. The double is removed
  from `_pending` atomically, so no concurrent finalization of the same
  invocation is possible.

## Progress

- [x] (2026-03-01) Write ExecPlan.
- [x] (2026-03-01) Write unit tests for `CommandDouble.record()` (red phase).
  11 tests in `test_command_double_record.py`.
- [x] (2026-03-01) Write unit tests for `PassthroughCoordinator` recording
  integration (red phase). 3 tests in `test_passthrough_record.py`.
- [x] (2026-03-01) Write BDD feature file, step definitions, and scenario
  bindings (red phase). 2 scenarios in `command_double_record.feature`.
- [x] (2026-03-01) Implement `CommandDouble.record()` in
  `cmd_mox/test_doubles.py`.
- [x] (2026-03-01) Implement recording in
  `PassthroughCoordinator.finalize_result()`.
- [x] (2026-03-01) Implement session finalization in `cmd_mox/controller.py`.
- [x] (2026-03-01) Confirm all tests pass (green phase). 631 passed, 12
  skipped.
- [x] (2026-03-01) Update `docs/usage-guide.md` with `.record()` fluent API
  documentation.
- [x] (2026-03-01) Update `docs/cmd-mox-roadmap.md` — mark XII-A items done.
- [x] (2026-03-01) Update `docs/python-native-command-mocking-design.md` — add
  decision 9.10.9.
- [x] (2026-03-01) Run full quality gates and fix any issues.

## Surprises & Discoveries

- Observation: The existing `_FakeDouble` test double in
  `cmd_mox/unittests/test_passthrough.py` does not carry `_recording_session`,
  causing `AttributeError` when `finalize_result()` accessed
  `double._recording_session` directly. Evidence: Test failure on first run:
  `AttributeError: '_FakeDouble' object has no attribute '_recording_session'`.
  Impact: Used `getattr(double, "_recording_session", None)` in the coordinator
  for defensive access, which is appropriate since the coordinator uses a
  `TYPE_CHECKING`-only import for `CommandDouble` and tests use cast fakes.

- Observation: The `make fmt` command reformatted a pre-existing file
  (`docs/execplans/7-1-2-fixture-file.md`), causing an MD032 markdown lint
  error where a line starting with a dash was merged into a paragraph.
  Evidence: `markdownlint-cli2` reported MD032 on line 443 of the file. Impact:
  Fixed by rewording the affected paragraph to avoid leading dash.

## Decision Log

- Decision: Integrate recording into `PassthroughCoordinator.finalize_result()`
  by inspecting `double._recording_session` rather than adding a constructor
  parameter to the coordinator. Rationale: Recording sessions are per-double
  (each spy can record to a different fixture file), so a single session
  parameter on the shared coordinator does not fit. The coordinator already
  stores the double in its pending dict and returns it from
  `finalize_result()`, so it can inspect the double's session attribute
  directly. This follows the roadmap requirement to integrate recording into
  `finalize_result()` while supporting per-double sessions cleanly.
  Date/Author: 2026-03-01 / DevBoxer agent.

- Decision: Call `RecordingSession.start()` immediately inside
  `CommandDouble.record()` rather than deferring to first invocation.
  Rationale: The session is ready as soon as it is created — there is no reason
  to defer `start()`. Eager start avoids the need for a "started?" guard in the
  coordinator's recording path and ensures the `_started_at` timestamp reflects
  when the user configured recording, not when the first command happened to
  execute. Date/Author: 2026-03-01 / DevBoxer agent.

- Decision: Finalize recording sessions in `CmdMox.verify()` after
  `_run_verifiers()` but before `_finalize_verification()`. Rationale:
  `_finalize_verification()` calls `__exit__()` which tears down the
  environment. Fixture files must be written before cleanup. Placing
  finalization between verifiers and teardown ensures fixtures are persisted
  even if verification raises (by wrapping in a try/finally). Date/Author:
  2026-03-01 / DevBoxer agent.

## Outcomes & Retrospective

### Outcomes

All acceptance criteria met:

- `make test`: 631 passed, 12 skipped. 16 new tests (11 CommandDouble unit + 3
  coordinator unit + 2 BDD scenarios).
- `make lint`: All checks passed.
- `make check-fmt`: All checks passed.
- `make typecheck`: All checks passed (ty 0.0.19, 0 diagnostics).
- `make markdownlint`: 0 errors in project files (1 pre-existing in
  `.uv-cache/`).
- `make nixie`: All diagrams validated.
- `docs/usage-guide.md`: Contains "Automatic recording with `.record()`"
  section with fluent API docs and examples.
- `docs/cmd-mox-roadmap.md`: XII-A items all marked `[x]`.
- `docs/python-native-command-mocking-design.md`: Decision 9.10.9 added.
- Fluent API validated: `spy("git").passthrough().record(path)` creates a
  started session; `spy("git").record(path)` raises `ValueError`.

### Retrospective

- The TDD workflow was effective. Writing tests first caught the `_FakeDouble`
  compatibility issue immediately.
- Using `getattr()` for defensive access in the coordinator was the right call;
  it avoids coupling the coordinator to `CommandDouble`'s internal attributes
  while still supporting recording.
- Net new files: 6. Net modified existing files: 6 (including the pre-existing
  fixture file fixup). Well within the 10-file tolerance.
- The implementation is minimal: 3 source file edits totaling ~40 lines of
  production code. The bulk of the work was tests and documentation.
- Post-merge fix: the original `verify()` placed `_finalize_recording_sessions()`
  and `_finalize_verification()` sequentially in the same `finally` block. If
  recording finalization raised (e.g., `OSError` from an unwritable fixture
  path), `_finalize_verification()` was skipped, leaking IPC server state and
  environment mutations. Fixed by wrapping `_finalize_recording_sessions()` in
  its own `try/except` so `_finalize_verification()` always runs. A
  `threading.Lock` was also added to `RecordingSession.record()` to serialize
  concurrent sequence assignment from multiple IPC threads.

## Context and Orientation

### Project Overview

CmdMox is a Python-native command mocking library. It intercepts external
command invocations via PATH-based shim scripts and an IPC bus, providing
stub/mock/spy test doubles. The project follows a record-replay-verify
lifecycle.

### Key Files

- `cmd_mox/test_doubles.py` — `CommandDouble` class with `__slots__`, fluent
  API methods (`returns()`, `runs()`, `passthrough()`, `with_args()`, etc.),
  and spy assertions. Uses `DoubleKind` enum (STUB, MOCK, SPY). Current slots:
  `controller`, `expectation`, `handler`, `invocations`, `kind`, `name`,
  `passthrough_mode`, `response`.

- `cmd_mox/passthrough.py` — `PassthroughCoordinator` with thread-safe pending
  dict mapping `invocation_id → (double, invocation, deadline)`.
  `prepare_request()` stores pending entries; `finalize_result()` pops them,
  builds a `Response`, and calls `invocation.apply(resp)`.

- `cmd_mox/controller.py` — `CmdMox` controller with `verify()` →
  `_run_verifiers()` → `_finalize_verification()` flow.
  `_handle_passthrough_result()` calls
  `self._passthrough_coordinator.finalize_result(result)`, then appends to
  `double.invocations` and `self.journal`.

- `cmd_mox/record/session.py` — `RecordingSession` with lifecycle:
  `start()` → `record(invocation, response, *, duration_ms=0)` → `finalize()`.
  Handles env filtering and optional scrubbing.

- `cmd_mox/record/scrubber.py` — `Scrubber` Protocol with `scrub()` method;
  `ScrubbingRule` dataclass.

- `cmd_mox/record/__init__.py` — Public re-exports for the record subpackage.

- `docs/python-native-command-mocking-design.md` Section 9.8.2 — Normative
  spec for `CommandDouble.record()` method signature.

- `docs/cmd-mox-roadmap.md` lines 244-259 — XII-A checklist items to mark
  done.

- `docs/usage-guide.md` lines 339-443 — Existing "Recording sessions" section
  to extend.

### Existing Test Patterns

Unit tests live in `cmd_mox/unittests/test_*.py`. They use `pytest.raises`,
`@dataclass` test configs, and test classes grouped by concern. BDD tests use
`features/*.feature` Gherkin files, step definitions in `tests/steps/*.py`, and
thin binding files in `tests/test_*_bdd.py` that import steps via
`from tests.steps.X import *`.

### Build Targets

- `make test` — `pytest -v -n auto`
- `make lint` — `ruff check`
- `make check-fmt` — `ruff format --check` + `mdformat --check`
- `make typecheck` — `ty`
- `make fmt` — auto-format
- `make markdownlint` — markdown lint
- `make nixie` — mermaid diagram validation

## Plan of Work

### Stage A: Unit Tests (Red Phase)

Write all unit tests against the not-yet-implemented `.record()` method and
coordinator recording hook. Tests should fail because `CommandDouble` has no
`record()` method and `PassthroughCoordinator.finalize_result()` does not
record.

Create `cmd_mox/unittests/test_command_double_record.py` with ~9 tests
covering: fluent return value, `ValueError` when passthrough not enabled (spy
without passthrough, stub, mock), `RecordingSession` creation, immediate
`start()`, parameter forwarding (scrubber, env_allowlist), and
`has_recording_session` property.

Create `cmd_mox/unittests/test_passthrough_record.py` with ~3 tests covering:
coordinator records to double's session when present, skips when absent, and
recorded data is correct.

Go/no-go: existing tests still pass; new tests fail with `AttributeError` on
`record` or similar.

### Stage B: BDD Tests (Red Phase)

Create `features/command_double_record.feature` with 2 scenarios: successful
fluent API recording session creation, and `ValueError` on record without
passthrough.

Create `tests/steps/command_double_record.py` with step definitions.

Create `tests/test_command_double_record_bdd.py` with scenario bindings.

Go/no-go: existing tests still pass; new BDD tests fail.

### Stage C: Implementation (Green Phase)

Modify `cmd_mox/test_doubles.py`: add `_recording_session` to `__slots__`,
initialize to `None` in `__init__()`, add `has_recording_session` property, add
`record()` method.

Modify `cmd_mox/passthrough.py`: in `finalize_result()`, after building the
response and calling `invocation.apply(resp)`, check if
`double._recording_session is not None` and if so call
`double._recording_session.record(invocation, resp)`.

Modify `cmd_mox/controller.py`: add `_finalize_recording_sessions()` helper,
call it in `verify()` after `_run_verifiers()` but before
`_finalize_verification()`.

Go/no-go: all tests pass (both existing and new).

### Stage D: Documentation and Roadmap

Update `docs/usage-guide.md` with new subsection documenting the `.record()`
fluent API.

Update `docs/cmd-mox-roadmap.md` to mark remaining XII-A items as done.

Update `docs/python-native-command-mocking-design.md` with decision 9.10.9.

### Stage E: Quality Gates

Run `make check-fmt`, `make typecheck`, `make lint`, `make test`,
`make markdownlint`. Fix any issues. Re-run until all pass.

## Concrete Steps

1. Create `cmd_mox/unittests/test_command_double_record.py` with unit tests.
2. Create `cmd_mox/unittests/test_passthrough_record.py` with coordinator
   recording tests.
3. Run `make test` — confirm new tests fail, existing tests pass.
4. Create `features/command_double_record.feature`.
5. Create `tests/steps/command_double_record.py`.
6. Create `tests/test_command_double_record_bdd.py`.
7. Run `make test` — confirm new BDD tests fail, existing tests pass.
8. Edit `cmd_mox/test_doubles.py` — add slot, property, and `record()` method.
9. Edit `cmd_mox/passthrough.py` — add recording hook in `finalize_result()`.
10. Edit `cmd_mox/controller.py` — add `_finalize_recording_sessions()` and
    call from `verify()`.
11. Run `make test` — confirm all tests pass.
12. Update `docs/usage-guide.md`.
13. Update `docs/cmd-mox-roadmap.md`.
14. Update `docs/python-native-command-mocking-design.md`.
15. Run `make check-fmt && make typecheck && make lint && make test`.
16. Fix any issues, re-run gates.
17. Update ExecPlan to COMPLETE.

## Validation and Acceptance

The change is accepted when all of the following are true:

- `make test` passes with all new tests green (~14 new tests: 9 unit for
  CommandDouble, 3 unit for coordinator, 2 BDD scenarios).
- `make lint` passes with no new warnings.
- `make check-fmt` passes.
- `make typecheck` does not introduce new diagnostics beyond the pre-existing
  baseline (currently 0).
- `docs/usage-guide.md` contains a section documenting the `.record()` fluent
  API with example code.
- `docs/cmd-mox-roadmap.md` XII-A items are all checked.
- `docs/python-native-command-mocking-design.md` contains decision 9.10.9.
- `spy("git").passthrough().record(path)` creates a started
  `RecordingSession`; `spy("git").record(path)` raises `ValueError`.

## Idempotence and Recovery

All steps are safe to re-run. Test files can be overwritten. Source file edits
are additive (new slots, new methods, new code paths). No existing code is
deleted.

If a quality gate fails: fix only the reported issue, re-run the failing gate,
then re-run all gates before marking complete.

## Artifacts and Notes

### New files created

- `cmd_mox/unittests/test_command_double_record.py`
- `cmd_mox/unittests/test_passthrough_record.py`
- `features/command_double_record.feature`
- `tests/steps/command_double_record.py`
- `tests/test_command_double_record_bdd.py`

### Existing files modified

- `cmd_mox/test_doubles.py` — add `_recording_session` slot, property, and
  `record()` method
- `cmd_mox/passthrough.py` — add recording hook in `finalize_result()`
- `cmd_mox/controller.py` — add `_finalize_recording_sessions()` and call from
  `verify()`
- `docs/usage-guide.md` — new subsection for `.record()` API
- `docs/cmd-mox-roadmap.md` — mark XII-A items done
- `docs/python-native-command-mocking-design.md` — add decision 9.10.9

## Interfaces and Dependencies

No new external dependencies.

### New public interface

In `cmd_mox/test_doubles.py`:

```python
class CommandDouble:
    _recording_session: RecordingSession | None

    @property
    def has_recording_session(self) -> bool:
        """Return True if a recording session is attached."""
        ...

    def record(
        self,
        fixture_path: str | Path,
        *,
        scrubber: Scrubber | None = None,
        env_allowlist: list[str] | None = None,
    ) -> Self:
        """Enable recording of passthrough invocations to a fixture file."""
        ...
```

### Modified internal interface

In `cmd_mox/passthrough.py`:

```python
class PassthroughCoordinator:
    def finalize_result(
        self, result: PassthroughResult
    ) -> tuple[CommandDouble, Invocation, Response]:
        """Finalize passthrough, optionally record, and return results."""
        # ... existing logic ...
        # NEW: if double._recording_session is not None, record
        ...
```

In `cmd_mox/controller.py`:

```python
class CmdMox:
    def _finalize_recording_sessions(self) -> None:
        """Finalize all active recording sessions and persist fixtures."""
        ...
```
