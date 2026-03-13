# Implement ReplaySession with fixture loading, schema validation, consumed-record tracking, and strict and fuzzy modes

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

Roadmap item 12.2.1 introduces the `ReplaySession` class to the cmd-mox record
module. This is the replay counterpart to the existing `RecordingSession`:
while `RecordingSession` captures passthrough invocations to fixture files,
`ReplaySession` loads those fixtures and uses them to respond to command
invocations without executing real commands. This bridges realistic integration
testing (passthrough) and fast, deterministic unit testing (mocked responses
from fixtures).

After this change a developer can load a previously recorded JSON fixture file,
match incoming command invocations against the recorded entries, track which
recordings have been consumed, and verify that all recordings were replayed.
Two matching modes are supported: strict (command + args + stdin + env_subset
must all match) and fuzzy (only command + args need match).

Observable success: new unit tests in
`cmd_mox/unittests/test_replay_session.py` and new behaviour-driven development
(BDD) scenarios in
`features/replay_session.feature` all pass. `make test`, `make lint`,
`make check-fmt`, `make typecheck`, `make markdownlint`, and `make nixie` all
succeed. `docs/usage-guide.md` documents the replay session API.
`docs/python-native-command-mocking-design.md` records design decisions. The
12.2.1 roadmap checkbox in `docs/cmd-mox-roadmap.md` is marked done.

## Constraints

- Python 3.12+ only (`from __future__ import annotations` in every file).
- Zero new external dependencies (JSON, threading, pathlib are all stdlib).
- Public API and data models follow the design spec Section 9.5.2 normative
  contract (field names, types, method signatures).
- `ReplaySession` is a plain class (not a dataclass), consistent with
  `RecordingSession`.
- Unit tests colocated in `cmd_mox/unittests/`; BDD features in `features/`;
  step defs in `tests/steps/`; BDD runners in `tests/test_*_bdd.py`.
- Tests written before implementation (AGENTS.md test-driven development (TDD)
  workflow).
- Existing tests must not break.
- No modifications to `CommandDouble`, `CmdMox`, `PassthroughCoordinator`, or
  `_make_response()` in this plan. Those are roadmap items 12.2.3, 12.2.4, and
  12.2.5 respectively.
- `InvocationMatcher` as a separate class is out of scope (roadmap 12.2.2).
  Matching logic lives as private methods inside `ReplaySession` for now.
- Quality gates: `make check-fmt`, `make typecheck`, `make lint`, `make test`,
  `make markdownlint`, `make nixie` must all pass.
- Documentation style guide: British English (Oxford), sentence-case headings,
  `-` bullets, fenced code blocks with language identifiers.
- Import conventions: `import datetime as dt`, `import dataclasses as dc`,
  `import typing as t`. TYPE_CHECKING block for type-only imports.

## Tolerances (exception triggers)

- Scope: if implementation requires modifying more than 12 files or 600 net
  new lines (excluding tests), stop and escalate.
- Interface: if any existing public API signature must change, stop and
  escalate.
- Dependencies: if a new external dependency is required, stop and escalate.
- Iterations: if quality gates still fail after 3 targeted fix attempts, stop
  and escalate with captured logs.
- Ambiguity: if the design spec is ambiguous on a normative point, document
  the interpretation in Decision Log and proceed with the conservative choice.

## Risks

- Risk: The design doc (Section 9.4) places `ReplaySession` in `session.py`
  alongside `RecordingSession`, but separate modules keep each session type
  focused. Severity: low. Likelihood: certain. Mitigation: Document the
  deviation in Decision Log. The class interface (Section 9.5.2) is normative;
  the module structure is guidance.

- Risk: `make typecheck` baseline is 0 diagnostics. New code must not add
  diagnostics. Severity: medium. Likelihood: medium. Mitigation: Run typecheck
  early, use explicit type annotations, prefer isinstance() over callable() for
  type narrowing.

- Risk: Thread safety for `_consumed` set access. Severity: low.
  Likelihood: low (replay is typically single-threaded). Mitigation: Use
  `threading.Lock()` around `_consumed` mutation, following `RecordingSession`
  threading pattern.

## Progress

- [x] Write ExecPlan to `docs/execplans/12-2-1-implement-replay-session.md`.
- [x] Write unit tests for `ReplaySession` (red phase).
- [x] Write BDD feature file and step definitions (red phase).
- [x] Implement `ReplaySession` in `cmd_mox/record/replay.py` (green phase).
- [x] Update `cmd_mox/record/__init__.py` with `ReplaySession` export.
- [x] Confirm all tests pass (green phase).
- [x] Update `docs/usage-guide.md` with replay session documentation.
- [x] Record design decisions in
  `docs/python-native-command-mocking-design.md`.
- [x] Mark 12.2.1 as done in `docs/cmd-mox-roadmap.md`.
- [x] Run full quality gates. Fix any issues.
- [x] Update ExecPlan to COMPLETE.

## Surprises & discoveries

- The `_make_fixture_file` helper used `recordings or [default]` which
  treated an empty list as falsy, creating a fixture with one recording instead
  of zero. Fixed with explicit `is None` check.
- BDD step definitions using `parsers.parse` receive escape sequences like
  `\n` as literal two-character strings. Required
  `.encode("utf-8").decode("unicode_escape")` to decode the expected value.
- RUF043 (ruff 0.12.7) flags `pytest.raises(match="...")` patterns containing
  regex metacharacters like `.` without raw strings or `re.escape()`.
- `make markdownlint` has a pre-existing katex/Node.js infrastructure issue
  (`__VERSION__` not defined); running `markdownlint-cli2` directly on changed
  files confirms 0 markdown lint errors.

## Decision log

- **Module placement**: Place `ReplaySession` in a new
  `cmd_mox/record/replay.py` module rather than in `session.py` alongside
  `RecordingSession`. Each session type has distinct responsibilities and
  lifecycle semantics. Separate modules improve readability. The public import
  path via `cmd_mox/record/__init__.py` is unaffected.

- **Matching logic**: Implement matching as private methods inside
  `ReplaySession` (`_matches_strict`, `_matches_fuzzy`) rather than a separate
  `InvocationMatcher` class. `InvocationMatcher` is roadmap item 12.2.2. Simple
  first-unconsumed-match logic suffices for 12.2.1. When 12.2.2 is implemented,
  the methods can be extracted without changing the public API.

- **Error types**: Use existing `VerificationError` for unconsumed-recordings
  verification failures. The existing hierarchy is sufficient. A finer-grained
  subclass (e.g., `UnconsumedRecordingError`) can be introduced later if needed.

- **Explicit load()**: `load()` is not called from `__init__()`. This matches
  the `RecordingSession` lifecycle pattern (construct, then start/load) and
  improves testability.

- **Return type**: `match()` returns `Response | None`, not
  `RecordedInvocation`. The caller needs a `Response` object; converting inside
  `match()` encapsulates the mapping. Confirmed by design spec 9.5.2.

- **Env_subset matching**: Uses subset containment semantics, not exact
  equality. A recorded `env_subset` captures only relevant env vars. The live
  `Invocation.env` has the full process environment. Every key-value pair in
  `env_subset` must be present in `invocation.env`; extra keys are ignored.
  This mirrors `.with_env()` matching elsewhere in CmdMox.

## Outcomes & retrospective

- 5 new files created, 4 existing files modified.
- 38 new tests: 33 unit tests + 5 BDD scenarios. All pass.
- Full suite: 676 passed, 12 skipped, 0 failed.
- `make check-fmt`: pass. `make lint`: pass. `make typecheck`: 0 diagnostics.
- `make nixie`: pass. Markdown lint on changed files: 0 errors.
- `ReplaySession` API matches the design spec Section 9.5.2 normative
  contract exactly. Three design decisions recorded in Section 9.10.
- Roadmap item 12.2.1 marked done. Usage guide updated with new section.
- TDD workflow followed: tests written first, implementation second.

## Plan of work

### Stage A: Unit tests (red phase)

Create `cmd_mox/unittests/test_replay_session.py` with comprehensive unit tests
covering all `ReplaySession` behaviour. Tests import from
`cmd_mox.record.replay` (the module does not exist yet). 33 unit tests across 8
test classes.

### Stage B: BDD tests (red phase)

Create `features/replay_session.feature` (5 scenarios),
`tests/steps/replay_session.py` (step definitions), and
`tests/test_replay_session_bdd.py` (BDD runner).

### Stage C: Implementation (green phase)

Create `cmd_mox/record/replay.py` with the `ReplaySession` class. Update
`cmd_mox/record/__init__.py` to export `ReplaySession`. Confirm all tests pass.

### Stage D: Documentation and roadmap

Update `docs/usage-guide.md` with a "Replay sessions" section. Record design
decisions in the design doc. Mark 12.2.1 as done in the roadmap.

### Stage E: Quality gates

Run all quality gates. Fix any issues.

## Validation and acceptance

The change is accepted when:

- `make test` passes with all new tests green (~33 unit + 5 BDD).
- `make lint`, `make check-fmt`, `make typecheck` all pass (0 new
  diagnostics).
- `make markdownlint`, `make nixie` pass.
- `docs/usage-guide.md` contains a "Replay sessions" section with examples.
- `docs/cmd-mox-roadmap.md` item 12.2.1 is marked `[x]`.
- `docs/python-native-command-mocking-design.md` contains new design
  decisions.
- Fixture loading correctly handles v1.0 and migrated older versions.
- Strict matching rejects mismatches on command, args, stdin, or env_subset.
- Fuzzy matching matches on command + args only.
- Consumed tracking prevents double-matching.
- `verify_all_consumed()` raises `VerificationError` with unconsumed details.
- Thread safety: concurrent `match()` calls consume distinct indices.
