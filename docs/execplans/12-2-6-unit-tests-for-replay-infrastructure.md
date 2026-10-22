# Harden and test replay fixture loading, matching, and consumption

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`,
`Decision log`, `Outcomes & retrospective`, `Conformance basis`, and
`Verification plan` must be kept up to date as work proceeds.

Status: DRAFT

This plan must be approved before implementation begins.

## Purpose / big picture

Roadmap item 12.2.6 (`docs/roadmap.md`, section 12.2, "Replay
infrastructure") asks for unit tests covering fixture loading, matcher
behaviour, and consumption tracking. Items 12.2.1 to 12.2.5 delivered
`ReplaySession`, `InvocationMatcher`, `CommandDouble.replay()`, controller
dispatch, and verify-time reporting, each with tests scoped to its own change.
This item closes the remaining gaps so that the replay layer is specified by
tests rather than by reading the code.

Reconnaissance for this plan (recorded in `Surprises & discoveries`) found
that the gaps are not only missing tests. Fixture loading silently accepts
some corrupt fixtures and turns them into wrong recordings: a JSON string in
`args` is split into single characters, a JSON `null` in `command` becomes the
literal command name `"None"`, and a JSON object in `recordings` is accepted
as an empty list. Fixture files are also read and written in the platform's
locale encoding rather than UTF-8. These are bugs, so this plan fixes them
test-first as part of the same item.

After this change a library user observes three things:

1. A corrupt fixture file fails at load time (that is, when
   `ReplaySession.load()` or `CommandDouble.replay()` is called) with a
   `ValueError` whose message starts with `Invalid fixture schema:` and names
   the offending field, instead of replaying wrong data.
2. Fixture files are always read and written as UTF-8, so a fixture recorded
   on Linux replays identically on Windows.
3. Matching and consumption behave exactly as documented in
   `docs/usage-guide.md` ("Replay sessions (fixture replay)"), and that
   behaviour is now pinned by example tests, Hypothesis property tests, a
   Hypothesis state machine, syrupy snapshots of user-visible error messages,
   and pytest-bdd scenarios.

Success is observable when `make check-fmt`, `make typecheck`, `make lint`,
and `make test` all pass, the new tests listed in `Validation and acceptance`
exist and pass, and each red test listed there was observed failing before its
fix.

## Constraints

- Do not change the public signatures of `ReplaySession`, `InvocationMatcher`,
  `FixtureFile`, `RecordedInvocation`, `CommandDouble.replay()`, or
  `CmdMox.verify()`. Behaviour changes are limited to rejecting corrupt
  fixtures and pinning UTF-8 file encoding, as described in this plan.
- Do not change matching semantics (strict and fuzzy compatibility, the
  `(stdin_match, matching_env_pairs, env_subset_size)` score, or the
  lower-`sequence` tie-break). Tests characterize them; they do not alter
  them.
- Do not change the fixture schema version (`FixtureFile.SCHEMA_VERSION`,
  currently `"1.0"`) or the on-disk JSON shape. Valid fixtures written by the
  current code must continue to load unchanged.
- Do not add runtime or development dependencies. Hypothesis
  (`hypothesis>=6.165.10,<7.0`, locked at 6.169.3), syrupy (6.1.1), pytest
  (9.1.1), pytest-bdd (9.0.0), and pytest-xdist (3.8.0) are already in the
  `dev` group of `pyproject.toml`.
- Tests must pass under `make test`, which runs `pytest -v -n auto` with the
  `timeout = 30` setting from `pyproject.toml`. Every new test, including each
  Hypothesis test, must finish well inside 30 seconds on one worker.
- Snapshot files must pass `ambrleaks` (run by `make lint`): no real secrets,
  user names, or absolute host paths in `.ambr` files.
- Follow `AGENTS.md`: NumPy-style docstrings for public code, single-line
  docstrings for private helpers, en-GB-oxendict spelling, and a commit gated
  by `make check-fmt`, `make typecheck`, `make lint`, and `make test` for each
  milestone.
- Do not edit `typos.toml` by hand; put new spelling terms in
  `typos.local.toml`.

## Tolerances (exception triggers)

- Scope: if production-code edits outside `cmd_mox/record/fixture.py`,
  `cmd_mox/record/scrubber.py`, and `cmd_mox/record/replay.py` are needed, or
  production edits exceed 150 net lines, stop and escalate.
- Interface: if any public signature listed in `Constraints` must change,
  stop and escalate.
- Behaviour: if a fixture that the current code loads correctly (all fields
  of the documented types) would be rejected by the hardened validation, stop
  and escalate.
- Dependencies: if CrossHair, mutmut as a local dependency, or any other new
  package appears necessary, stop and escalate.
- Shim path: if the end-to-end strict-exhaustion scenario (Milestone EP-M4)
  shows that an `UnexpectedCommandError` raised inside the IPC handler hangs
  the shim, crashes the server thread, or produces no diagnosable failure,
  record the evidence, do not fix the IPC layer in this item, and escalate
  with a proposed follow-up roadmap entry.
- Iterations: if a gate still fails after three fix attempts for the same
  cause, stop and escalate.
- Flakiness: if any Hypothesis test fails nondeterministically or exceeds 10
  seconds under `-n auto`, reduce `max_examples` or `stateful_step_count`
  once; if it still fails, stop and escalate.
- Ambiguity: if the design document and the code disagree on intended
  behaviour in a way this plan has not already resolved, stop and present
  the options.

## Risks

- Risk: hardened type validation rejects a fixture shape that real
  recordings produce (for example a numeric `stdout` or a `null` stdin).
  Severity: medium. Likelihood: low. Mitigation: validation accepts exactly
  the types `RecordedInvocation.to_dict()` and `FixtureMetadata.to_dict()`
  emit; a Hypothesis round-trip property over `RecordedInvocation` (already in
  `cmd_mox/unittests/test_fixture_file.py`) plus a new full-fixture round-trip
  property prove every value the writer produces is accepted.
- Risk: Hypothesis tests that write fixture files trip the
  `function_scoped_fixture` health check when combined with `tmp_path`.
  Severity: low. Likelihood: high if ignored. Mitigation: property tests that
  need files create them with `tempfile.TemporaryDirectory()` inside the test
  body or in the state machine's `@initialize` rule; most matcher properties
  call `InvocationMatcher.find_match()` directly and need no files.
- Risk: the stateful test is slow under xdist and the 30-second timeout.
  Severity: medium. Likelihood: medium. Mitigation: settings of
  `max_examples=50`, `stateful_step_count=25`, and `deadline=None`; record
  the measured duration in `Progress`.
- Risk: strict replay misses raised inside the IPC handler may not surface
  cleanly through a real shim. Severity: medium. Likelihood: unknown.
  Mitigation: Stage A probe before writing the scenario; see the shim-path
  tolerance.
- Risk: making `FixtureFile.save()` write UTF-8 explicitly changes bytes on
  disk. Severity: low. Likelihood: low. `json.dumps` defaults to
  `ensure_ascii=True`, so current files are pure ASCII and identical in every
  ASCII-compatible encoding. Mitigation: keep `ensure_ascii` unchanged and pin
  it with a test so the output bytes do not change.

## Progress

- [x] (2026-10-23) Branch renamed to
  `12-2-6-unit-tests-for-replay-infrastructure`.
- [x] (2026-10-23) Reconnaissance of loading, matcher, consumption, and
  tooling conventions completed; defects confirmed by probe.
- [x] (2026-10-23) Draft ExecPlan written.
- [ ] Expert review of the draft completed and revisions applied.
- [ ] Plan approved by the user.
- [ ] Stage A: shim-path probe and baseline gates.
- [ ] EP-M1: shared replay test builders and strategies.
- [ ] EP-M2: fixture-loading tests and schema hardening.
- [ ] EP-M3: matcher example and property tests.
- [ ] EP-M4: consumption tracking tests, state machine, snapshots, BDD, and
  end-to-end scenarios.
- [ ] EP-M5: documentation, roadmap update, and final gates.

## Surprises & discoveries

- Observation: corrupt fixtures are silently accepted with wrong values.
  Evidence: a probe calling `FixtureFile.from_dict()` on edited copies of a
  valid fixture (run with `uv run python -I` on 2026-10-23) printed:

  ```plaintext
  args_str OK [('git', ['a', 'b', 'c'], 0)]
  cmd_null OK [('None', ['status'], 0)]
  recs_dict OK []
  exit_abc ValueError invalid literal for int() with base 10: 'abc'
  list ValueError Invalid fixture schema: list indices must be integers or slices, not str
  null ValueError Invalid fixture schema: argument of type 'NoneType' is not a container or iterable
  ```

  Impact: the first three are correctness bugs; `exit_abc` lacks the
  `Invalid fixture schema:` prefix; the top-level non-object messages leak
  Python internals. EP-M2 fixes all of them test-first.
- Observation: `FixtureFile.save()` and `FixtureFile.load()` call
  `Path.write_text()` and `Path.read_text()` without `encoding=`, so they use
  the locale encoding. Evidence: `cmd_mox/record/fixture.py`, `save` and
  `load`. Impact: a hand-edited or non-ASCII fixture can decode differently
  on Windows (`cp1252`) than on Linux. RFC 8259 section 8.1 requires JSON
  exchanged between systems to be UTF-8 and permits parsers to ignore a
  leading byte order mark. EP-M2 pins `encoding="utf-8"`.
- Observation: `CmdMox._verify_replay_sessions_consumed()`
  (`cmd_mox/controller.py`) raises on the first double with leftover
  recordings, so several failing doubles report only one. Impact: this is
  existing behaviour outside 12.2.6's scope; EP-M4 pins it with a test and
  the design document records it, but it is not changed.
- Observation: the matcher's tie-break uses `RecordedInvocation.sequence`,
  not list position; equal `sequence` values fall back to list order.
  Existing tests only use ascending sequences, so they cannot tell the two
  rules apart. Impact: EP-M3 adds discriminating cases.
- Observation: `docs/users-guide.md` does not exist; the user guide is
  `docs/usage-guide.md`. There is no separate component architecture
  document; `docs/python-native-command-mocking-design.md` section IX is the
  component design for record mode. Impact: documentation steps target those
  files.

## Decision log

- Decision: fix the confirmed loading defects inside 12.2.6 rather than
  only characterizing them.
  Rationale: the roadmap item's purpose is trustworthy loading; a test that
  pins `args: "abc"` becoming `["a", "b", "c"]` would specify a bug.
  `AGENTS.md` requires bug fixes to start with a failing test, which this
  plan does. The fixes stay inside `cmd_mox/record/fixture.py` and
  `cmd_mox/record/scrubber.py`. Date/Author: 2026-10-23, planning agent.
- Decision: validate field types explicitly instead of coercing with `str()`
  and `int()`.
  Rationale: coercion is what turns `null` into `"None"`. The accepted types
  are exactly those the writer emits: `sequence`, `exit_code`, and
  `duration_ms` are JSON integers (Python `bool` is rejected because it is an
  `int` subclass); `command`, `stdin`, `stdout`, `stderr`, and `timestamp` are
  strings; `args` is a list of strings; `env_subset` is an object with string
  values. Optional fields keep their current defaults when absent. Date/Author:
  2026-10-23, planning agent.
- Decision: do not move matcher or consumption logic into a Rust extension or
  write a Verus proof.
  Rationale: the repository has no Rust build, the item adds tests rather
  than new contractual logic, and the invariants concern a short pure Python
  function and a set of indices. Hypothesis oracle properties with seeded
  mutants give proportionate evidence. Introducing a native extension only to
  prove a test-scoped property would exceed the item and add a build
  dependency. Date/Author: 2026-10-23, planning agent.
- Decision: use Hypothesis rather than CrossHair for bounded exploration.
  Rationale: CrossHair is not a dependency (adding one breaches a
  tolerance), and the matcher's inputs are lists of records whose
  interesting space Hypothesis covers with small alphabets. The residual gap
  is recorded in `Verification plan`. Date/Author: 2026-10-23, planning agent.
- Decision: record design outcomes as new subsections 9.10.16 (fixture field
  validation) and 9.10.17 (UTF-8 fixture encoding) of
  `docs/python-native-command-mocking-design.md`, not as an ADR.
  Rationale: both are local, reversible implementation rules within an
  existing component; the style guide reserves ADRs for hard-to-reverse
  architectural choices. Date/Author: 2026-10-23, planning agent.

## Outcomes & retrospective

Not started. Complete at each milestone and at the end.

## Context and orientation

CmdMox is a Python library that replaces external commands with shims during
tests. "Record mode" captures real command runs into JSON fixture files;
"replay" serves those recordings back as mock responses. The relevant code is:

- `cmd_mox/record/fixture.py`: the fixture data model. `RecordedInvocation`
  is one recorded command run (fields `sequence`, `command`, `args`, `stdin`,
  `env_subset`, `stdout`, `stderr`, `exit_code`, `timestamp`,
  `duration_ms`). `FixtureMetadata` holds creation details. `FixtureFile` is
  the whole file, with `to_dict()`, `from_dict()`, `save(path)`, and
  `load(path)`. `from_dict()` first runs `_apply_migrations()`, which reads
  the `version` field, rejects a newer major version, and migrates older ones
  through the `_MIGRATIONS` registry. It then wraps `AttributeError`,
  `KeyError`, and `TypeError` as `ValueError("Invalid fixture schema: ...")`.
- `cmd_mox/record/scrubber.py`: `ScrubbingRule.from_dict()`, used for the
  fixture's `scrubbing_rules` list.
- `cmd_mox/record/matching.py`: `InvocationMatcher(strict=...)`. `matches()`
  decides whether a recording is compatible with a live `Invocation`:
  command and args must be equal; in strict mode stdin must be equal and
  every `env_subset` pair must be present in the live environment.
  `find_match(invocation, recordings, consumed)` skips consumed indices,
  ranks compatible ones by the tuple `(stdin_match, matching_env_pairs,
  env_subset_size)`, and on a tie prefers the lower `sequence`.
- `cmd_mox/record/replay.py`: `ReplaySession(fixture_path, *,
  strict_matching=True, allow_unmatched=False)`. `load()` reads the fixture
  once (a second call raises `LifecycleError`). `match(invocation)` returns a
  `Response` and records the index in a private `set[int]` under a
  `threading.Lock`, or returns `None`. `consumed_count` and
  `is_consumed(index)` expose that state. `verify_all_consumed()` raises
  `VerificationError` listing each unconsumed recording on its own
  indented line of the form `[idx] command args`, sorted by index, unless
  `allow_unmatched` is set.
- `cmd_mox/test_doubles.py`: `CommandDouble.replay(path, *, strict=True)`
  creates and eagerly loads a `ReplaySession` for a spy.
- `cmd_mox/controller.py`: `CmdMox._response_for_replay()` serves matched
  responses, raises `UnexpectedCommandError("No fixture recording matches:
  ...")` on a strict miss, and falls back to the spy's normal response on a
  fuzzy miss. `CmdMox._verify_replay_sessions_consumed()` calls
  `verify_all_consumed()` for each double during `verify()`.

Existing tests to extend, not duplicate:

- `cmd_mox/unittests/test_fixture_file.py`: round trips, migration, version
  parsing; defines the private Hypothesis strategy `_RECORDED_INVOCATIONS`.
- `cmd_mox/unittests/test_replay_session.py`: loading, matching,
  consumption, verify, thread safety; defines private builders
  `_make_recorded_invocation`, `_make_fixture_file`, and `_make_invocation`.
- `cmd_mox/unittests/test_invocation_matcher.py`: strict and fuzzy
  compatibility tables and best-fit cases.
- `cmd_mox/unittests/test_controller_replay.py` and
  `cmd_mox/unittests/test_command_double_replay.py`: controller and fluent
  API behaviour.
- BDD: feature files live in the top-level `features/` directory
  (`features/replay_session.feature`, `features/controller.feature`); step
  modules live in `tests/steps/` (`replay_session.py`, `controller_replay.py`,
  `command_execution.py`); bindings use `@scenario(str(FEATURES_DIR /
  "x.feature"), "title")` in `tests/test_replay_session_bdd.py` and
  `tests/test_controller_bdd.py`. Controller scenarios that say "I run the
  command" spawn the real shim through a subprocess, so they are end-to-end.
- Snapshots: syrupy's `snapshot` fixture, typed as
  `syrupy.assertion.SnapshotAssertion` under `TYPE_CHECKING`, compared with
  `assert value == snapshot`; `.ambr` files live in a `__snapshots__`
  directory beside the test module.

Terms used in this plan: a *property test* asserts a rule over many
generated inputs (Hypothesis `@given`). A *state machine test* generates
sequences of operations (Hypothesis `RuleBasedStateMachine` with `@rule`,
`@invariant`, `@precondition`, and `@initialize`) and checks the system
against a simple model after every step. An *oracle* is an independent,
obviously correct reimplementation used to compute the expected answer. A
*seeded mutant* is a deliberately broken variant used to prove a test can
fail.

## Relevant documentation and skills

Read before starting:

- `docs/python-native-command-mocking-design.md` section IX, especially 9.3
  (fixture format), 9.5.2 (`ReplaySession`), 9.5.5 (`InvocationMatcher`),
  and 9.10.11 to 9.10.15 (matching and replay decisions).
- `docs/usage-guide.md`, "Replay sessions (fixture replay)" and its
  subsections "Matching modes", "Best-fit selection", "Consumed-record
  tracking", and "Verifying all recordings were consumed".
- `docs/developers-guide.md`, "Snapshot testing" and "Property-based
  testing".
- `docs/documentation-style-guide.md` for prose rules.
- `docs/execplans/12-2-5-extend-cmd-mox-verify-to-report-unconsumed-recordings.md`
  for the preceding item's conventions.
- RFC 8259 section 8.1 (UTF-8 requirement for JSON text):
  <https://www.rfc-editor.org/rfc/rfc8259#section-8.1>.
- Hypothesis stateful testing reference:
  <https://hypothesis.readthedocs.io/en/latest/reference/api.html>.

Skills to load: `python-router` first, then `python-testing` (fixtures,
parametrization, snapshots), `hypothesis` (strategies and
`RuleBasedStateMachine`), `python-verification` (non-vacuity and mutants),
`python-errors-and-logging` (validation error messages), `execplans` (to
maintain this plan), `en-gb-oxendict-style` (documentation), and
`commit-message` (commits).

## Conformance basis

There is no Terms of Reference document for this repository. The upstream
artefacts are:

- `docs/roadmap.md` item 12.2.6 at commit `d22bb65` (RM-12.2.6).
- `docs/python-native-command-mocking-design.md` at commit `d22bb65`:
  9.3 fixture format (TDD-9.3), 9.5.2 `ReplaySession` (TDD-9.5.2), 9.5.5
  `InvocationMatcher` (TDD-9.5.5), 9.10.12 env subset semantics
  (TDD-9.10.12), 9.10.13 best-fit scoring (TDD-9.10.13), 9.10.15 strict-miss
  handling (TDD-9.10.15).
- `AGENTS.md` testing and commit rules (STD-AGENTS).

Trace links:

```plaintext
RM-12.2.6 (loading) -> TDD-9.3, TDD-9.5.2 -> EP-M2
  -> test_fixture_file.py::TestFixtureSchemaValidation
  -> test_replay_session.py::TestReplaySessionLoading
RM-12.2.6 (matcher) -> TDD-9.5.5, TDD-9.10.12, TDD-9.10.13 -> EP-M3
  -> test_invocation_matcher.py
  -> test_invocation_matcher_properties.py
RM-12.2.6 (consumption) -> TDD-9.5.2, TDD-9.10.15 -> EP-M4
  -> test_replay_session_state_machine.py
  -> test_replay_session.py::TestVerifyAllConsumedMessage
  -> features/replay_session.feature, features/controller.feature
```

## Verification plan

The work introduces two production rules (field type validation and UTF-8
encoding) and pins four existing invariants. Each obligation below names its
method and the control that proves the check can fail.

Axioms relied on, not verified here: Python's `json` module parses RFC 8259
JSON and `json.dumps` with default `ensure_ascii=True` emits ASCII only;
`threading.Lock` provides mutual exclusion; `pathlib.Path.read_text(encoding=
"utf-8-sig")` strips one leading byte order mark; Hypothesis shrinks and
replays failures deterministically from its database; syrupy compares
serialized values exactly.

- Obligation LOAD-1 (round-trip acceptance): for every `FixtureFile` value
  built from generated `RecordedInvocation`, `FixtureMetadata`, and
  `ScrubbingRule` values, `FixtureFile.from_dict(f.to_dict()) == f` and
  `FixtureFile.load()` after `save()` returns an equal value.
  Method: Hypothesis property. Rationale: the hardening must not reject
  anything the writer emits, which is an open-ended domain. Domain: text
  strategies including non-ASCII and control characters, integers including
  negatives and zero, up to five recordings. Artefact:
  `cmd_mox/unittests/test_fixture_file.py::test_fixture_file_round_trips`.
  Evidence: passes before and after EP-M2. Non-vacuity: `hypothesis.event()`
  labels for "non-ascii text", "empty recordings", and "env present" must
  each appear in `--hypothesis-show-statistics` output; seeded control: a
  local copy of the validator that rejects empty `stdin` must make the
  property fail (asserted with `pytest.raises` around the inner property
  function).
- Obligation LOAD-2 (type rejection): for each field and each wrong JSON
  type (null, boolean, number, string, list, object, as applicable), loading
  raises `ValueError` whose message starts with `Invalid fixture schema:` and
  names the field path (for example `recordings[0].args`). Method:
  parameterized pytest over a finite corpus plus a syrupy snapshot of the
  message table. Rationale: the field-by-type space is finite and each row
  has a distinct meaning. Artefact:
  `cmd_mox/unittests/test_fixture_file.py::TestFixtureSchemaValidation` and
  its `.ambr`. Evidence: rows for `args`-as-string, `command`-as-null, and
  `recordings`-as-object fail before EP-M2 (marked
  `xfail(strict=True)` during red) and pass after. Non-vacuity: the corpus
  must contain at least one row per field; a valid control row must load.
- Obligation LOAD-3 (UTF-8): a fixture containing non-ASCII stdout, written
  as UTF-8 bytes with and without a leading byte order mark, loads with the
  same text regardless of locale; `save()` output bytes are unchanged for
  ASCII content. Method: named examples, with the locale forced by
  monkeypatching `locale.getpreferredencoding` and by writing the file with
  explicit bytes. Artefact: `test_fixture_file.py::TestFixtureEncoding`.
  Evidence: the byte-order-mark and `cp1252` cases fail before EP-M2.
  Non-vacuity: the test asserts the decoded stdout differs under the old
  call (`read_text(encoding="cp1252")`) to prove the fixture bytes are
  discriminating.
- Obligation MATCH-1 (oracle agreement): for generated invocations,
  recordings, and consumed sets, `find_match()` returns the same index as an
  oracle that filters unconsumed compatible recordings and takes the maximum
  by `(stats, -sequence, -index)`. Method: Hypothesis property. Rationale:
  scoring is a total order over an open input space; an independent oracle
  checks every component. Domain: commands and env keys drawn from two or
  three letter alphabets so collisions are common, zero to six recordings,
  sequences drawn from a small range so ties occur. Artefact:
  `cmd_mox/unittests/test_invocation_matcher_properties.py`. Non-vacuity:
  events classify each example as "no candidate", "single candidate",
  "won on stdin", "won on env pairs", "won on subset size", and "won on
  sequence tie-break"; each class must appear. Seeded mutants, each asserted
  to make the property fail: tie-break by list index, ignore `consumed`,
  and swap the first two score components.
- Obligation MATCH-2 (mode relations): strict compatibility implies fuzzy
  compatibility; adding live environment keys that are not in a recording's
  `env_subset` never removes a strict match; `find_match()` does not mutate
  its `consumed` argument or `recordings`. Method: Hypothesis properties in
  the same module. Non-vacuity: events confirm both true and false strict
  results occur.
- Obligation CONS-1 (consumption model): across any sequence of `match()`
  calls on a loaded session, the consumed index set equals that of a model
  that applies the oracle from MATCH-1; `consumed_count` never decreases and
  increases by exactly one per non-`None` result; no index is returned twice;
  `match()` returns `None` exactly when the model has no compatible
  unconsumed recording; `verify_all_consumed()` raises exactly when
  `allow_unmatched` is false and some index is unconsumed, its message lists
  exactly the model's unconsumed indices in ascending order, and it does not
  change `consumed_count`. Method: Hypothesis `RuleBasedStateMachine`.
  Rationale: the bugs this guards against depend on operation order.
  Domain: a fixture of up to six recordings generated in `@initialize`;
  rules `match_recorded` (replays a recorded invocation, possibly with
  perturbed stdin or env), `match_unknown` (an unrecorded command), and
  `verify`. Bounds: `max_examples=50`, `stateful_step_count=25`, which admits
  the shortest interesting run (exhaust every recording then call `match`
  once more). Artefact:
  `cmd_mox/unittests/test_replay_session_state_machine.py`. Non-vacuity:
  events record "exhausted", "partial verify", and "complete verify"; seeded
  control: a session subclass whose `match()` forgets to add to `_consumed`
  must fail the machine (run via `run_state_machine_as_test` inside
  `pytest.raises`).
- Obligation CONS-2 (concurrency): with N recordings and M > N threads
  calling `match()` concurrently on identical invocations, exactly N calls
  return a response, M - N return `None`, and the returned stdout values are
  a permutation of the recorded ones. Method: named example with a
  `threading.Barrier`, repeated over a small parameter table of (N, M).
  Non-vacuity: the table includes M = N and M > N; the test asserts all M
  threads ran.
- Obligation MSG-1 (message format): `verify_all_consumed()` and
  `UnexpectedCommandError` messages for zero-arg commands, multi-arg
  commands, one unconsumed, and several unconsumed recordings match
  snapshots. Method: syrupy snapshots, because the format is multivariant
  user-visible output. Artefact:
  `test_replay_session.py::TestVerifyAllConsumedMessage` and
  `cmd_mox/unittests/__snapshots__/test_replay_session.ambr`. Non-vacuity:
  snapshots are generated once with `--snapshot-update`, reviewed, then
  verified without it.

Residual gaps: Hypothesis samples rather than exhausts; with small alphabets
and up to six recordings it reaches every scoring class (proved by events),
but no claim of exhaustive coverage is made. Thread interleavings are tested
by example only. The IPC transport path for strict misses is tested by one
end-to-end scenario, not exhaustively.

## Plan of work

Stage A (no production changes): run the four gates on the untouched branch
and record the pass counts in `Progress`. Probe the shim path: in a scratch
script under `/tmp/12-2-6-probe/`, create a `CmdMox` with a spy replaying a
one-recording fixture, run the shim twice, and record the second run's exit
code, stderr, and whether `verify()` reports the `UnexpectedCommandError`.
This decides the wording of the EP-M4 end-to-end scenario. Go/no-go: gates
green and the probe shows a diagnosable failure; otherwise apply the
shim-path tolerance.

Stage B (EP-M1, refactor only): move the builders `_make_recorded_invocation`,
`_make_fixture_file`, and `_make_invocation` from
`cmd_mox/unittests/test_replay_session.py` into a new private module
`cmd_mox/unittests/_replay_helpers.py`, renamed without the leading
underscore (`make_recorded_invocation`, `write_fixture_file`,
`make_invocation`) because they become module exports. Add Hypothesis
strategies there: `recorded_invocations()` (absorbing `_RECORDED_INVOCATIONS`
from `test_fixture_file.py`), `fixture_metadata()`, `fixture_files()`, and
`replay_scenarios()` (a small-alphabet invocation plus recordings plus
consumed set). Update existing tests to import them. No behaviour change;
existing tests must pass unmodified in their assertions.

Stage C (EP-M2, red then green): add the LOAD-1, LOAD-2, and LOAD-3 tests
and the loading gap tests from the reconnaissance (missing recording keys,
missing metadata keys, missing scrubbing-rule keys, malformed version string
through `load()`, integer `version`, `str` path to `ReplaySession`, retry
after a failed `load()`, directory path raising `IsADirectoryError` or
`PermissionError` as an `OSError`, empty recordings list). Mark the
defect rows `xfail(strict=True)` and run them to observe the expected
failures. Then implement, in `cmd_mox/record/fixture.py`, small private
validators (`_require_str`, `_require_int`, `_require_str_list`,
`_require_str_mapping`, `_require_list`, `_require_object`) that raise
`TypeError` or `KeyError` with a field path, call them from
`RecordedInvocation.from_dict()`, `FixtureMetadata.from_dict()`, and
`ScrubbingRule.from_dict()`, check the top-level value is an object before
migration, and pass `encoding="utf-8"` to `save()` and
`encoding="utf-8-sig"` to `load()`. The existing wrapper in
`FixtureFile.from_dict()` already converts those to `ValueError("Invalid
fixture schema: ...")`; extend it to include `ValueError` from `int`-like
parsing only if a row still escapes. Remove the `xfail` markers.

Stage D (EP-M3): add discriminating example tests to
`cmd_mox/unittests/test_invocation_matcher.py` (missing live env key in
strict mode, empty `env_subset`, all candidates consumed, empty recordings
list, lower `sequence` appearing later in the list, equal `sequence` falling
back to list order, `env_subset_size` deciding in fuzzy mode). Add
`cmd_mox/unittests/test_invocation_matcher_properties.py` with MATCH-1 and
MATCH-2 and their seeded mutants. These characterize existing behaviour and
are expected to pass on first run; to satisfy red-green, each new property is
first run against its seeded mutant and observed failing, which is the
negative control recorded in `Validation and acceptance`.

Stage E (EP-M4): add `cmd_mox/unittests/test_replay_session_state_machine.py`
(CONS-1), extend the thread-safety class in `test_replay_session.py` with the
CONS-2 table, add `TestVerifyAllConsumedMessage` (MSG-1) with snapshots, add
fuzzy-mode exhaustion at session level, and add to
`cmd_mox/unittests/test_controller_replay.py`: strict exhaustion through
`_response_for_replay` raises `UnexpectedCommandError`, fuzzy exhaustion falls
back and counts the invocation once, two doubles keep independent
consumption, and verify with two failing doubles reports the first
registered double (pinning current behaviour). Add the BDD scenarios and
steps listed in `Validation and acceptance`.

Stage F (EP-M5): documentation and roadmap; see the milestone.

## Milestones and plateaus

- EP-M1: shared test builders and strategies exist and all existing tests
  pass. Requirements: enabling work for RM-12.2.6. Acceptance: `make test`
  pass count equals the Stage A baseline. Conformance: no production change;
  no new dependency. Recovery: revert the single commit. Remaining gaps: all
  new tests. Compatibility decision: none (test-only surface).
- EP-M2: corrupt fixtures are rejected with field-named messages and files
  use UTF-8. Requirements: RM-12.2.6 loading, TDD-9.3, TDD-9.5.2.
  Acceptance: LOAD-1 to LOAD-3 and the loading gap tests pass; the three
  defect rows were observed failing first. Conformance: only `fixture.py`
  and `scrubber.py` changed; no schema version change; valid fixtures still
  load (LOAD-1). Recovery: revert the milestone commits; the tests can be
  re-added with `xfail` if the fix must be postponed. Remaining gaps:
  matcher and consumption tests. Compatibility decision: none. Corrupt
  fixtures were never a supported input, and valid fixtures are unaffected.
- EP-M3: matcher behaviour fully pinned. Requirements: RM-12.2.6 matcher,
  TDD-9.5.5, TDD-9.10.12, TDD-9.10.13. Acceptance: new examples and
  MATCH-1 and MATCH-2 pass; each seeded mutant is rejected. Conformance: no
  production change. Recovery: revert. Remaining gaps: consumption.
  Compatibility decision: none.
- EP-M4: consumption tracking pinned at session, controller, BDD, and
  end-to-end levels. Requirements: RM-12.2.6 consumption, TDD-9.10.15.
  Acceptance: CONS-1, CONS-2, MSG-1, controller tests, and BDD scenarios
  pass; the state-machine mutant is rejected. Conformance: no production
  change unless the shim probe forces an escalation. Recovery: revert.
  Remaining gaps: documentation. Compatibility decision: none.
- EP-M5: documentation updated and roadmap item marked done. Acceptance:
  `make markdownlint` and `make nixie` pass, plus all four code gates.
  Compatibility decision: none.

Each milestone ends with `make check-fmt`, `make typecheck`, `make lint`,
and `make test` passing, then a commit.

## Concrete steps

Run everything from the repository root. Capture gate output with `tee` so
truncated terminal output can be reviewed:

```bash
set -o pipefail
NO_COLOR=1 make check-fmt 2>&1 | tee /tmp/check-fmt-cmd-mox-12-2-6-unit-tests-for-replay-infrastructure.out
NO_COLOR=1 make typecheck 2>&1 | tee /tmp/typecheck-cmd-mox-12-2-6-unit-tests-for-replay-infrastructure.out
NO_COLOR=1 make lint 2>&1 | tee /tmp/lint-cmd-mox-12-2-6-unit-tests-for-replay-infrastructure.out
NO_COLOR=1 make test 2>&1 | tee /tmp/test-cmd-mox-12-2-6-unit-tests-for-replay-infrastructure.out
```

Focused runs while iterating:

```bash
uv run pytest cmd_mox/unittests/test_fixture_file.py -q
uv run pytest cmd_mox/unittests/test_invocation_matcher_properties.py -q --hypothesis-show-statistics
uv run pytest cmd_mox/unittests/test_replay_session_state_machine.py -q --hypothesis-show-statistics
uv run pytest cmd_mox/unittests/test_replay_session.py --snapshot-update
uv run pytest tests/test_replay_session_bdd.py tests/test_controller_bdd.py -q
```

Expected red output for an EP-M2 defect row before the fix, with the
`xfail(strict=True)` marker in place, is an `XFAIL` line; removing the marker
before the fix shows, for example:

```plaintext
FAILED ...::TestFixtureSchemaValidation::test_rejects_wrong_type[recordings[0].args-string]
  Failed: DID NOT RAISE <class 'ValueError'>
```

For documentation-only commits run `make fmt`, `make markdownlint`, and
`make nixie`.

## Validation and acceptance

New BDD scenarios in `features/replay_session.feature`:

```gherkin
  Scenario: replay session rejects a fixture with a corrupt recording
    Given a fixture file whose first recording has args stored as a string
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded expecting an error
    Then the replay load error message should contain "Invalid fixture schema: recordings[0].args"

  Scenario: duplicate recordings are consumed in sequence order
    Given a fixture file with "git status" recordings "one", "two", "three" in reverse list order
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    And a replay invocation of "git" with args "status" is matched 3 times
    Then the replay match stdout values are "one", "two", "three"
    And replay verify_all_consumed does not raise

  Scenario: an exhausted strict replay session returns none
    Given a fixture file with a recording of "git" with args "status"
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    And a replay invocation of "git" with args "status" is matched 2 times
    Then the last replay match result is None
```

New scenarios in `features/controller.feature` (end-to-end through the real
shim):

```gherkin
  Scenario: replay-backed spy consumes duplicate recordings in order
    Given a CmdMox controller
    And a git replay fixture with recordings "first" and "second" exists
    And the command "git" is spied with that replay fixture
    When I replay the controller
    And I run the command "git" with arguments "status"
    Then the output should be "first"
    When I run the command "git" with arguments "status"
    Then the output should be "second"
    When I verify the controller
    Then the spy "git" should record 2 invocation

  Scenario: corrupt replay fixture is rejected when configuring the spy
    Given a CmdMox controller
    And a corrupt git replay fixture exists
    When the command "git" is spied with that replay fixture expecting an error
    Then the replay configuration error message should contain "Invalid fixture schema"
```

A third controller scenario, "strict replay exhaustion fails the second shim
run", is added with Then-steps worded from the Stage A probe result; if the
probe triggers the shim-path tolerance, it is not added and the plan
escalates.

Unit-level acceptance, by file:

- `cmd_mox/unittests/test_fixture_file.py`: `test_fixture_file_round_trips`
  (LOAD-1, with its seeded control), `TestFixtureSchemaValidation` (LOAD-2,
  parameterized, with snapshot), `TestFixtureEncoding` (LOAD-3). The rows
  `recordings[0].args-string`, `recordings[0].command-null`, and
  `recordings-object` and the encoding byte-order-mark case fail before
  EP-M2 and pass after.
- `cmd_mox/unittests/test_replay_session.py`: `TestReplaySessionLoading`
  gap cases (string path, retry after failed load, directory path, malformed
  version through `load()`), `TestVerifyAllConsumedMessage` (MSG-1),
  fuzzy-mode exhaustion, CONS-2 table.
- `cmd_mox/unittests/test_invocation_matcher.py`: the seven discriminating
  examples in Stage D.
- `cmd_mox/unittests/test_invocation_matcher_properties.py`: MATCH-1,
  MATCH-2, and three mutant controls.
- `cmd_mox/unittests/test_replay_session_state_machine.py`: CONS-1 and its
  mutant control.
- `cmd_mox/unittests/test_controller_replay.py`: strict exhaustion, fuzzy
  exhaustion fallback, independent doubles, first-failing-double verify.

Quality criteria: all four code gates pass at each milestone; `make
markdownlint` and `make nixie` pass for documentation; every Hypothesis test
finishes in under 10 seconds locally; every mutant control passes (meaning
the property rejected the mutant); `--hypothesis-show-statistics` shows each
named event class at least once.

## Idempotence and recovery

All steps are additive edits under version control. Re-running tests is
safe. Snapshot updates are explicit (`--snapshot-update`) and must be
followed by a run without the flag. Each milestone is one or more commits;
revert with `git revert <sha>` to step back a milestone. Scratch probes live
in `/tmp/12-2-6-probe/` and can be deleted at any time.

## Artefacts and notes

The `Surprises & discoveries` probe transcript is the main evidence for the
EP-M2 scope. Record red and green transcripts for each defect row and each
mutant control in this section during implementation.

## Interfaces and dependencies

No new dependencies. New test-only module
`cmd_mox/unittests/_replay_helpers.py` exports:

```python
def make_recorded_invocation(
    command: str = "git",
    args: list[str] | None = None,
    *,
    sequence: int = 0,
    stdin: str = "",
    env_subset: dict[str, str] | None = None,
    stdout: str = "ok\n",
    stderr: str = "",
    exit_code: int = 0,
) -> RecordedInvocation: ...

def write_fixture_file(
    directory: Path,
    recordings: list[RecordedInvocation],
    filename: str = "fixture.json",
) -> Path: ...

def make_invocation(
    command: str = "git",
    args: list[str] | None = None,
    *,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> Invocation: ...

def recorded_invocations(
    *, commands: st.SearchStrategy[str] | None = None
) -> st.SearchStrategy[RecordedInvocation]: ...

def fixture_files(
    *, max_recordings: int = 5
) -> st.SearchStrategy[FixtureFile]: ...

def replay_scenarios(
    *, max_recordings: int = 6
) -> st.SearchStrategy[ReplayScenario]: ...
```

`ReplayScenario` is a frozen dataclass in the same module holding
`invocation: Invocation`, `recordings: list[RecordedInvocation]`, and
`consumed: frozenset[int]`. The exact keyword defaults should follow the
existing private builders they replace.

Private production helpers added to `cmd_mox/record/fixture.py`, each
raising `TypeError` (wrong type) or `KeyError` (missing required key) with a
message naming the field path:

```python
def _require_str(data: dict[str, object], key: str, path: str, *, default: str | None = None) -> str: ...
def _require_int(data: dict[str, object], key: str, path: str, *, default: int | None = None) -> int: ...
def _require_str_list(data: dict[str, object], key: str, path: str) -> list[str]: ...
def _require_str_mapping(data: dict[str, object], key: str, path: str) -> dict[str, str]: ...
```

Documentation changes in EP-M5:

- `docs/python-native-command-mocking-design.md`: add 9.10.16 "Fixture field
  validation" and 9.10.17 "UTF-8 fixture encoding"; note in 9.5.5 that a
  missing live environment key never satisfies an `env_subset` pair and that
  equal `sequence` values fall back to list order; note in 9.8 that verify
  stops at the first double with unconsumed recordings.
- `docs/usage-guide.md`: in "Creating and loading a replay session", state
  that fixtures are UTF-8 and that corrupt fixtures raise `ValueError`
  starting `Invalid fixture schema:` with the field path; in "Best-fit
  selection", state the equal-sequence rule.
- `docs/developers-guide.md`: extend "Property-based testing" with the
  replay properties, the state machine, the seeded-mutant convention, and
  `--hypothesis-show-statistics`; extend "Snapshot testing" with the replay
  message snapshots; add a short "Replay test helpers" section describing
  `cmd_mox/unittests/_replay_helpers.py`.
- `docs/roadmap.md`: mark 12.2.6 done (`- [x]`).

## Revision note

Initial draft, 2026-10-23.
