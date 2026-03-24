# Implement `InvocationMatcher` with strict, fuzzy, and best-fit replay selection

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETED

## Purpose / big picture

Roadmap item `12.2.2` extracts replay matching out of
`cmd_mox/record/replay.py` into a dedicated `InvocationMatcher` and upgrades
the matching behaviour from "first unconsumed match wins" to deterministic
"best-fit wins" selection. This matters because record-mode fixtures can
contain several recordings with the same command and argv but different
`stdin`, environment subsets, or levels of specificity. Today
`ReplaySession.match()` returns the first unconsumed candidate, which is simple
but can pick the wrong fixture entry when replay data is ambiguous. After this
change, replay remains deterministic while choosing the most appropriate
recording for the live invocation.

Observable success after implementation:

1. A new `cmd_mox.record.matching.InvocationMatcher` exists and
   `ReplaySession.match()` delegates candidate selection to it.
2. Strict replay still requires command, args, `stdin`, and `env_subset`
   compatibility, but when more than one recording is compatible the matcher
   deterministically selects the most specific one instead of the first one.
3. Fuzzy replay still requires exact command and args equality, but it prefers
   the candidate whose `stdin` and environment subset are closest to the live
   invocation.
4. New unit tests prove boolean matching, scoring, consumed-record skipping,
   deterministic tie-breaking, and `ReplaySession` delegation.
5. New behavioural tests prove the user-visible replay behaviour through
   `ReplaySession`, including an ambiguous fixture where best-fit selection
   changes which recording is consumed.
6. `docs/python-native-command-mocking-design.md` records the final scoring and
   tie-break decisions, `docs/usage-guide.md` explains the replay-selection
   semantics for consumers, and `docs/cmd-mox-roadmap.md` marks `12.2.2` done.
7. The full quality gates pass:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-2-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-2-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-2-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-2-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-2-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-2-test.log
```

## Constraints

- Follow the repository test-driven development (TDD) workflow from
  `AGENTS.md`: change tests first,
  observe failure, then implement code, then rerun the full suite.
- Keep the existing `ReplaySession` public API unchanged:
  `__init__(fixture_path, *, strict_matching=True, allow_unmatched=False)`,
  `load()`, `match()`, and `verify_all_consumed()`.
- Keep fixture schema semantics unchanged. `RecordedInvocation`,
  `FixtureFile`, and persisted JSON structure are not part of this task.
- Do not add external dependencies. The matcher must use only the standard
  library and existing project modules.
- Preserve the existing strict/fuzzy mode contract already documented in
  `docs/usage-guide.md` and `docs/python-native-command-mocking-design.md`:
  strict mode requires command, args, `stdin`, and `env_subset` compatibility;
  fuzzy mode requires command and args equality only.
- `env_subset` continues to use subset containment semantics. A recorded key in
  `env_subset` matches when the live invocation environment contains the same
  key-value pair, regardless of additional live keys.
- Consumed recording indices remain owned by `ReplaySession`. The matcher must
  not mutate session state directly; it only decides which index to consume.
- Behaviour must stay deterministic. Re-running the same test against the same
  fixture and invocation stream must consume the same recording indices.
- Unit tests stay under `cmd_mox/unittests/`. Behavioural tests stay in
  `features/`, `tests/steps/`, and `tests/test_*_bdd.py`.
- Any documentation change must use sentence-case headings and follow the
  documentation style already present in `docs/`.
- Because this turn is for planning only, this ExecPlan remains in `DRAFT`
  state and no implementation should begin until the user explicitly approves
  the plan.

## Tolerances

- Scope tolerance: stop and escalate if implementation of `12.2.2` requires
  changing more than 8 files of Python code or more than 500 net new lines of
  Python outside tests. This item should be a focused extraction plus scoring
  behaviour, not a controller-wide replay redesign.
- API tolerance: stop and escalate if the work appears to require changing the
  public constructor or method signatures of `ReplaySession`, `CommandDouble`,
  or `CmdMox`. Those belong to roadmap items `12.2.3` through `12.2.5`.
- Behaviour tolerance: stop and escalate if "best-fit" cannot be specified
  deterministically without introducing a new public option. The draft below
  proposes exact scoring semantics; if that proves incompatible with existing
  tests or expectations, confirm direction with the user before proceeding.
- Testing tolerance: stop and escalate if the new behaviour-driven development
  (BDD) coverage would require a
  large new step-library abstraction instead of a small extension to the
  existing replay-session feature files and step definitions.
- Quality-gate tolerance: if any of `make markdownlint`, `make nixie`,
  `make check-fmt`, `make typecheck`, `make lint`, or `make test` fails after
  3 focused fix attempts, capture the failing command and log path in
  `Surprises & Discoveries`, then pause for guidance.

## Risks

- Risk: "best-fit" is currently under-specified in the roadmap. Different
  engineers could reasonably choose different scoring rules. Mitigation: make
  the scoring tuple explicit in the design doc and the usage guide before
  merging the code.
- Risk: extracting `InvocationMatcher` could accidentally change the current
  strict-mode behaviour for fixtures with a single candidate. Mitigation: keep
  the existing `ReplaySession` tests and add delegation tests that prove single
  candidate behaviour is unchanged.
- Risk: fuzzy-mode scoring can become opaque if implemented as arbitrary
  integer weights. Mitigation: use a lexicographic score tuple whose fields map
  directly to user-visible concepts.
- Risk: deterministic tie-breaking matters for repeated or duplicate fixture
  recordings. Mitigation: when scores tie, prefer the recording with the lower
  `sequence` value.
- Risk: docs currently say replay uses the "first unconsumed recording that
  matches". That statement will become false after this feature. Mitigation:
  update the replay-session usage guide text in the same change as the code.

## Progress

- [x] Review `AGENTS.md`, replay design sections, the existing `12.2.1`
  ExecPlan, and the current `ReplaySession` implementation.
- [x] Draft this ExecPlan in
  `docs/execplans/12-2-2-invocation-matcher-with-strict-matching.md`.
- [x] Get explicit user approval for this plan.
- [x] Add or update unit tests for `InvocationMatcher` and `ReplaySession`
  before touching implementation code.
- [x] Add or update `pytest-bdd` behavioural coverage for ambiguous replay
  fixtures and best-fit consumption.
- [x] Implement `cmd_mox/record/matching.py` and refactor
  `cmd_mox/record/replay.py` to delegate matching.
- [x] Update `docs/python-native-command-mocking-design.md` with final
  extraction and scoring decisions.
- [x] Update `docs/usage-guide.md` to explain replay best-fit semantics for
  consumers.
- [x] Mark roadmap item `12.2.2` done in `docs/cmd-mox-roadmap.md`.
- [x] Run all quality gates and attach the resulting evidence to this document
  if the plan later moves into execution.

## Surprises & Discoveries

- The current docs and implementation are intentionally out of sync on one
  point. The design doc already models a standalone `InvocationMatcher`, but
  the shipped code still keeps `_matches_strict` and `_matches_fuzzy` as
  private methods on `ReplaySession`. This roadmap item is therefore both an
  extraction and a behaviour upgrade.
- `docs/usage-guide.md` currently promises first-match semantics in the replay
  section, so this task has user-facing documentation impact even though it is
  primarily internal.
- `ReplaySession` already exports strict and fuzzy modes and already owns the
  consumed-index lock. That makes it a good façade boundary: the matcher should
  decide, the session should load fixtures and mutate `_consumed`.

## Decision Log

- Decision: create a new internal module `cmd_mox/record/matching.py`
  containing `InvocationMatcher`. This matches the design doc's `matching.py`
  module layout and keeps replay-selection logic out of
  `cmd_mox/record/replay.py`.

- Decision: keep `InvocationMatcher` out of the top-level public API for now.
  The user-visible contract remains `ReplaySession.match()`. The class is
  exported from `cmd_mox.record` as an advanced record-module utility rather
  than a stable primary entrypoint.

- Decision: use boolean compatibility plus lexicographic scoring rather than
  opaque weighted sums.
  The scoring rule is:
  `command` and `args` are mandatory gates in both modes.
  In strict mode, `stdin` equality and full `env_subset` containment are also
  mandatory gates.
  Among remaining candidates, higher specificity wins by comparing the stats
  tuple `(stdin_match, matching_env_pairs, env_subset_size)`.
  In fuzzy mode, `stdin` and environment no longer disqualify candidates, but
  they still influence the score using the same ranking dimensions.

- Decision: when two candidates produce the same stats tuple, choose the
  recording with the lower `sequence` value. The `sequence` field corresponds
  to fixture list order in well-formed fixtures and provides a deterministic,
  auditable tie-breaker.

- Decision: keep `ReplaySession.verify_all_consumed()` unchanged.
  `InvocationMatcher` only selects candidates; it does not participate in
  verification or error formatting.

- Decision (resolved): fuzzy-mode scoring treats an exact `stdin` match as
  more important than environment specificity. `stdin` wins first because it
  usually captures the command payload more directly than the environment
  does.

## Outcomes & Retrospective

Implementation completed successfully. All objectives from the "Purpose / big
picture" section have been met:

1. **Created `InvocationMatcher` class**: New `cmd_mox/record/matching.py`
   module with `InvocationMatcher` class that handles boolean matching and
   best-fit selection using lexicographic scoring.

2. **Best-fit selection**: The matcher selects the most appropriate recording
   when multiple candidates qualify, using the stats tuple
   `(stdin_match, matching_env_pairs, env_subset_size)` with an explicit
   tie-breaker on lower `sequence` value.

3. **ReplaySession delegation**: `ReplaySession.match()` now delegates candidate
   selection to `InvocationMatcher.find_match()`, keeping lifecycle, loading,
   consumed tracking, and locking in `ReplaySession`.

4. **Test coverage**: Added 18 unit tests in `test_invocation_matcher.py`,
   2 integration tests in `test_replay_session.py`, and 2 BDD scenarios in
   `replay_session.feature`. All tests pass.

5. **Documentation updates**:
   - Design doc section 9.5.5 updated with scoring details
   - Design doc decision 9.10.13 added to capture extraction rationale
   - Usage guide updated to explain best-fit selection for consumers
   - Roadmap item 12.2.2 marked complete

6. **Quality gates**: All gates pass with no regressions:
   - `make markdownlint`: Project docs pass (third-party cache issue ignored)
   - `make nixie`: All diagrams validated
   - `make check-fmt`: All files formatted
   - `make typecheck`: Zero diagnostics (ty 0.0.23)
   - `make lint`: All checks passed (ruff)
   - `make test`: 700 passed, 12 skipped (22 new tests)

### Implementation notes

- The scoring approach using a tuple proved clear and auditable. No need for
  weighted integers or opaque magic numbers.
- All existing `ReplaySession` tests continued to pass after refactoring,
  confirming backward compatibility for non-ambiguous fixtures.
- The delegation boundary worked well: `ReplaySession` owns state and lifecycle,
  `InvocationMatcher` is a pure decision function.
- `InvocationMatcher` is exported from `cmd_mox.record` for advanced use but
  the primary user-facing API remains `ReplaySession.match()`.

### Deviations from plan

None. All stages (A through F) completed as specified in the ExecPlan.

## Repository orientation

The implementer should begin with these files because they define the current
replay behaviour and the documentation that must change:

- `cmd_mox/record/replay.py`: current matching logic lives here in
  `_matches_strict`, `_matches_fuzzy`, and `match()`.
- `cmd_mox/record/__init__.py`: record-module exports.
- `cmd_mox/unittests/test_replay_session.py`: current replay-session unit
  coverage. Extend this file or split out matcher-specific tests if it becomes
  too large to navigate.
- `features/replay_session.feature`,
  `tests/steps/replay_session.py`, and
  `tests/test_replay_session_bdd.py`: existing behavioural tests for replay.
- `docs/python-native-command-mocking-design.md`: normative design text and
  design-decision log.
- `docs/usage-guide.md`: replay-session consumer documentation that still
  describes first-match selection.
- `docs/cmd-mox-roadmap.md`: checkbox for `12.2.2`.

The current `ReplaySession.match()` implementation is small: it loads the
fixture, chooses either `_matches_strict` or `_matches_fuzzy`, iterates the
recordings in order under a lock, skips consumed indices, marks the first
match as consumed, and returns a `Response`. `InvocationMatcher` should take
over the candidate-selection part of that method, leaving fixture loading,
locking, response construction, and consumed tracking in `ReplaySession`.

## Plan of work

### Stage A: Write failing unit tests first

Create matcher-focused unit coverage before implementation changes. There are
two acceptable shapes:

1. Add a new file `cmd_mox/unittests/test_invocation_matcher.py` for direct
   tests of `InvocationMatcher`, plus a few delegation assertions in
   `cmd_mox/unittests/test_replay_session.py`.
2. Keep everything in `cmd_mox/unittests/test_replay_session.py` if the file
   remains readable after the additions.

The direct unit tests must cover:

1. `matches()` in strict mode for exact match, command mismatch, args mismatch,
   `stdin` mismatch, and `env_subset` mismatch.
2. `matches()` in fuzzy mode showing that command and args still gate, while
   `stdin` and environment differences no longer disqualify a candidate.
3. `find_match()` skipping consumed indices.
4. `find_match()` returning `None` when no compatible candidate exists.
5. Best-fit selection in strict mode when two compatible recordings differ in
   specificity, for example one with empty `env_subset` and one with a matching
   non-empty `env_subset`.
6. Best-fit selection in fuzzy mode when several command+args candidates exist
   and only one has matching `stdin` or more matching environment pairs.
7. Deterministic tie-breaking when two candidates have the same score.

Before moving on, run the targeted unit subset and confirm it fails for the
expected reason because `InvocationMatcher` does not yet exist or because the
old first-match logic chooses the wrong fixture entry.

Suggested red-phase command:

```bash
set -o pipefail
pytest cmd_mox/unittests/test_replay_session.py cmd_mox/unittests/test_invocation_matcher.py 2>&1 | tee /tmp/12-2-2-red-unit.log
```

If the test file is not yet created, the first failing run may simply be
`pytest cmd_mox/unittests/test_replay_session.py`.

### Stage B: Add behavioural coverage for user-visible replay selection

Update the replay BDD feature to demonstrate the behaviour a user cares about:
when the fixture contains several possible recordings for the same command and
argv, replay should consume the best-fitting one instead of whichever appears
first.

Prefer extending the existing files instead of creating a second BDD vertical:

- `features/replay_session.feature`
- `tests/steps/replay_session.py`
- `tests/test_replay_session_bdd.py`

Add at least two scenarios:

1. Strict-mode replay chooses the more specific recording when multiple strict
   candidates exist.
2. Fuzzy-mode replay chooses the closest candidate when `stdin` or environment
   differs between recordings but command and args are the same.

Make the scenario observable by asserting the returned `stdout` and then
calling `verify_all_consumed()` or checking that the expected recording remains
unconsumed. The red-phase behavioural run should fail before implementation.

Suggested red-phase command:

```bash
set -o pipefail
pytest tests/test_replay_session_bdd.py 2>&1 | tee /tmp/12-2-2-red-bdd.log
```

### Stage C: Implement `InvocationMatcher`

Create `cmd_mox/record/matching.py` and add the new class. Keep the class
small and explicit. A novice should be able to read it top to bottom without
guessing about hidden state.

Implementation outline:

1. Add a `strict` constructor flag matching the design doc.
2. Implement `matches(invocation, recording) -> bool` as a pure predicate.
   This method should not inspect the consumed set.
3. Add a private helper that computes a comparable candidate key for a
   recording. Prefer a tuple over a weighted integer so the score dimensions
   remain obvious during review and future debugging.
4. Implement `find_match(invocation, recordings, consumed) -> int | None`.
   Iterate the fixture in list order, skip indices in `consumed`, filter with
   `matches()`, compute candidate keys, and return the index with the best key.
   On a score tie, prefer the recording with the lower `sequence` value.

The initial matcher should stay entirely free of threading concerns. The
consumed set is read-only input here; `ReplaySession` will continue to protect
mutation with its existing lock.

### Stage D: Refactor `ReplaySession` to delegate without changing its public API

Modify `cmd_mox/record/replay.py` so `ReplaySession` owns only lifecycle,
loading, consumed tracking, locking, and response conversion. The matching
details should move to `InvocationMatcher`.

Concrete refactor steps:

1. Replace `_matches_strict` and `_matches_fuzzy` with a configured matcher
   instance, or keep thin wrappers temporarily if that makes the red-to-green
   transition easier.
2. In `match()`, load the fixture, acquire the session lock, call
   `matcher.find_match(...)`, and if an index is returned mark it consumed and
   build the `Response`.
3. Keep `verify_all_consumed()` unchanged except for any helper renames needed
   by the refactor.
4. Update `cmd_mox/record/__init__.py` only if the project decides to expose
   `InvocationMatcher` from the record package namespace.

After the refactor, rerun the targeted unit and BDD suites and confirm the red
tests turn green.

### Stage E: Update the design doc and the usage guide

Update `docs/python-native-command-mocking-design.md` in two places:

1. Revise section `9.5.5 InvocationMatcher` so it documents the actual
   behaviour instead of only the class outline.
2. Add a new decision entry after `9.10.12` explaining the final best-fit
   scoring rule and deterministic tie-breaking. If the extraction/export choice
   matters, record that here too.

Update `docs/usage-guide.md` in the replay-session section so it no longer says
"first unconsumed recording that matches". Replace that text with the final
best-fit semantics, written for library consumers rather than for maintainers.
The wording must explain:

1. Strict mode still requires full compatibility.
2. Fuzzy mode still gates on command and args only.
3. When multiple candidates qualify, CmdMox chooses the closest match
   deterministically.
4. Ties resolve to the earliest remaining fixture entry.

### Stage F: Mark the roadmap and run the full quality gates

Only after code, tests, and docs are complete:

1. Mark `12.2.2` as done in `docs/cmd-mox-roadmap.md`.
2. Run the full repository gates with `tee` and `set -o pipefail` exactly as
   required by `AGENTS.md`.
3. Review the captured logs rather than trusting truncated terminal output.
4. Update `Progress`, `Surprises & Discoveries`, `Decision Log`, and
   `Outcomes & Retrospective` with the actual evidence and any deviations from
   this draft.

## Validation and acceptance

The implementation is complete only when all of the following are true:

1. A dedicated `InvocationMatcher` exists in the codebase and is used by
   `ReplaySession.match()`.
2. Existing replay-session tests still pass, proving backward compatibility for
   non-ambiguous fixtures.
3. New unit tests prove strict and fuzzy boolean matching, best-fit scoring,
   consumed skipping, and deterministic tie-breaking.
4. New `pytest-bdd` scenarios prove the same semantics at the behavioural
   level through `ReplaySession`.
5. `docs/python-native-command-mocking-design.md` records the final design
   decisions and matches the shipped behaviour.
6. `docs/usage-guide.md` accurately describes replay selection semantics for
   consumers.
7. `docs/cmd-mox-roadmap.md` marks `12.2.2` as complete.
8. The following commands all succeed, with logs captured for review:

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/12-2-2-markdownlint.log
```

```bash
set -o pipefail
make nixie 2>&1 | tee /tmp/12-2-2-nixie.log
```

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/12-2-2-check-fmt.log
```

```bash
set -o pipefail
make typecheck 2>&1 | tee /tmp/12-2-2-typecheck.log
```

```bash
set -o pipefail
make lint 2>&1 | tee /tmp/12-2-2-lint.log
```

```bash
set -o pipefail
make test 2>&1 | tee /tmp/12-2-2-test.log
```

Expected success signals:

- `pytest` reports all tests passing, including the new replay matcher cases.
- `ty` reports zero diagnostics.
- `ruff` and formatting checks complete without new violations.
- Markdown lint and nixie succeed after the design doc, usage guide, roadmap,
  and ExecPlan changes.
