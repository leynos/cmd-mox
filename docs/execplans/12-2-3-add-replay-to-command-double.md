# Add `.replay()` to `CommandDouble`

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

Roadmap item `12.2.3` adds the fluent `.replay()` API to
`cmd_mox.test_doubles.CommandDouble` so recorded fixture files can be attached
directly to a double during test setup. After this change, a test author can
write `cmd_mox.spy("git").replay("fixtures/git.json")` and know that the
fixture is loaded immediately, the strict-versus-fuzzy matching mode is chosen
explicitly, and invalid configurations such as combining replay with
passthrough are rejected at configuration time rather than later during command
execution.

This work is intentionally narrower than roadmap items `12.2.4` and `12.2.5`.
`12.2.3` establishes the public fluent API and stores a ready-to-use
`ReplaySession` on the double. It does not yet teach `CmdMox._make_response()`
to consume replay sessions, and it does not yet extend `CmdMox.verify()` to
report unconsumed recordings. Those behaviours remain separate follow-on tasks
and must not be folded into this change unless the user explicitly expands the
scope.

Observable success after implementation:

1. `CommandDouble` exposes `.replay(fixture_path, *, strict=True) -> Self`.
2. Calling `.replay()` creates and loads a `ReplaySession` immediately, so
   missing files and schema errors surface during test setup.
3. `.replay()` rejects invalid combinations, at minimum replay plus
   passthrough, with clear error messages.
4. Unit tests written with `pytest` fail before implementation and pass after
   it, covering strict defaults, explicit fuzzy mode, validation, duplicate
   attachment protection, and path handling.
5. Behavioural tests written with `pytest-bdd` cover the consumer-visible
   fluent API contract for replay-enabled doubles.
6. `docs/python-native-command-mocking-design.md` records the final API
   decisions, `docs/usage-guide.md` explains the consumer-facing behaviour, and
   `docs/cmd-mox-roadmap.md` marks `12.2.3` done.
7. The full quality gates pass:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-3-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-3-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-3-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-3-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-3-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-3-test.log
```

## Repository orientation

The current implementation already has the replay infrastructure needed by this
API:

- `cmd_mox/record/replay.py` contains `ReplaySession`.
- `cmd_mox/record/matching.py` contains `InvocationMatcher`.
- `cmd_mox/record/__init__.py` already exports `ReplaySession`.

The missing piece is the fluent attachment point on the double:

- `cmd_mox/test_doubles.py` currently implements `.passthrough()` and
  `.record()`, carries `_recording_session` in `__slots__`, and exposes
  `has_recording_session` / `recording_session`.
- `cmd_mox/controller.py` already finalises recording sessions, but it does not
  yet consult replay sessions in `_make_response()` or verify replay
  consumption. That omission is expected at this roadmap stage.
- Existing tests for the neighbouring feature live in
  `cmd_mox/unittests/test_command_double_record.py`,
  `features/command_double_record.feature`,
  `tests/steps/command_double_record.py`, and
  `tests/test_command_double_record_bdd.py`. This task should mirror that
  structure for replay.

The implementation should therefore stay centred on `cmd_mox/test_doubles.py`
plus replay-specific tests and documentation.

## Constraints

- Follow the repository test-driven development workflow from `AGENTS.md`:
  write or update tests first, run them to confirm failure, then implement the
  code, then rerun the focused tests and full quality gates.
- Keep roadmap boundaries intact. `12.2.3` adds the fluent API only. Do not
  modify `CmdMox._make_response()` or `CmdMox.verify()` as part of this item.
- Do not change the public `ReplaySession` API introduced by `12.2.1` and
  refined by `12.2.2`.
- Do not change fixture schema semantics, `RecordedInvocation`, or matching
  rules. Strict versus fuzzy behaviour already belongs to `ReplaySession` and
  `InvocationMatcher`.
- Do not add new dependencies.
- Preserve existing public APIs unless this plan explicitly adds replay-related
  surface area. Existing `.record()` behaviour must remain unchanged.
- Keep unit tests under `cmd_mox/unittests/` and behavioural tests under
  `features/`, `tests/steps/`, and `tests/test_*_bdd.py`.
- Documentation changes must follow the existing style in `docs/`, including
  sentence-case headings and British English spellings where the surrounding
  text already uses them.
- Quality gates for completion are `make markdownlint`, `make nixie`,
  `make check-fmt`, `make typecheck`, `make lint`, and `make test`.

## Tolerances

- Scope: stop and escalate if `12.2.3` appears to require touching
  `cmd_mox/controller.py` beyond comments or imports. That is the clearest sign
  the work is spilling into `12.2.4` or `12.2.5`.
- API: stop and escalate if the change seems to require altering the signature
  of `ReplaySession.__init__()`, `ReplaySession.load()`, or existing
  `CommandDouble.record()`.
- Semantics: stop and escalate if replay must be supported on stubs or mocks to
  satisfy existing tests or documentation. This plan proceeds with the
  conservative assumption that replay is a spy-oriented fluent API, mirroring
  the design document examples.
- Testing: stop and escalate if behavioural coverage cannot be written against
  observable public behaviour without prematurely implementing controller
  replay integration.
- Quality gates: if any required gate still fails after 3 focused fix attempts,
  capture the failing command and log path in `Surprises & Discoveries` and
  pause for guidance.

## Risks

- Risk: the design documents show `spy("git").replay(...)`, but they do not
  state as plainly as they could whether replay is legal on stubs and mocks.
  Severity: medium. Likelihood: medium. Mitigation: implement the conservative
  spy-only interpretation, then record that decision explicitly in the design
  doc when the code lands.
- Risk: adding `.replay()` without controller integration can produce tests
  that look superficially incomplete if they expect end-to-end command replay.
  Severity: medium. Likelihood: high. Mitigation: keep the new tests focused on
  the fluent API contract and make the roadmap boundary explicit in docs and
  plan text.
- Risk: immediate fixture loading means `.replay()` will surface file and
  schema errors earlier than `.record()`. Severity: low. Likelihood: high.
  Mitigation: document this as intentional eager validation and cover it with
  unit tests.
- Risk: tests may reach into a private `_replay_session` slot if no public
  accessor exists. Severity: low. Likelihood: high. Mitigation: add read-only
  public query helpers mirroring recording-session access if that keeps tests
  and docs off private state.

## Progress

- [x] Reviewed `AGENTS.md`, the execplans skill, the roadmap, the record-mode
  design sections, and the existing `12.2.1` / `12.2.2` ExecPlans.
- [x] Inspected the current `CommandDouble`, `ReplaySession`, and neighbouring
  recording tests to establish the actual implementation seam.
- [x] Drafted this ExecPlan in
  `docs/execplans/12-2-3-add-replay-to-command-double.md`.
- [x] Obtain explicit user approval for this plan before implementation.
- [x] Add or update unit tests for `CommandDouble.replay()` before editing
  production code.
- [x] Add or update `pytest-bdd` scenarios covering the replay fluent API
  contract before editing production code.
- [x] Implement the replay session attachment API on `CommandDouble`.
- [x] Update the design doc, usage guide, and roadmap.
- [x] Run all required quality gates and capture the results in the plan if it
  moves into execution.

## Surprises & Discoveries

- `ReplaySession` and `InvocationMatcher` are already fully implemented and
  exported. The only missing public entrypoint is on `CommandDouble`, which
  makes this roadmap item a genuine API-wiring task rather than replay-engine
  work.
- `CommandDouble` currently mirrors recording state only: it has
  `_recording_session` in `__slots__`, initializes that slot in `__init__()`,
  and exposes public read-only recording-session helpers. There is no replay
  equivalent yet.
- The design document already contains illustrative code for
  `CommandDouble.replay()` and a `_replay_session` slot, so the docs are ahead
  of the shipped code in this area.
- `docs/usage-guide.md` currently explains `ReplaySession` directly but does
  not document a fluent `.replay()` method on doubles, which means this task
  has real user-facing documentation impact.
- `make markdownlint` scans `.uv-cache/` by default through the repo-wide
  `**/*.md` glob, so the markdownlint config needed an explicit `.uv-cache/**`
  ignore to avoid third-party package READMEs failing the gate.

## Decision Log

- Decision: keep `12.2.3` strictly focused on fluent API attachment. The double
  will gain replay configuration state, but controller dispatch and replay
  verification stay for `12.2.4` and `12.2.5`.

- Decision: use eager validation. `CommandDouble.replay()` should construct a
  `ReplaySession`, call `load()` immediately, and only return `self` once the
  fixture has been validated successfully. This matches the design document’s
  illustrative snippet and gives test authors immediate feedback when a fixture
  path or schema is wrong.

- Decision: reject replay plus passthrough explicitly with a `ValueError`.
  Replay is a fixture-backed substitute for real command execution, so allowing
  both at once would create contradictory behaviour.

- Decision: treat replay as a spy-oriented API unless implementation evidence
  proves otherwise. The design examples use `spy("...").replay(...)`, replayed
  invocations are still useful to record on spy history, and allowing replay on
  stubs or mocks would introduce public semantics the roadmap does not yet
  justify.

- Decision: add replay-session query helpers on `CommandDouble` if that keeps
  tests and usage documentation off private state. The preferred shape is to
  mirror the existing recording API with `has_replay_session` and
  `replay_session`. If the implementation turns out to be cleaner without these
  helpers, document that reversal here before landing the change.

## Plan of work

### Stage A: Write failing unit tests first

Create `cmd_mox/unittests/test_command_double_replay.py` and model it on the
existing recording tests. The file should establish the public API contract
before production code changes.

Write unit tests that cover at least the following behaviours:

1. `.replay()` returns the same `CommandDouble` instance for fluent chaining.
2. The default `strict` value is `True`.
3. Passing `strict=False` creates a replay session configured for fuzzy
   matching.
4. String paths are accepted and normalised to `Path`.
5. The attached replay session is loaded eagerly.
6. Missing fixture files and invalid fixture data raise from `.replay()`
   immediately.
7. Replay rejects invalid combinations:
   `passthrough().replay(...)` must raise `ValueError`.
8. Replay rejects duplicate attachment on the same double with a clear error.
9. If public replay-session helpers are added, cover their default and
   post-attachment values.

Run the focused unit test file before implementation and confirm it fails:

```bash
set -o pipefail
pytest cmd_mox/unittests/test_command_double_replay.py -q 2>&1 | tee /tmp/12-2-3-unit-red.log
```

### Stage B: Write failing behavioural tests first

Add consumer-facing behavioural coverage with:

- `features/command_double_replay.feature`
- `tests/steps/command_double_replay.py`
- `tests/test_command_double_replay_bdd.py`

These scenarios should stay at the fluent-API level because controller replay
dispatch is deliberately out of scope for `12.2.3`.

Minimum scenarios:

1. A spy can attach a replay fixture and the session is loaded with strict mode
   by default.
2. A spy can attach a replay fixture with `strict=False` and the replay session
   reports fuzzy matching.
3. Combining passthrough and replay raises `ValueError`.

Run the behavioural file before implementation and confirm it fails:

```bash
set -o pipefail
pytest tests/test_command_double_replay_bdd.py -q 2>&1 | tee /tmp/12-2-3-bdd-red.log
```

### Stage C: Implement `CommandDouble.replay()`

Update `cmd_mox/test_doubles.py` only after the red tests are in place.

Implementation steps:

1. Extend the TYPE_CHECKING imports to include `ReplaySession`.
2. Add `_replay_session` to `CommandDouble.__slots__`.
3. Initialise `self._replay_session` to `None` in `__init__()`.
4. Add `replay(self, fixture_path, *, strict=True) -> Self`.
5. Inside `replay()`, validate the intended constraints:
   - replay is not allowed when passthrough mode is already enabled
   - replay is not attached twice to the same double
   - if replay is kept spy-only, reject non-spy doubles with a clear error
6. Create `ReplaySession(fixture_path=Path(...), strict_matching=strict)`.
7. Call `load()` immediately so validation happens during setup.
8. Store the session on the double and return `self`.
9. If public query helpers are part of the implementation, add
   `has_replay_session` and `replay_session` properties mirroring the recording
   helpers.

After implementation, rerun the focused unit and behavioural tests until they
both pass.

### Stage D: Update documentation

Update `docs/python-native-command-mocking-design.md` so the normative and
illustrative sections match the shipped behaviour. At minimum:

1. Confirm Section `9.6.1` and Table `9.6.2` reflect the final `.replay()`
   signature and validation semantics.
2. Update Section `9.8.2` if the final implementation adds public replay query
   helpers or spy-only validation.
3. Add a new numbered decision under Section `9.10` recording any non-obvious
   choices made while implementing `.replay()`, especially:
   eager loading, spy-only scope if retained, and whether public replay-session
   accessors were added.

Update `docs/usage-guide.md` to describe the consumer-facing behaviour:

1. Add a short subsection near the existing Record Mode material showing
   `cmd_mox.spy("git").replay("fixtures/git.json")`.
2. Explain the `strict` flag in terms of strict versus fuzzy replay matching.
3. Explain that replay validates the fixture eagerly during setup.
4. Explain that replay cannot be combined with passthrough.
5. If public replay-session helpers are added, document them only if they are
   intended for users rather than just tests.

### Stage E: Mark the roadmap item complete

Once the code, tests, and docs are done, update `docs/cmd-mox-roadmap.md` to
change:

```plaintext
- [ ] 12.2.3. Add `.replay()` to `CommandDouble`, including passthrough
  incompatibility validation and strict-mode option.
```

to:

```plaintext
- [x] 12.2.3. Add `.replay()` to `CommandDouble`, including passthrough
  incompatibility validation and strict-mode option.
```

### Stage F: Run full quality gates

After the focused replay tests pass and all docs are updated, run the complete
repository gates:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-3-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-3-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-3-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-3-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-3-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-3-test.log
```

If any gate fails, fix the issue and rerun the failed command before rerunning
the full sequence if necessary.

## Validation and acceptance

The feature is complete when all of the following are true:

1. `cmd_mox.spy("git").replay("fixtures/git.json")` is a supported fluent API.
2. The attached `ReplaySession` is created and loaded during `.replay()`.
3. `strict=True` is the default and `strict=False` selects fuzzy replay mode.
4. Replay plus passthrough is rejected with a clear `ValueError`.
5. Duplicate replay attachment is rejected with a clear error.
6. If replay is implemented as spy-only, non-spy doubles are rejected with a
   clear error and the docs reflect that contract.
7. New `pytest` unit tests and `pytest-bdd` behavioural tests pass.
8. `docs/python-native-command-mocking-design.md` captures the final design
   decisions.
9. `docs/usage-guide.md` describes the user-visible replay behaviour.
10. `docs/cmd-mox-roadmap.md` marks `12.2.3` done.
11. `make markdownlint`, `make nixie`, `make check-fmt`, `make typecheck`,
    `make lint`, and `make test` all pass.

## Outcomes & Retrospective

- Shipped `CommandDouble.replay(fixture_path, *, strict=True)` with eager
  `ReplaySession.load()` during configuration, plus public read-only
  `has_replay_session` and `replay_session` helpers.
- Landed focused unit and behavioural coverage for fluent chaining, strict vs
  fuzzy mode, eager fixture validation, duplicate attachment rejection,
  passthrough incompatibility, and the current spy-only scope.
- Updated the usage guide, design document, and roadmap so the written contract
  matches the shipped API.
- Quality gates:
  - `make markdownlint`: passed
  - `make nixie`: passed
  - `make check-fmt`: passed
  - `make typecheck`: passed
  - `make lint`: passed
  - `make test`: passed (`720 passed, 12 skipped`)
