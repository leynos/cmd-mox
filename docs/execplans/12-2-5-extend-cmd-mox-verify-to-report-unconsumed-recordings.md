# Extend `CmdMox.verify()` to report unconsumed recordings

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

Roadmap item `12.2.5` closes the main correctness gap that remains after
`12.2.4`. Replay-backed spies already serve fixture responses during command
execution, but `cmd_mox.controller.CmdMox.verify()` still succeeds even when a
fixture contains recordings that were never consumed. That leaves a silent
failure mode: a test can claim to be replaying a real interaction while only
using part of the captured fixture.

After this change, a developer who attaches a replay fixture to a spy and never
uses all of its recorded entries will see `CmdMox.verify()` fail with a clear
`VerificationError`. A fully consumed fixture will continue to verify cleanly.
Direct `ReplaySession` users keep their existing opt-out via
`allow_unmatched=True`, and replay-related cleanup must still run even when
verification fails.

Observable success after implementation:

1. A replay-backed spy that leaves fixture entries unused causes
   `CmdMox.verify()` to raise `VerificationError` with the existing
   replay-session diagnostic text.
2. A replay-backed spy that consumes all fixture entries continues to verify
   successfully.
3. The implementation reuses `ReplaySession.verify_all_consumed()` rather than
   duplicating consumption logic inside the controller.
4. `CmdMox.verify()` still tears down the IPC server and finalizes recording
   sessions even when replay-consumption verification fails.
5. Unit tests written with `pytest` fail before the code change and pass after
   it.
6. Behavioural tests written with `pytest-bdd` demonstrate the consumer-facing
   verify-time failure.
7. `docs/python-native-command-mocking-design.md` records the final ordering
   and error-surface decision, `docs/usage-guide.md` explains the new
   verify-time behaviour for replay fixtures, and `docs/cmd-mox-roadmap.md`
   marks `12.2.5` done after the feature lands.
8. The full quality gates pass:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-5-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-5-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-5-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-5-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-5-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-5-test.log
```

## Repository orientation

The implementation seam is now very narrow because the replay machinery already
exists:

- `cmd_mox/controller.py`
  - `CmdMox.verify()` currently runs `_run_verifiers()` and then always calls
    `_finalize_verification()` and `_finalize_recording_sessions()` in a
    `finally` block.
  - `CmdMox._run_verifiers()` currently verifies unexpected commands, ordering,
    and mock call counts only.
  - `CmdMox._response_for_replay()` already consumes recordings through
    `ReplaySession.match()` and raises `UnexpectedCommandError` for strict
    replay misses.
- `cmd_mox/record/replay.py`
  - `ReplaySession.verify_all_consumed()` already implements the exact
    consumption check needed by this roadmap item, including the
    `allow_unmatched` escape hatch and the diagnostic message format.
- `cmd_mox/test_doubles.py`
  - `CommandDouble` already exposes `has_replay_session` and `replay_session`,
    which is enough for controller-side iteration without adding new public API.
- `cmd_mox/unittests/test_controller_replay.py`
  - already covers replay dispatch in `_make_response()` and is the natural
    home for controller-level replay-verification tests.
- `cmd_mox/unittests/test_controller_lifecycle.py`
  - already covers the rule that `verify()` must still clean up when later
    finalization steps raise.
- `features/controller.feature`, `tests/test_controller_bdd.py`,
  `tests/steps/controller_setup.py`, and `tests/steps/controller_replay.py`
  already provide controller-level behavioural coverage and reusable step
  definitions for verify-time errors.
- `docs/python-native-command-mocking-design.md`
  - already says that `verify()` should call
    `ReplaySession.verify_all_consumed()`, but it does not yet pin down the
    final controller ordering relative to existing verifiers and teardown.
- `docs/usage-guide.md`
  - already documents replay fixtures, strict versus fuzzy replay, and direct
    `ReplaySession(..., allow_unmatched=True)` usage, but it does not yet state
    that `CmdMox.verify()` enforces replay-consumption completeness.

The neighbouring roadmap items matter here:

- `12.2.4` is complete and already handles runtime replay matching.
- `12.2.6` remains a follow-on item for broader replay infrastructure coverage.
  This plan should only add tests needed to validate `12.2.5` itself.

## Relevant docs and skills

The implementer should keep these references open while executing the plan:

- Docs:
  - `docs/cmd-mox-roadmap.md`
  - `docs/python-native-command-mocking-design.md`, especially Section IX,
    `9.5.2`, and `9.8.3`
  - `docs/usage-guide.md`, especially the replay-session and `.replay()`
    sections
  - `docs/execplans/12-2-3-add-replay-to-command-double.md`
  - `docs/execplans/12-2-4-integrate-replay-into-cmd-mox-make-response.md`
- Skills:
  - `execplans` for keeping this document current during implementation
  - `leta` for symbol-aware navigation of `CmdMox.verify()`,
    `_run_verifiers()`, and the replay-session tests

## Constraints

- The completed implementation kept the roadmap boundary intact. `12.2.5`
  only extends controller verification to report unconsumed replay recordings;
  fixture schema, matching rules, replay attachment API, and
  strict-versus-fuzzy runtime matching behaviour remain unchanged.
- `ReplaySession.verify_all_consumed()` remains the single source of truth for
  consumption checks, and `CmdMox` delegates unconsumed-recording detection to
  that API.
- Preserve existing cleanup guarantees in `CmdMox.verify()`: IPC teardown and
  recording-session finalization must still run even if replay-consumption
  verification fails.
- The test and documentation updates stayed within the existing project
  layout: unit tests under `cmd_mox/unittests/`, behavioural tests under
  `features/`, `tests/steps/`, and `tests/test_*_bdd.py`, consumer-facing docs
  in `docs/usage-guide.md`, and design notes in
  `docs/python-native-command-mocking-design.md`.
- The completed feature passed `make markdownlint`, `make nixie`,
  `make check-fmt`, `make typecheck`, `make lint`, and `make test`.

## Tolerances

- Scope tolerance: stop and escalate if the work appears to require new replay
  public API, fixture-format changes, or `CmdMox._make_response()` behaviour
  changes. Those belong to other roadmap items.
- Ordering tolerance: stop and escalate if replay-consumption verification
  cannot be added with a focused controller helper or a small extension to
  `_run_verifiers()`. This item should not require a wide controller refactor.
- Error-precedence tolerance: preserve the current broad `verify()` error
  contract unless tests prove a regression. Do not opportunistically redesign
  how earlier verification errors interact with later cleanup errors without
  explicit approval.
- Test-surface tolerance: prefer extending
  `cmd_mox/unittests/test_controller_replay.py`,
  `cmd_mox/unittests/test_controller_lifecycle.py`, and
  `features/controller.feature` over creating new controller-specific test
  files. Stop and reassess if coverage seems to need a large new scaffold.
- Quality-gate tolerance: if any required gate still fails after 3 focused fix
  attempts, capture the failing command and log path in
  `Surprises & Discoveries` and pause for guidance.

## Risks

- Risk: it is easy to place replay-consumption verification in the wrong part
  of `CmdMox.verify()`, either skipping cleanup on failure or letting teardown
  happen before the verification check. Mitigation: write an ordering-focused
  unit test before changing controller code.
- Risk: the controller already has one cleanup regression test for recording
  finalization errors, but not for replay-verification failures. Mitigation:
  add a red-phase test that proves environment cleanup still happens when
  replay verification raises.
- Risk: `.replay()` does not expose `allow_unmatched`, so there is no fluent
  library-level opt-out for consumers using the common spy API. Mitigation:
  keep scope limited; document the current behaviour clearly and only mention
  `allow_unmatched=True` in the direct `ReplaySession` section where it already
  exists.
- Risk: existing replay-backed controller scenarios may start failing once
  verification tightens. Mitigation: treat the existing successful replay BDD
  scenario as an implicit regression test for the fully consumed path, then add
  one new scenario for the unconsumed path.
- Risk: replay-consumption verification may be tempted to inspect private state
  directly. Mitigation: iterate doubles via the public `replay_session`
  property and delegate the actual check to `verify_all_consumed()`.

## Progress

- [x] Reviewed `AGENTS.md`, project notes, the `execplans` skill, and the
  `leta` skill instructions relevant to this task.
- [x] Inspected the roadmap, design doc, usage guide, existing replay ExecPlans,
  and the current controller and replay-session seams.
- [x] Drafted this ExecPlan in
  `docs/execplans/12-2-5-extend-cmd-mox-verify-to-report-unconsumed-recordings.md`.
- [x] User approved implementation by requesting execution of this plan.
- [x] Add or update unit tests for replay-consumption verification before
  touching production code.
- [x] Add or update `pytest-bdd` scenarios covering the verify-time failure
  before touching production code.
- [x] Implement controller-side replay-consumption verification in
  `cmd_mox/controller.py`.
- [x] Update the design doc, usage guide, and roadmap.
- [x] Run all required quality gates and record the results in this document.

## Surprises & Discoveries

- `ReplaySession.verify_all_consumed()` already exists and already formats the
  exact `VerificationError` message this roadmap item needs. The missing work
  is controller integration, not replay-engine design.
- `CmdMox._run_verifiers()` is the narrowest safe insertion point. Extending it
  preserves the existing `verify()` cleanup structure, which already guarantees
  both finalizers run and that the first failure wins.
- `CmdMox.verify()` currently runs replay teardown before recording-session
  finalization, but replay-consumption verification is absent entirely. The
  implementation must add the check without regressing cleanup.
- Existing fuzzy replay fallback behaviour remains unchanged at invocation time,
  but `verify()` now fails afterward if the fallback path left the fixture
  recording unused. The affected BDD scenario had to move from verify-success
  to verify-failure expectations.
- Existing BDD infrastructure already supports
  `When I verify the controller expecting an VerificationError` and
  `Then the verification error message should contain "..."`, so this feature
  should need only a small scenario addition rather than new plumbing.
- The current usage guide already documents `allow_unmatched=True` for direct
  `ReplaySession` use. The plan should preserve that documentation path rather
  than inventing a new `.replay(..., allow_unmatched=...)` API.
- Focused red/green test runs need
  `UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools uv run pytest ...` rather than
  plain `pytest`, because the repo does not put the test runner directly on
  `PATH`.
- Red-phase confirmation:
  - `cmd_mox/unittests/test_controller_replay.py::TestControllerReplayVerification.test_verify_raises_when_replay_fixture_has_unconsumed_recordings`
    failed with `Failed: DID NOT RAISE <class 'cmd_mox.errors.VerificationError'>`.
  - `cmd_mox/unittests/test_controller_lifecycle.py::test_verify_cleans_up_when_replay_consumption_verification_raises`
    failed with the same missing-error assertion.
  - `tests/test_controller_bdd.py::test_replay_backed_spy_fails_verification_when_fixture_recordings_remain_unused`
    failed in `verify_controller_expect_error()` with the same missing-error
    assertion.

## Decision Log

- Decision: verify replay consumption in the controller by calling
  `ReplaySession.verify_all_consumed()` for each attached replay session after
  the existing mock/order/unexpected-command verifiers and before teardown
  finalizers. Rationale: this keeps replay consumption as part of verification
  rather than cleanup, while still letting the existing `finally` block run.

- Decision: keep `ReplaySession.verify_all_consumed()` as the single source of
  truth for unconsumed-recording semantics, including the
  `allow_unmatched=True` branch and the diagnostic message shape. Rationale:
  this avoids semantic drift between direct `ReplaySession` usage and
  controller-driven replay.

- Decision: integrate replay-consumption verification into
  `CmdMox._run_verifiers()` rather than adding a separate top-level
  verification phase in `CmdMox.verify()`. Rationale: the check is semantically
  part of verification, and this placement preserves the existing teardown and
  error-precedence behaviour with the smallest controller change.

- Decision: keep fuzzy replay fallback behaviour at invocation time and surface
  incomplete fixture consumption only during `verify()`. Rationale: runtime
  fallback remains useful for additive replay scenarios, while verify-time
  enforcement still prevents silent partial-fixture tests.

- Decision: preserve the current public API. `12.2.5` should not add new
  fluent options to `.replay()` or new replay configuration objects. Rationale:
  the roadmap item is controller verification, not API expansion.

- Decision: treat the existing controller replay success scenarios as the
  consumed-path regression tests, and add one explicit controller scenario for
  the unconsumed-path failure. Rationale: the feature needs behavioural
  coverage, but the happy path is already exercised end-to-end.

- Decision: mark `docs/cmd-mox-roadmap.md` item `12.2.5` done after the
  implementation landed in the working tree and all required quality gates
  passed. Rationale: the roadmap status now reflects completed, verified
  feature work rather than the earlier planning draft.

## Plan of work

### Stage A: Add failing unit tests first

Extend `cmd_mox/unittests/test_controller_replay.py` with controller-level
verification tests that demonstrate the missing behaviour before production
code changes. Keep these tests focused on user-visible controller outcomes, not
on private replay-session internals.

Add at least these unit tests:

1. A test where a spy attaches a replay fixture, no invocation consumes the
   fixture, and `mox.verify()` raises `VerificationError` containing
   `Not all fixture recordings were consumed during replay`.
2. A test where a replay-backed spy consumes the fixture and `mox.verify()`
   succeeds, proving the new verification does not break the happy path.
3. A test where a replay session is attached with `allow_unmatched=True` and
   `mox.verify()` does not raise for unused fixture entries. If the cleanest
   setup requires constructing `ReplaySession` directly and assigning it to the
   double in the test, document that in the test comment rather than expanding
   production API.
4. A cleanup regression test, likely in
   `cmd_mox/unittests/test_controller_lifecycle.py`, proving that a
   `VerificationError` from replay-consumption verification still leaves
   `EnvironmentManager.get_active_manager()` as `None`, sets `mox._entered` to
   `False`, and leaves the controller in `Phase.VERIFY` after `verify()`
   returns or raises.

Run a focused red-phase command and confirm the new or modified tests fail for
the expected reason:

```bash
set -o pipefail
UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools uv run pytest \
  cmd_mox/unittests/test_controller_replay.py \
  cmd_mox/unittests/test_controller_lifecycle.py \
  2>&1 | tee /tmp/12-2-5-unit-red.log
```

Record the observed failing assertion briefly in `Surprises & Discoveries` once
implementation begins.

### Stage B: Add one behavioural scenario for the verify-time failure

Extend `features/controller.feature` with a new scenario that proves the
consumer-visible failure mode. Keep it minimal:

1. Create a `CmdMox` controller.
2. Create a replay fixture.
3. Attach the fixture to a spy.
4. Enter replay mode but do not run the command.
5. Verify the controller expecting `VerificationError`.
6. Assert that the verification message contains the unconsumed-recordings
   text.

Prefer reusing existing steps in `tests/steps/controller_setup.py` and
`tests/steps/assertions.py`. Only add a new step in
`tests/steps/controller_replay.py` if the scenario needs replay-specific setup
that is not already expressed clearly.

Run a focused behavioural red-phase command:

```bash
set -o pipefail
UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools uv run pytest \
  tests/test_controller_bdd.py -k "replay or verify" \
  2>&1 | tee /tmp/12-2-5-bdd-red.log
```

### Stage C: Implement controller verification with minimal surface area

Modify `cmd_mox/controller.py` with the smallest possible change set.

Preferred shape:

1. Add a private helper such as `_verify_replay_sessions_consumed()` that
   iterates `self._doubles.values()`.
2. For each double whose `replay_session` property is not `None`, call
   `replay_session.verify_all_consumed()`.
3. Call that helper from `CmdMox.verify()` after `_run_verifiers()` succeeds
   and before entering the `finally` block's teardown finalizers, or fold it
   into `_run_verifiers()` if that is measurably cleaner. Either placement is
   acceptable so long as cleanup still runs on failure and the plan’s tests
   pass.
4. Do not inspect `ReplaySession._consumed` or any other private replay state
   from the controller.
5. Do not alter `_response_for_replay()` or replay matching rules unless a test
   demonstrates a direct regression in `12.2.5`.

Run the focused unit and behavioural suites again until they pass:

```bash
set -o pipefail
UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools uv run pytest \
  cmd_mox/unittests/test_controller_replay.py \
  cmd_mox/unittests/test_controller_lifecycle.py \
  tests/test_controller_bdd.py \
  2>&1 | tee /tmp/12-2-5-focused-green.log
```

### Stage D: Update documentation and roadmap

Update the documentation immediately after the code and tests settle:

- `docs/python-native-command-mocking-design.md`
  - record the final placement of replay-consumption verification inside the
    controller lifecycle and any deliberate decision about error precedence.
- `docs/usage-guide.md`
  - explain that `CmdMox.verify()` now enforces full fixture consumption for
    replay-backed spies and point readers to `allow_unmatched=True` in the
    direct `ReplaySession` section when tolerant verification is desired.
- `docs/cmd-mox-roadmap.md`
  - mark `12.2.5` checked to reflect the completed implementation and passing
    quality gates.

The final design rules discovered during implementation are recorded in
`Decision Log`.

### Stage E: Run the full quality gates

Run the full repository gates with `tee` and `pipefail`, then inspect the logs
if anything fails:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-5-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-5-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-5-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-5-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-5-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-5-test.log
```

All six commands succeeded, `Progress` records completion,
`Outcomes & Retrospective` captures the final verification summary, and roadmap
item `12.2.5` is marked done.

## Outcomes & Retrospective

`CmdMox.verify()` now enforces full replay-fixture consumption by calling a new
controller helper, `_verify_replay_sessions_consumed()`, from
`CmdMox._run_verifiers()`. That helper iterates attached replay sessions via
the existing `CommandDouble.replay_session` property and delegates the actual
check to `ReplaySession.verify_all_consumed()`, preserving the existing
cleanup-first `finally` behaviour in `verify()`.

The shipped test coverage proves both behaviour and cleanup:

- Focused red/green coverage was added in
  `cmd_mox/unittests/test_controller_replay.py`,
  `cmd_mox/unittests/test_controller_lifecycle.py`,
  `features/controller.feature`, and `tests/test_controller_bdd.py`.
- The new tests cover verify-time failure for unused recordings, successful
  verification after full consumption, the direct `allow_unmatched=True`
  opt-out path, and cleanup after replay-consumption verification raises.
- An existing fuzzy replay BDD scenario was updated to reflect the final
  contract: fuzzy mismatch still falls back at invocation time, then `verify()`
  raises if the fixture recording remained unused.

Documentation was updated in `docs/usage-guide.md` and
`docs/python-native-command-mocking-design.md`, and roadmap item `12.2.5` is
now marked complete in `docs/cmd-mox-roadmap.md`.

Final gate results:

- `make markdownlint`: passed
- `make nixie`: passed
- `make check-fmt`: passed
- `make typecheck`: passed
- `make lint`: passed
- `make test`: passed (`738 passed, 12 skipped`)

Lesson for follow-on replay work: fuzzy replay is now explicitly a two-stage
contract. Invocation-time matching may be permissive, but verify-time fixture
consumption remains strict unless a caller opts into `allow_unmatched=True`
through direct `ReplaySession` construction.
