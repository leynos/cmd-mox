# Integrate replay into `CmdMox._make_response()`

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

Roadmap item `12.2.4` is the first point where replay fixtures stop being
configuration-only and start affecting live command execution. `12.2.3` already
lets a test author write
`cmd_mox.spy("git").replay("fixtures/git_clone.json")`, but the controller
still ignores the attached `ReplaySession` and continues using the spy's normal
response or handler. This task wires replay into
`cmd_mox.controller.CmdMox._make_response()` so replay-enabled spies actually
serve recorded responses during the replay phase.

The user-visible behaviour after implementation is:

1. A replay-enabled spy consults its attached `ReplaySession` before the normal
   spy response or handler path.
2. When the fixture contains a matching recording, the recorded
   `stdout` / `stderr` / exit code are returned and the invocation is still
   visible in the spy's history and the controller journal.
3. When strict replay is enabled and no recording matches, the invocation fails
   immediately with `UnexpectedCommandError` instead of silently falling
   through and only failing later during `verify()`.
4. When fuzzy replay is enabled and no recording matches, the controller falls
   back to the spy's existing response/handler behaviour.
5. Unit tests written with `pytest` fail before the code change and pass after
   it.
6. Behavioural tests written with `pytest-bdd` demonstrate the consumer-facing
   replay flow.
7. `docs/python-native-command-mocking-design.md` records the final controller
   integration decision, `docs/usage-guide.md` explains the new runtime
   behaviour, and `docs/cmd-mox-roadmap.md` marks `12.2.4` done.
8. The full quality gates pass:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-4-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-4-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-4-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-4-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-4-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-4-test.log
```

## Repository orientation

The implementation seam is concentrated in the controller, but the surrounding
pieces already exist:

- `cmd_mox/test_doubles.py` already provides the spy-only fluent
  `.replay(fixture_path, *, strict=True)` API and stores a loaded
  `ReplaySession` on `CommandDouble._replay_session`.
- `cmd_mox/record/replay.py` already loads fixtures, delegates matching to
  `InvocationMatcher`, marks recordings as consumed, and returns
  `Response | None` from `ReplaySession.match()`.
- `cmd_mox/controller.py` currently has no replay-aware branch in
  `_make_response()`. Its existing strategies are "missing double",
  "passthrough", and "regular". Spy invocation history is only updated inside
  `_response_for_regular()` and `_handle_passthrough_result()`.
- `docs/usage-guide.md` already documents `.replay()` setup, but it does not
  yet describe the controller's runtime replay behaviour because that behaviour
  is not implemented.
- `docs/python-native-command-mocking-design.md` already contains a draft
  controller integration sketch in section `9.8.3`, so this task should align
  the shipped code with that design and record any final clarifications.
- Existing behavioural controller coverage lives in
  `features/controller.feature`, `tests/steps/controller_setup.py`,
  `tests/steps/command_execution.py`, and `tests/test_controller_bdd.py`.
- Existing replay fixture helpers live in `tests/helpers/fixtures.py`.

The follow-on roadmap item `12.2.5` extends `CmdMox.verify()` to check for
unconsumed recordings. That verification work is explicitly out of scope here.

## Constraints

- Follow the repository test-driven development workflow from `AGENTS.md`:
  update tests first, confirm they fail, then implement the production change,
  then rerun focused tests and the full quality gates.
- Keep roadmap boundaries intact. `12.2.4` only integrates replay into
  `CmdMox._make_response()` and introduces the strict-unmatched failure path.
  Do not add replay-consumption verification to `CmdMox.verify()` in this
  change.
- Preserve the public APIs already shipped in `ReplaySession`,
  `InvocationMatcher`, and `CommandDouble.replay()`.
- Do not change fixture schema semantics, matching rules, or the meaning of
  strict versus fuzzy replay. Those were established by `12.2.1` through
  `12.2.3`.
- Replay-backed invocations must still populate the spy's `invocations` list
  and the controller journal so existing spy assertions remain meaningful.
- Strict replay misses must raise `UnexpectedCommandError` during invocation
  handling. Fuzzy replay misses must not raise solely because the fixture did
  not match; they must fall through to the spy's normal response/handler path.
- No new dependencies.
- Keep unit tests under `cmd_mox/unittests/` and behavioural tests under
  `features/`, `tests/steps/`, and `tests/test_*_bdd.py`.
- Update consumer-facing docs in `docs/usage-guide.md` and record any final
  design decisions in `docs/python-native-command-mocking-design.md`.
- Mark roadmap item `12.2.4` done only after the implementation and all
  quality gates succeed.
- Required gates for completion are `make markdownlint`, `make nixie`,
  `make check-fmt`, `make typecheck`, `make lint`, and `make test`.

## Tolerances

- Scope tolerance: stop and escalate if `12.2.4` appears to require extending
  `CmdMox.verify()` beyond comments or helper extraction. That is `12.2.5`.
- Controller tolerance: stop and escalate if replay integration cannot be
  implemented with a focused controller change plus tests and docs. If the work
  starts pulling in inter-process communication (IPC) protocol changes, shim
  protocol changes, or fixture schema changes, confirm direction before
  proceeding.
- Error-surface tolerance: unit tests should assert the exact
  `UnexpectedCommandError` type in-process. Behavioural tests should verify the
  user-visible failure mode exposed by the current transport. If surfacing the
  raw exception class through subprocess execution would require a protocol
  redesign, do not expand the scope to do that here.
- File-count tolerance: stop and escalate if the implementation needs more than
  10 touched files of Python code or more than 450 net new Python lines outside
  tests. This item should be controller wiring, not replay architecture churn.
- Quality-gate tolerance: if any required gate still fails after 3 focused fix
  attempts, capture the failing command and log file path in
  `Surprises & Discoveries` and pause for guidance.

## Risks

- Risk: the current controller strategy split does not include replay, so a
  naive early return can bypass spy bookkeeping. Mitigation: test and implement
  replay through a helper that appends to `double.invocations` before returning
  the matched `Response`.
- Risk: strict replay mismatch now fails at invocation time, which shifts one
  class of failure earlier than the rest of CmdMox's verification model.
  Mitigation: document that this is intentional and cover it in both unit tests
  and the usage guide.
- Risk: fuzzy replay mismatch semantics are easy to leave ambiguous. Mitigation:
  add a test that proves fuzzy replay falls back to the configured spy
  response/handler instead of raising.
- Risk: behavioural tests that go through the shim may observe a transport-level
  error string rather than a raw Python exception type. Mitigation: keep the
  exact exception-type assertions in unit tests and keep BDD expectations at
  the user-visible behaviour level.
- Risk: `12.2.5` will later add `verify_all_consumed()` calls. Mitigation:
  avoid any implementation shortcuts in `12.2.4` that would make replay
  sessions look "consumed" without actually going through
  `ReplaySession.match()`.

## Progress

- [x] Review `AGENTS.md`, the `execplans` skill, the record-mode roadmap
  entries, and the existing `12.2.1` to `12.2.3` ExecPlans.
- [x] Inspect the current controller, replay session, double API, usage guide,
  and controller BDD scaffolding.
- [x] Draft this ExecPlan in
  `docs/execplans/12-2-4-integrate-replay-into-cmd-mox-make-response.md`.
- [x] Obtain explicit user approval for this plan before implementation.
- [x] Add or update unit tests for controller replay integration before
  touching production code.
- [x] Add or update `pytest-bdd` scenarios that exercise replay-backed command
  execution behaviour.
- [x] Implement replay-aware response selection in `cmd_mox/controller.py`.
- [x] Update the design doc, usage guide, and roadmap.
- [x] Run all required quality gates and record the results in this document if
  the plan moves into execution.

## Surprises & Discoveries

- `CommandDouble.replay()` is already shipped and eagerly loads fixtures, so
  the missing work here is purely controller dispatch and its test coverage.
- `CmdMox._make_response()` currently handles only three cases: no double,
  passthrough spy, and regular stub/mock/spy handling. Replay is not modelled
  anywhere in that control flow.
- Spy bookkeeping is split across two places today:
  `_response_for_regular()` appends invocations for non-passthrough doubles,
  while `_handle_passthrough_result()` appends invocations and journal entries
  after real-command execution. A replay path must preserve equivalent
  bookkeeping.
- The controller journal is populated in `_handle_invocation()` after
  `_make_response()` returns. A strict replay mismatch that raises inside
  `_make_response()` will therefore leave no journal entry, which is the
  correct behaviour for an immediately-failed invocation.
- The design doc already sketches the desired `_make_response()` replay branch,
  but it does not explicitly call out spy invocation-history bookkeeping. This
  plan makes that requirement explicit.
- Focused red-phase test runs need
  `UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools`
  `uv run pytest ...` rather than plain `pytest`, because the repo does not put
  the virtualenv binaries directly on `PATH`.

## Decision Log

- Decision: implement replay as an explicit controller branch ahead of the
  existing "regular" response logic. The controller should check whether the
  resolved double has an attached replay session before selecting the normal
  response strategy.

- Decision: keep `ReplaySession.match()` as the single source of truth for
  replay matching and consumption. The controller must not duplicate matching
  logic or mutate replay-consumption state directly.

- Decision: when `ReplaySession.match()` returns a `Response`, treat the
  invocation as a normal spy call for bookkeeping purposes by appending the
  invocation to `double.invocations`. Journal appending can remain in
  `_handle_invocation()` because that path already runs for non-passthrough
  responses.

- Decision: when `ReplaySession.match()` returns `None` and the session is in
  strict mode, raise `UnexpectedCommandError` immediately with a message that
  includes the command and arguments. This makes strict replay mismatches fail
  at the actual invocation site rather than later during verification.

- Decision: when `ReplaySession.match()` returns `None` and the session is in
  fuzzy mode, fall through to the existing spy response/handler path. This
  keeps fuzzy replay additive rather than making it all-or-nothing.

- Decision: keep `CmdMox.verify()` unchanged in this roadmap item, aside from
  any comment updates needed to clarify that replay-consumption verification is
  still pending in `12.2.5`.

- Decision: keep replay handling as a dedicated helper branch in
  `cmd_mox/controller.py` rather than extending `_ResponseStrategy`. Replay is
  an overlay on top of the existing regular path, and the helper keeps the diff
  small while still making the precedence rules explicit.

## Plan of work

### Stage A: Write failing unit tests first

Create or extend a controller-focused unit test file under
`cmd_mox/unittests/`. The cleanest option is a dedicated file such as
`cmd_mox/unittests/test_controller_replay.py` so replay integration stays easy
to find and does not get buried in unrelated handler or passthrough tests.

Write tests that cover at least these behaviours:

1. A replay-enabled spy returns the fixture-backed `Response` for a matching
   invocation.
2. A matched replay invocation is appended to `spy.invocations`.
3. A matched replay invocation reaches the journal when driven through
   `_handle_invocation()`.
4. Strict replay mismatch raises `UnexpectedCommandError` immediately.
5. A strict replay mismatch does not append to `spy.invocations` or the
   journal.
6. Fuzzy replay mismatch falls back to the configured spy response or handler
   instead of raising.
7. A replay-backed match takes precedence over a configured spy
   `.returns(...)` or `.runs(...)` response.
8. A double without replay continues using the existing behaviour unchanged.

Use `tests/helpers/fixtures.py` where possible for valid fixture creation. Add
a small purpose-built fixture helper only if the existing `git status` fixture
is too narrow for the new controller cases.

Run the focused unit tests before implementation and confirm they fail:

```bash
set -o pipefail
pytest cmd_mox/unittests/test_controller_replay.py -q 2>&1 | tee /tmp/12-2-4-unit-red.log
```

If the tests live in an existing file instead, adjust the command accordingly
and record that change in `Decision Log`.

### Stage B: Write failing behavioural tests

Extend the consumer-visible controller behaviour through `pytest-bdd`. The
lowest-friction location is the existing controller feature suite:

- add replay scenarios to `features/controller.feature`;
- add any new step definitions in a focused file such as
  `tests/steps/controller_replay.py` or extend the existing controller step
  modules only where the new steps clearly belong;
- register the new scenarios in `tests/test_controller_bdd.py`.

Cover at least these scenarios:

1. A replay-enabled spy serves the recorded response during command execution.
2. A replay-backed invocation is visible through the spy's recorded call count
   or the controller journal after verification.
3. A strict replay mismatch fails during invocation handling rather than only
   during `verify()`.

Keep the BDD assertions consumer-facing. For example, a success-path scenario
should drive a real shimmed command and assert on its stdout and the
spy/journal state. For the strict-mismatch scenario, it is acceptable for the
step to exercise the controller directly if that is the most stable way to
assert the timing and error type without expanding the IPC protocol.

Run the focused behavioural coverage before implementation and confirm failure:

```bash
set -o pipefail
pytest tests/test_controller_bdd.py -k replay -q 2>&1 | tee /tmp/12-2-4-bdd-red.log
```

### Stage C: Implement replay-aware controller dispatch

Update `cmd_mox/controller.py` with the smallest change that makes the tests
pass cleanly and keeps the controller easy to read.

The recommended implementation shape is:

1. Add a small helper such as `_response_for_replay(...)` or equivalent inline
   logic in `_make_response()`.
2. Resolve the double from `self._doubles` as today.
3. If no double exists, keep the existing missing-double path unchanged.
4. If the double has an attached replay session:
   - call `double.replay_session.match(invocation)`;
   - when a `Response` is returned, append the invocation to
     `double.invocations` and return that response;
   - when `None` is returned and `strict_matching` is `True`, raise
     `UnexpectedCommandError` with a message that includes enough invocation
     detail to debug the mismatch;
   - when `None` is returned and `strict_matching` is `False`, fall through to
     the existing response logic.
5. Preserve the current passthrough and regular paths for all non-replay
   doubles.
6. Do not add `verify_all_consumed()` calls here.

After the code change, rerun the focused unit and behavioural tests:

```bash
set -o pipefail
pytest cmd_mox/unittests/test_controller_replay.py -q 2>&1 | tee /tmp/12-2-4-unit-green.log
```

```bash
set -o pipefail
pytest tests/test_controller_bdd.py -k replay -q 2>&1 | tee /tmp/12-2-4-bdd-green.log
```

### Stage D: Update documentation and roadmap

Update the docs in the same change so the shipped behaviour and the written
contract stay aligned.

In `docs/python-native-command-mocking-design.md`:

1. Confirm section `9.8.3` matches the final controller code shape.
2. Add a new decision entry after `9.10.14` capturing the final strict-miss and
   spy-bookkeeping rules if those details are not already covered elsewhere.

In `docs/usage-guide.md`:

1. Extend the `.replay()` section to explain that attached replay sessions are
   now consulted during live command execution.
2. Document the strict mismatch behaviour explicitly.
3. Document the fuzzy fallback behaviour explicitly so consumers know replay is
   best-effort in fuzzy mode.

In `docs/cmd-mox-roadmap.md`:

1. Change `12.2.4` from `[ ]` to `[x]` only after the implementation and all
   gates succeed.

### Stage E: Run the full quality gates

After the code and documentation changes are complete, run the full required
gates with `tee` logging:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-4-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-4-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-4-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-4-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-4-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-4-test.log
```

If any gate fails, fix the regression and rerun the affected command until all
six pass. Record noteworthy failures in `Surprises & Discoveries`.

## Validation and acceptance

The implementation is complete when all of the following are true:

1. `cmd_mox.spy("git").replay("fixture.json")` actually replays the fixture
   during command execution.
2. A strict replay mismatch raises `UnexpectedCommandError` at invocation time.
3. A fuzzy replay mismatch falls back to the configured spy response/handler.
4. Replay-backed spy calls are reflected in `spy.invocations` and in the
   controller journal.
5. Unit tests and behavioural tests covering the new behaviour pass.
6. `docs/usage-guide.md` describes the runtime replay behaviour for library
   consumers.
7. `docs/python-native-command-mocking-design.md` records the final controller
   integration decision.
8. `docs/cmd-mox-roadmap.md` marks `12.2.4` done.
9. `make markdownlint`, `make nixie`, `make check-fmt`, `make typecheck`,
   `make lint`, and `make test` all pass.

## Outcomes & Retrospective

Implementation completed successfully.

What shipped:

1. `CmdMox._make_response()` now consults an attached replay session before the
   normal spy response/handler path.
2. Matched replay invocations are recorded in `double.invocations`, and
   `_handle_invocation()` continues to record them in the controller journal.
3. Strict replay mismatches now raise `UnexpectedCommandError` immediately with
   a `"No fixture recording matches: ..."` message.
4. Fuzzy replay mismatches now fall back to the spy's configured
   response/handler path instead of raising.
5. The usage guide, design doc, roadmap, and this ExecPlan were updated to
   reflect the final behaviour.

Evidence:

- New unit coverage in `cmd_mox/unittests/test_controller_replay.py`
  exercises replay precedence, handler bypass, spy/journal bookkeeping, strict
  mismatch failure, and fuzzy fallback.
- New behavioural scenarios in `features/controller.feature` and
  `tests/steps/controller_replay.py` exercise replay-backed command execution,
  strict invocation-time failure, and fuzzy fallback behaviour.
- Focused green runs:

  ```plaintext
  5 passed in 0.04s
  6 passed, 28 deselected in 1.89s
  ```

- Full quality gates passed:

  ```plaintext
  make markdownlint
  Summary: 0 error(s)

  make nixie
  All diagrams validated successfully

  make check-fmt
  133 files already formatted

  make typecheck
  All checks passed!

  make lint
  All checks passed!

  make test
  729 passed, 12 skipped
  ```

Follow-on work remains in roadmap item `12.2.5`, which will extend
`CmdMox.verify()` to report unconsumed replay recordings.
