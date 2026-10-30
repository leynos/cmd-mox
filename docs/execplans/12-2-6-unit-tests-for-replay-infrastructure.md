# Test replay fixture loading, matching, and consumption tracking

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: DRAFT

This plan must be approved before implementation begins.

## Purpose / big picture

Roadmap item 12.2.6 (`docs/roadmap.md`, section 12.2, "Replay infrastructure")
asks for unit tests covering fixture loading, matcher behaviour, and
consumption tracking. Items 12.2.1 to 12.2.5 delivered `ReplaySession`,
`InvocationMatcher`, `CommandDouble.replay()`, controller dispatch, and
verify-time reporting, each with tests scoped to its own change. This item
closes the remaining gaps so that the replay layer is specified by tests rather
than by reading the code.

Reconnaissance for this plan (recorded in `Surprises & discoveries`) found that
some gaps hide defects. Fixture loading silently accepts some schema-invalid
recordings and turns them into wrong data: a JSON string in `args` is split
into single characters, a JSON `null` in `command` becomes the literal command
name `"None"`, and a JSON object in `recordings` is accepted as an empty list.
Fixture files are also read and written in the platform's locale encoding
rather than UTF-8, and a fixture saved with a leading UTF-8 byte order mark (as
some Windows editors do) fails to parse. A test that pinned this behaviour
would specify a bug, so the plan fixes these defects test-first, in their own
milestone and commits, labelled as defects found while delivering 12.2.6.

After this change a library user observes four things:

1. A fixture whose recordings or metadata have fields of the wrong JSON type
   fails at load time (when `ReplaySession.load()` or `CommandDouble.replay()`
   is called) with a `ValueError` whose message starts with
   `Invalid fixture schema:` and names the field path, for example
   `Invalid fixture schema: recordings[0].args: expected list of
   strings, got string`,
   instead of replaying wrong data. Malformed JSON and undecodable bytes keep
   raising their existing `ValueError` subclasses (`json.JSONDecodeError`,
   `UnicodeDecodeError`).
2. Fixture files are read and written as UTF-8 regardless of the platform
   locale, and a leading byte order mark is tolerated on load.
3. Matching and consumption behave exactly as documented in
   `docs/usage-guide.md` ("Replay sessions (fixture replay)"), and that
   behaviour is pinned by example tests, Hypothesis property tests, a
   Hypothesis state machine, syrupy snapshots of user-visible error messages,
   and pytest-bdd scenarios, including end-to-end runs through the real shim.
4. The usage guide documents one sharp edge the probe found: when a strict
   replay session has no matching recording, the shim exits with status 1 and
   prints `No fixture recording matches: <command>` on stderr, but
   `CmdMox.verify()` does not report it. Changing that is roadmap item 12.2.7,
   not done here.

Success is observable when `make check-fmt`, `make typecheck`, `make lint`, and
`make test` all pass at every milestone, the new tests listed in
`Validation and acceptance` exist and pass, and each red test listed there was
observed failing before its fix.

## Constraints

- Do not change the public signatures of `ReplaySession`, `InvocationMatcher`,
  `FixtureFile`, `RecordedInvocation`, `FixtureMetadata`,
  `CommandDouble.replay()`, or `CmdMox.verify()`.
- Do not change matching semantics (strict and fuzzy compatibility, the
  `(stdin_match, matching_env_pairs, env_subset_size)` score, or the
  lower-`sequence` tie-break with list order as the final fallback). Tests
  characterize them; they do not alter them.
- Do not change controller dispatch or verification behaviour, including the
  strict-miss-over-shim behaviour described in `Surprises & discoveries`.
- Do not change the fixture schema version (`FixtureFile.SCHEMA_VERSION`,
  currently `"1.0"`) or the on-disk JSON shape. Every value the current writer
  (`FixtureFile.to_dict()`) can emit must still load.
- Do not modify `cmd_mox/record/scrubber.py`. Field-level validation of
  scrubbing rules belongs to roadmap item 12.3.2, which reworks
  `ScrubbingRule`; this item only requires `scrubbing_rules` to be a list of
  objects.
- Do not add runtime or development dependencies. Hypothesis
  (`hypothesis>=6.165.10,<7.0`, locked at 6.169.3), syrupy (6.1.1), pytest
  (9.1.1), pytest-bdd (9.0.0), and pytest-xdist (3.8.0) are already in the
  `dev` group of `pyproject.toml`.
- Tests must pass under `make test` (`pytest -v -n auto`) and under the
  serial, traced coverage job in `.github/workflows/ci.yml`, both with the
  `timeout = 30` setting from `pyproject.toml`.
- Snapshot files must pass `ambrleaks` (run by `make lint`). Messages under
  snapshot must not contain fixture paths, temporary directories, or `repr` of
  user values; they name field paths and JSON type names only.
- Follow `AGENTS.md`: NumPy-style docstrings for public code, single-line
  docstrings for private helpers, en-GB-oxendict spelling, and every commit
  gated. Do not edit `typos.toml` by hand; put new terms in `typos.local.toml`.

## Tolerances (exception triggers)

- Scope: production edits are limited to `cmd_mox/record/fixture.py`. If any
  other production file must change, or production edits exceed 150 net lines,
  stop and escalate.
- Interface: if any public signature listed in `Constraints` must change,
  stop and escalate.
- Behaviour: if the hardened validation rejects any value that
  `FixtureFile.to_dict()` can emit (including `null` descriptions and absent
  optional metadata), stop and escalate.
- Dependencies: if CrossHair, a local mutmut install, or any other new
  package appears necessary, stop and escalate.
- Shim path: Stage A has already been probed (see `Surprises & discoveries`).
  If re-running it during implementation shows a hang, a crashed server thread,
  or a different exit status, stop and escalate.
- Iterations: if a gate still fails after three fix attempts for the same
  cause, stop and escalate.
- Timing: if any Hypothesis test exceeds 10 seconds under `make test`, or 20
  seconds in a serial run under `coverage run`, lower `max_examples` or
  `stateful_step_count` once; if it still exceeds the limit, stop and escalate.
- Ambiguity: if the design document and the code disagree on intended
  behaviour in a way this plan has not already resolved, stop and present the
  options.

## Risks

- Risk: hardened validation rejects a value the writer emits.
  Severity: high. Likelihood: medium without mitigation; the review found
  `ScrubbingRule.to_dict()` writes `"description": null` for an empty
  description (`cmd_mox/record/scrubber.py`). Mitigation: scrubbing-rule fields
  are out of scope (only "list of objects" is checked); the nullability table in
  `Decision log` covers metadata; the LOAD-1 round-trip property generates
  empty descriptions and absent optional metadata; a checked-in golden fixture
  produced by the current writer must load.
- Risk: tightening rejects hand-edited fixtures that load today through
  coercion (`exit_code: 1.0`, `duration_ms: 1e3`, `sequence: true`,
  `test_module: 3`). Severity: low. Likelihood: low; the writer has never
  produced these (`git log -S to_dict -- cmd_mox/record/fixture.py` shows the
  field types unchanged since the field set was introduced). Mitigation:
  recorded as an accepted contract tightening in the EP-M2 compatibility
  decision and noted in `docs/usage-guide.md`.
- Risk: Hypothesis tests exceed the 30-second timeout in the serial coverage
  job. Severity: medium. Likelihood: medium. Mitigation: pure matcher
  properties need no files; the state machine writes one fixture per run in
  `@initialize`; seeded-mutant controls are deterministic witnesses, never
  generated runs that shrink; durations are measured under `coverage run` and
  recorded in `Progress`.
- Risk: new BDD step text collides with an existing `cfparse` pattern (for
  example `I run the command "{cmd}" expecting failure` also matches text
  containing `with arguments`). Severity: low. Likelihood: medium. Mitigation:
  prefer existing steps; for any new step, confirm binding with
  `pytest --collect-only` and one red run.
- Risk: the shim blocks reading stdin when it is not a terminal, so ad hoc
  probes hang. Severity: low. Mitigation: probes and any new subprocess helper
  pass `stdin=subprocess.DEVNULL`; the existing BDD helpers already handle this.

## Progress

- [x] (2026-10-23) Branch renamed to
  `12-2-6-unit-tests-for-replay-infrastructure`.
- [x] (2026-10-23) Reconnaissance of loading, matcher, consumption, and
  tooling conventions completed; defects confirmed by probe.
- [x] (2026-10-23) Draft ExecPlan written and committed.
- [x] (2026-10-23) Expert design review (structure, contracts, verification,
  cost, failure modes, viability) completed; the shim-path probe (Stage A) was
  run during review; revisions applied.
- [x] (2026-10-23) User decided that the defect fixes stay in this pull
  request as separate commits, and that 12.2.7 is added to the roadmap (added
  in the planning pull request).
- [ ] Plan approved by the user for implementation.
- [ ] Stage A: baseline gates recorded.
- [ ] EP-M1: shared replay test builders and strategies.
- [ ] EP-M2: fixture-loading tests and schema and encoding defect fixes.
- [ ] EP-M3: matcher example and property tests.
- [ ] EP-M4: consumption tracking tests, state machine, snapshots, BDD, and
  end-to-end scenarios.
- [ ] EP-M5: documentation, roadmap update, and final gates.

## Surprises & discoveries

- Observation: schema-invalid recordings are silently accepted with wrong
  values. Evidence: a probe calling `FixtureFile.from_dict()` on edited copies
  of a valid fixture (run with `uv run python -I` on 2026-10-23) printed:

  ```plaintext
  args_str OK [('git', ['a', 'b', 'c'], 0)]
  cmd_null OK [('None', ['status'], 0)]
  recs_dict OK []
  exit_abc ValueError invalid literal for int() with base 10: 'abc'
  missing_seq ValueError Invalid fixture schema: 'sequence'
  list ValueError Invalid fixture schema: list indices must be integers or slices, not str
  null ValueError Invalid fixture schema: argument of type 'NoneType' is not a container or iterable
  ```

  Impact: the first three are correctness defects. `exit_abc` lacks the
  `Invalid fixture schema:` prefix. A missing key is quoted (`'sequence'`)
  because `str(KeyError(...))` adds quotes. Top-level non-object messages leak
  Python internals. EP-M2 fixes all of them.
- Observation: `FixtureFile.save()` and `FixtureFile.load()` call
  `Path.write_text()` and `Path.read_text()` without `encoding=`. Evidence:
  `cmd_mox/record/fixture.py`; running a load under
  `python -X warn_default_encoding -W error::EncodingWarning` exits 1 with
  `EncodingWarning: 'encoding' argument not specified`, and a fixture with a
  leading byte order mark raises `JSONDecodeError: Unexpected UTF-8 BOM`.
  Monkeypatching `locale.getpreferredencoding` does not change what
  `Path.read_text()` uses, so it cannot serve as a test. Ruff's
  `unspecified-encoding` rule is enabled in `pyproject.toml` but does not flag
  these `pathlib` calls. Impact: EP-M2 pins `encoding="utf-8"` on save and
  `encoding="utf-8-sig"` on load and tests both with the discriminating methods
  above. RFC 8259 section 8.1 requires UTF-8 for JSON exchanged between systems
  and lets parsers ignore a byte order mark.
- Observation: `ScrubbingRule.to_dict()` writes `"description": null` when
  the description is empty, and `ScrubbingRule.from_dict()` splits a string
  `applied_to` into characters. Impact: scrubbing-rule field validation is
  deferred to 12.3.2 (see `Decision log`); this item must not reject `null`
  descriptions.
- Observation (Stage A probe, run during review): with a one-recording
  strict fixture, the first shim run of `git status` exits 0 with `ok`; the
  second exits 1 with stderr `No fixture recording matches: git status`, because
  `_server_core._execute_request` turns handler exceptions into
  `Response(stderr=str(exc), exit_code=1)`. `CmdMox.verify()` then passes,
  because the strict miss is raised before the invocation is journalled.
  Impact: a strict-mode test whose code under test ignores the exit status
  passes silently. This contradicts the expectation in design section 9.10.15
  that raising makes the test fail. It is outside 12.2.6's scope; EP-M4 pins
  the current behaviour with an end-to-end scenario, EP-M5 documents it, and
  `Decision log` proposes a follow-up roadmap item.
- Observation: `CmdMox._verify_replay_sessions_consumed()`
  (`cmd_mox/controller.py`) raises on the first double with leftover
  recordings, so several failing doubles report only one. Impact: existing
  behaviour; EP-M4 pins it and EP-M5 documents it.
- Observation: the matcher's tie-break uses `RecordedInvocation.sequence`,
  not list position; equal `sequence` values fall back to list order. Existing
  tests only use ascending sequences, so they cannot tell the two rules apart.
  In strict mode every compatible candidate has `stdin_match=True` and
  `matching_env_pairs == len(env_subset)`, so only fuzzy mode can discriminate
  the first two score components. Impact: EP-M3 adds discriminating cases and
  draws the mode in generated scenarios.
- Observation: `cmd_mox/unittests` is excluded from the Skylos scan
  (`Makefile`) and from mutation (`do_not_mutate` in `pyproject.toml`), and
  `_invocation_helpers.py` already sets the precedent for a shared,
  non-collected helper module there. Ruff's per-file ignores for `PLR0913`,
  `PLR0917`, and `S101` apply only to `**/test_*.py`, so a helper module must
  respect `max-args = 4`. Impact: helpers take spec dataclasses rather than
  long keyword lists; no Skylos entry-point rules are needed.
- Observation: `docs/users-guide.md` does not exist; the user guide is
  `docs/usage-guide.md`. There is no separate component architecture document;
  `docs/python-native-command-mocking-design.md` section IX is the component
  design for record mode. Windows CI runs only `WINDOWS_SMOKE_ARGS`, so no
  claim about Windows behaviour is made beyond "independent of locale". Impact:
  documentation steps target those files.

## Decision log

- Decision: fix the confirmed loading and encoding defects inside 12.2.6,
  in a separate milestone (EP-M2) with commits whose subjects say they fix
  defects, rather than only characterizing them. Rationale: the item's purpose
  is trustworthy loading; pinning `args: "abc"` becoming `["a", "b", "c"]`
  would specify a bug. `AGENTS.md` requires bug fixes to start with a failing
  test, which this plan does. The review raised splitting EP-M2 into its own
  pull request; keeping it in the 12.2.6 pull request with separate commits was
  preferred because the tests and fixes are inseparable. Date/Author:
  2026-10-23, planning agent after design review; confirmed by the user on
  2026-10-23.
- Decision: validate recording and metadata field types explicitly instead
  of coercing with `str()` and `int()`. Accepted types, which are exactly what
  the writer emits:

  ```plaintext
  top level        object
  recordings       list of objects (absent -> [])
  scrubbing_rules  list of objects (absent -> []); fields unchecked (12.3.2)
  recordings[i].sequence, exit_code, duration_ms   int, not bool
                                                   (exit_code, duration_ms absent -> 0)
  recordings[i].command, timestamp                 str, required
  recordings[i].stdin, stdout, stderr              str (absent -> "")
  recordings[i].args                               list of str, required
  recordings[i].env_subset                         object of str -> str (absent -> {})
  metadata                                         object, required
  metadata.created_at, cmdmox_version, platform, python_version   str, required
  metadata.test_module, test_function              str or null or absent (-> None)
  ```

  Rationale: coercion is what turns `null` into `"None"`. Python `bool` is
  rejected for integer fields because it is an `int` subclass and JSON `true`
  is not a sequence number. Non-string `test_module` and `test_function` were
  silently dropped before and are now rejected; this is an accepted tightening.
  Date/Author: 2026-10-23, planning agent after design review.
- Decision: schema errors are raised by one private helper as
  `ValueError("Invalid fixture schema: <path>: expected <type>, got
  <json type>")`,
  and a missing required key as
  `ValueError("Invalid fixture schema: <path>: missing required field")`. The
  existing wrapper in `FixtureFile.from_dict()` keeps catching `AttributeError`,
  `KeyError`, and `TypeError` as a backstop, but formats a `KeyError` with
  `exc.args[0]` so it is not quoted. Version errors (`_parse_version`,
  `_normalize_version_field`) keep their existing messages, which existing
  tests pin. JSON and decoding errors are not wrapped. Rationale:
  `str(KeyError("x"))` is `"'x'"`, which would break field-path messages;
  building the message once keeps it consistent and snapshot-friendly.
  Date/Author: 2026-10-23, planning agent after design review.
- Decision: keep the validators in `cmd_mox/record/fixture.py` and leave
  `cmd_mox/record/scrubber.py` untouched. Rationale: `fixture.py` imports
  `ScrubbingRule` from `scrubber.py` at run time, so `scrubber.py` calling back
  into `fixture.py` would create an import cycle; and scrubbing-rule fields
  belong to 12.3.2. Date/Author: 2026-10-23, planning agent after design review.
- Decision: pin and document, but do not change, the behaviour where a strict
  replay miss through the shim exits 1 while `verify()` passes. Propose a
  follow-up roadmap entry under 12.2, worded as "12.2.7. Journal strict replay
  misses so `CmdMox.verify()` reports them alongside unconsumed recordings.",
  added to `docs/roadmap.md`. Rationale: the fix changes controller and
  verifier behaviour, which is outside a testing item and needs its own design
  decision. Date/Author: 2026-10-23, planning agent after design review; the
  user approved adding 12.2.7 on 2026-10-23, and it was added to the roadmap in
  the planning pull request.
- Decision: seeded-mutant controls use deterministic, hand-built witnesses
  passed to a shared check function, not generated Hypothesis runs against
  mutants; class coverage is enforced by witness tests, not by reading
  `--hypothesis-show-statistics`. Rationale: Hypothesis keys its example
  database by test function, so a mutant run can pollute the real property;
  failing generated runs shrink and threaten the timeout; and statistics output
  is not asserted by anything. A witness that the mutant must fail is
  deterministic and cheap. Date/Author: 2026-10-23, planning agent after design
  review.
- Decision: do not move matcher or consumption logic into a Rust extension or
  write a Verus proof. Rationale: the repository has no Rust build; this item
  adds tests and two local validation rules, not new contractual logic; and the
  invariants concern a short pure Python function and a set of indices, for
  which an independent oracle plus mutant witnesses gives proportionate
  evidence. A native extension only to prove a test-scoped property would
  exceed the item and add a build dependency. Date/Author: 2026-10-23, planning
  agent.
- Decision: use Hypothesis rather than CrossHair.
  Rationale: CrossHair is not a dependency (adding one breaches a tolerance),
  and Hypothesis with small alphabets reaches every scoring class, which
  witness tests prove. The residual gap is recorded in `Verification plan`.
  Date/Author: 2026-10-23, planning agent.
- Decision: record design outcomes as new subsections 9.10.16 ("Fixture field
  validation") and 9.10.17 ("UTF-8 fixture encoding") of
  `docs/python-native-command-mocking-design.md`, and amend 9.10.15 with the
  strict-miss observation, rather than writing an ADR. Rationale: these are
  local, reversible rules within an existing component; the documentation style
  guide reserves ADRs for hard-to-reverse architectural choices. Date/Author:
  2026-10-23, planning agent.

## Outcomes & retrospective

Not started. Complete at each milestone and at the end.

## Context and orientation

CmdMox is a Python library that replaces external commands with shims during
tests. "Record mode" captures real command runs into JSON fixture files;
"replay" serves those recordings back as mock responses. The relevant code is:

- `cmd_mox/record/fixture.py`: the fixture data model. `RecordedInvocation`
  is one recorded command run (fields `sequence`, `command`, `args`, `stdin`,
  `env_subset`, `stdout`, `stderr`, `exit_code`, `timestamp`, `duration_ms`).
  `FixtureMetadata` holds creation details. `FixtureFile` is the whole file,
  with `to_dict()`, `from_dict()`, `save(path)`, and `load(path)`.
  `from_dict()` first runs `_apply_migrations()`, which reads the `version`
  field, rejects a newer major version, and migrates older ones through the
  `_MIGRATIONS` registry. It then wraps `AttributeError`, `KeyError`, and
  `TypeError` as `ValueError("Invalid fixture schema: ...")`.
- `cmd_mox/record/scrubber.py`: `ScrubbingRule` and its `from_dict()`, used
  for the fixture's `scrubbing_rules` list. Not modified by this plan.
- `cmd_mox/record/matching.py`: `InvocationMatcher(strict=...)`. `matches()`
  decides whether a recording is compatible with a live `Invocation`: command
  and args must be equal; in strict mode stdin must be equal and every
  `env_subset` pair must be present with the same value in the live environment
  (a missing live key never matches).
  `find_match(invocation, recordings, consumed)` skips consumed indices, ranks
  compatible ones by the tuple
  `(stdin_match, matching_env_pairs, env_subset_size)`, prefers the lower
  `sequence` on a tie, and keeps the earlier list position when `sequence` is
  also equal.
- `cmd_mox/record/replay.py`:
  `ReplaySession(fixture_path, *, strict_matching=True, allow_unmatched=False)`.
  `load()` reads the fixture once (a second call raises `LifecycleError`).
  `match(invocation)` returns a `Response` and records the index in a private
  `set[int]` under a `threading.Lock`, or returns `None`. `consumed_count` and
  `is_consumed(index)` expose that state. `verify_all_consumed()` raises
  `VerificationError` listing each unconsumed recording on its own indented
  line of the form `[idx] command args`, sorted by index, unless
  `allow_unmatched` is set.
- `cmd_mox/test_doubles.py`: `CommandDouble.replay(path, *, strict=True)`
  creates a `ReplaySession`, loads it, and only then attaches it to the spy.
- `cmd_mox/controller.py`: `CmdMox._response_for_replay()` serves matched
  responses, raises `UnexpectedCommandError("No fixture recording matches: …")`
  on a strict miss, and falls back to the spy's normal response on a fuzzy miss.
  `CmdMox._verify_replay_sessions_consumed()` calls `verify_all_consumed()`
  for each double during `verify()`.
- `cmd_mox/ipc/_server_core.py`: `_execute_request` converts a handler
  exception into a `Response` with the exception text on stderr and exit status
  1, which the shim prints and exits with.

Existing tests to extend, not duplicate:

- `cmd_mox/unittests/test_fixture_file.py`: round trips, migration, version
  parsing; defines the private Hypothesis strategy `_RECORDED_INVOCATIONS`.
- `cmd_mox/unittests/test_replay_session.py`: loading, matching,
  consumption, verify, thread safety; defines `RecordedInvocationSpec`,
  `BestFitCase`, `_make_recorded_invocation`, `_make_fixture_file`,
  `_make_invocation`, and `_run_session_match`.
- `cmd_mox/unittests/test_invocation_matcher.py`: strict and fuzzy
  compatibility tables and best-fit cases; has its own copy of
  `RecordedInvocationSpec` and builders.
- `cmd_mox/unittests/test_controller_replay.py` and
  `cmd_mox/unittests/test_command_double_replay.py`: controller and fluent API
  behaviour.
- BDD: feature files live in the top-level `features/` directory
  (`features/replay_session.feature`, `features/controller.feature`); step
  modules live in `tests/steps/` (`replay_session.py`, `controller_replay.py`,
  `command_execution.py`, `assertions.py`); bindings use
  `@scenario(str(FEATURES_DIR / "x.feature"), "title")` in
  `tests/test_replay_session_bdd.py` and `tests/test_controller_bdd.py`.
  Controller scenarios that say "I run the command" spawn the real shim in a
  subprocess, so they are end-to-end. Reusable steps include
  `the exit code should be {code:d}`, `the stderr should contain "{text}"`,
  `the replay error message should contain "{text}"` (reads the `replay_error`
  fixture), `the spy has a replay session attached`, and
  `the replay match result is None`.
- Snapshots: syrupy's `snapshot` fixture, typed as
  `syrupy.assertion.SnapshotAssertion` under `TYPE_CHECKING`, compared with
  `assert value == snapshot`; `.ambr` files live in a `__snapshots__` directory
  beside the test module.

Terms used in this plan: a *property test* asserts a rule over many generated
inputs (Hypothesis `@given`). A *state machine test* generates sequences of
operations (Hypothesis `RuleBasedStateMachine` with `@rule`, `@invariant`,
`@precondition`, `@initialize`, and `teardown()`) and checks the system against
a simple model after every step. An *oracle* is an independent, obviously
correct reimplementation used to compute the expected answer. A *seeded mutant*
is a deliberately broken variant; a *witness* is a hand-built input on which
the mutant must fail, proving the check can fail.

## Relevant documentation and skills

Read before starting:

- `docs/python-native-command-mocking-design.md` section IX, especially 9.3
  (fixture format), 9.5.2 (`ReplaySession`), 9.5.5 (`InvocationMatcher`), and
  9.10.11 to 9.10.15 (matching and replay decisions).
- `docs/usage-guide.md`, "Replay sessions (fixture replay)" and its
  subsections "Creating and loading a replay session", "Matching modes",
  "Best-fit selection", "Consumed-record tracking", and "Verifying all
  recordings were consumed".
- `docs/developers-guide.md`, "Snapshot testing" and "Property-based
  testing".
- `docs/documentation-style-guide.md` for prose rules.
- `docs/execplans/12-2-5-extend-cmd-mox-verify-to-report-unconsumed-recordings.md`
  for the preceding item's conventions.
- RFC 8259 section 8.1 (UTF-8 requirement for JSON text):
  <https://www.rfc-editor.org/rfc/rfc8259#section-8.1>.
- Hypothesis API reference, including stateful testing:
  <https://hypothesis.readthedocs.io/en/latest/reference/api.html>.
- Prior art for consumption tracking: VCR.py cassettes expose `play_count`
  and `all_played` and refuse unmatched requests in record mode `none`
  (<https://vcrpy.readthedocs.io/en/latest/advanced.html>); CmdMox's
  `verify_all_consumed()` is the analogue of asserting `all_played`.

Skills to load: `python-router` first, then `python-testing` (fixtures,
parametrization, snapshots), `hypothesis` (strategies and
`RuleBasedStateMachine`), `python-verification` (non-vacuity and mutants),
`python-errors-and-logging` (validation error messages), `execplans` (to
maintain this plan), `en-gb-oxendict-style` (documentation), and
`commit-message` (commits). Use the `scrutineer` agent for full gate runs.

## Conformance basis

There is no Terms of Reference document for this repository. The upstream
artefacts are:

- `docs/roadmap.md` item 12.2.6 at commit `d22bb65` (RM-12.2.6).
- `docs/python-native-command-mocking-design.md` at commit `d22bb65`:
  9.3 fixture format (TDD-9.3), 9.5.2 `ReplaySession` (TDD-9.5.2), 9.5.5
  `InvocationMatcher` (TDD-9.5.5), 9.10.12 env subset semantics (TDD-9.10.12),
  9.10.13 best-fit scoring (TDD-9.10.13), 9.10.15 strict-miss handling
  (TDD-9.10.15).
- `AGENTS.md` testing and commit rules (STD-AGENTS).

Trace links:

```plaintext
RM-12.2.6 (loading) -> TDD-9.3, TDD-9.5.2 -> EP-M2
  -> test_fixture_file.py::TestFixtureSchemaValidation
  -> test_fixture_file.py::TestFixtureEncoding
  -> test_replay_session.py::TestReplaySessionLoading
RM-12.2.6 (matcher) -> TDD-9.5.5, TDD-9.10.12, TDD-9.10.13 -> EP-M3
  -> test_invocation_matcher.py
  -> test_invocation_matcher_properties.py
RM-12.2.6 (consumption) -> TDD-9.5.2, TDD-9.10.15 -> EP-M4
  -> test_replay_session_state_machine.py
  -> test_replay_session.py::TestVerifyAllConsumedMessage
  -> test_controller_replay.py
  -> features/replay_session.feature, features/controller.feature
```

Known deviation from an upstream expectation: TDD-9.10.15 assumes a strict miss
fails the test; over the shim it only fails the command. This plan does not
change behaviour; it records the deviation in the design document and leaves
the fix to roadmap item 12.2.7.

## Verification plan

The work introduces two production rules (field type validation and UTF-8
encoding) and pins four existing invariants. Each obligation names its method
and the control that proves the check can fail.

Axioms relied on, not verified here: Python's `json` module parses RFC 8259
JSON and `json.dumps` with default `ensure_ascii=True` emits ASCII only; the
`utf-8-sig` codec strips at most one leading byte order mark; `threading.Lock`
provides mutual exclusion; `-X warn_default_encoding` emits `EncodingWarning`
for any text I/O without an explicit encoding; Hypothesis replays failures
deterministically; syrupy compares serialized values exactly.

- Obligation LOAD-1 (round-trip acceptance): for every `FixtureFile` built
  from generated recordings, metadata (with `test_module` and `test_function`
  each present or absent), and scrubbing rules (including empty descriptions),
  `FixtureFile.from_dict(f.to_dict()) == f`, and `FixtureFile.load()` after
  `save()` returns an equal value. A golden fixture
  `cmd_mox/unittests/data/replay_golden_v1.json`, written once by the current
  writer before EP-M2 begins and checked in, must also load. Method: Hypothesis
  property plus one golden example. Rationale: the hardening must not reject
  anything the writer emits, an open-ended domain. Domain: text including
  non-ASCII and control characters, integers including negatives and zero, zero
  to five recordings. Artefact:
  `cmd_mox/unittests/test_fixture_file.py::test_fixture_file_round_trips`.
  Non-vacuity: the shared check `_assert_round_trips(fixture)` is also called
  on a fixed witness containing an empty `stdin`, an empty scrubbing
  description, and absent `test_module`, while
  `cmd_mox.record.fixture._require_str` is monkeypatched to reject empty
  strings; the call must raise. A `classify_fixture()` helper labels "non-ascii
  text", "empty recordings", "env present", and "null description", and one
  `hypothesis.find()` call per label (with `settings(database=None)`) proves
  the strategy reaches it.
- Obligation LOAD-2 (type rejection): for each field in the accepted-types
  table and each wrong JSON type that applies (null, boolean, number, string,
  list, object), loading raises `ValueError` whose message is
  `Invalid fixture schema: <path>: expected <type>, got <json type>`, and a
  missing required field gives
  `Invalid fixture schema: <path>: missing required field`. Method:
  parameterized pytest over a generated corpus (each row derived from a valid
  base fixture by replacing or deleting one field) plus one syrupy snapshot of
  the full row-to-message mapping. Rationale: the field-by-type space is finite
  and each row has a distinct meaning. Artefact:
  `cmd_mox/unittests/test_fixture_file.py::TestFixtureSchemaValidation` and
  `cmd_mox/unittests/__snapshots__/test_fixture_file.ambr`. Evidence: rows
  `recordings[0].args-string`, `recordings[0].command-null`,
  `recordings-object`, `recordings[0].exit_code-string`,
  `recordings[0].sequence-missing`, and `top-level-list` fail before EP-M2 (run
  with `xfail(strict=True)` and observed as `XFAIL`, then once without the
  marker to see the real failure) and pass after. Non-vacuity: the corpus
  builder asserts it produced at least one row per field, and the unmodified
  base fixture loads.
- Obligation LOAD-3 (UTF-8): `save()` and `load()` perform no text I/O with
  an implicit encoding; a fixture whose bytes start with a UTF-8 byte order
  mark loads; a fixture containing non-ASCII stdout written as UTF-8 bytes
  loads with the same text; `save()` output for ASCII content is unchanged.
  Method: three named examples. The first runs
  `[sys.executable, "-X",
  "warn_default_encoding", "-W", "error::EncodingWarning", "-c", <save then
  load a fixture in a temporary directory>]`
  with `stdin=subprocess.DEVNULL` and asserts exit status 0. Rationale: the
  locale cannot be faked in-process, but the interpreter's own warning detects
  a missing `encoding=` deterministically on every platform. Artefact:
  `test_fixture_file.py::TestFixtureEncoding`. Evidence: the subprocess test
  exits 1 with `EncodingWarning` before EP-M2, and the byte-order-mark test
  raises `JSONDecodeError` before EP-M2. Non-vacuity: both red failures are
  observed; the byte-identity test compares `save()` output, with newlines
  normalized, against the bytes produced by `json.dumps(..., indent=2) + "\n"`
  encoded as ASCII.
- Obligation MATCH-1 (oracle agreement): for generated scenarios (mode,
  invocation, recordings, consumed set), `find_match()` returns the same index
  as `oracle_find_match()`, which filters unconsumed compatible recordings and
  takes the maximum by `(stats, -sequence, -index)`. Method: Hypothesis
  property through a shared check `check_find_match(matcher, scenario)`.
  Rationale: scoring is a total order over an open input space; an independent
  oracle checks every component. Domain: commands and env keys from two- or
  three-letter alphabets so collisions are common, zero to six recordings,
  sequences from a small range so ties occur, `strict` drawn as a boolean.
  Artefact: `cmd_mox/unittests/test_invocation_matcher_properties.py`.
  Non-vacuity: `classify_scenario()` labels "no candidate", "single candidate",
  "won on stdin" (fuzzy only), "won on env pairs" (fuzzy only), "won on subset
  size", "won on sequence", and "won on list order"; one deterministic witness
  test per label asserts the label and oracle agreement, and one `find()` per
  label proves the strategy reaches it. Seeded mutants, each run through
  `check_find_match` on a named witness inside `pytest.raises(AssertionError)`:
  tie-break by list index instead of `sequence` (witness: equal stats, lower
  `sequence` later in the list); ignore `consumed` (witness: the best candidate
  is consumed); swap the first two score components (fuzzy witness: one
  candidate matches stdin only, another matches more env pairs only).
- Obligation MATCH-2 (mode relations): strict compatibility implies fuzzy
  compatibility; adding live environment keys absent from a recording's
  `env_subset` never removes a strict match; `find_match()` mutates neither
  `consumed` nor `recordings`. Method: Hypothesis properties in the same
  module. Non-vacuity: witness tests show a recording that is fuzzy- but not
  strict-compatible, and a strict match that survives an added key. If the
  timing tolerance bites, the non-mutation property is the first to be reduced
  to a named example.
- Obligation CONS-1 (consumption model): across any sequence of `match()`
  calls on a loaded session, the consumed index set equals that of a model that
  applies `oracle_find_match()`; `consumed_count` never decreases and increases
  by exactly one per non-`None` result; no index is returned twice; `match()`
  returns `None` exactly when the model has no compatible unconsumed recording;
  `verify_all_consumed()` raises exactly when `allow_unmatched` is false and
  some index is unconsumed, its message lists exactly the model's unconsumed
  indices in ascending order, and it does not change `consumed_count`. Method:
  Hypothesis `RuleBasedStateMachine`. Rationale: the defects this guards
  against depend on operation order. Domain: `@initialize` draws mode,
  `allow_unmatched`, and up to six recordings, writes one fixture into a
  `tempfile.TemporaryDirectory`, and loads the session; `teardown()` removes
  the directory; rules are `match_recorded` (replays a recorded invocation,
  possibly with perturbed stdin or env), `match_unknown` (an unrecorded
  command), and `verify`. Bounds: `max_examples=50`, `stateful_step_count=25`,
  which admits the shortest interesting run (exhaust six recordings, then miss)
  several times over; a reviewer's experiment ran a machine of this size in
  under 3 seconds. Artefact:
  `cmd_mox/unittests/test_replay_session_state_machine.py`. Non-vacuity: a
  scripted run of the machine (calling its rules directly in a fixed order)
  demonstrates "exhausted", "partial verify", and "complete verify"; a
  `ReplaySession` subclass whose `match()` does not record consumption is run
  through the same scripted sequence and must fail an invariant inside
  `pytest.raises(AssertionError)`.
- Obligation CONS-2 (concurrency): with N recordings and M ≥ N threads
  calling `match()` concurrently on identical invocations, exactly N calls
  return a response, M − N return `None`, and the returned stdout values are a
  permutation of the recorded ones. Method: named example with a
  `threading.Barrier`, parameterized over (N, M) in {(3, 3), (3, 8)}.
  Non-vacuity: the table includes M = N and M > N; the test asserts all M
  threads ran and joined.
- Obligation MSG-1 (message format): `verify_all_consumed()` messages for a
  zero-argument command, a multi-argument command, one unconsumed and three
  unconsumed recordings, and the controller's `UnexpectedCommandError` message,
  match snapshots. Method: syrupy snapshots, because the format is multivariant
  user-visible output. Artefacts:
  `test_replay_session.py::TestVerifyAllConsumedMessage`,
  `test_controller_replay.py`, and their `.ambr` files. Non-vacuity: snapshots
  are generated once with `--snapshot-update`, reviewed, then verified without
  the flag.

Residual gaps: Hypothesis samples rather than exhausts; witness and `find()`
tests prove every scoring class is reachable, but no exhaustive claim is made.
Thread interleavings are tested by example only. Scrubbing-rule field
validation is deferred to 12.3.2. Windows-specific newline and locale behaviour
is not exercised by Windows CI.

## Plan of work

Stage A (no production changes): run the four gates on the untouched branch and
record the pass counts in `Progress`. Before EP-M2, generate the golden fixture
`cmd_mox/unittests/data/replay_golden_v1.json` with the current
`FixtureFile.save()`, using a recording with non-empty env and stdin, a
scrubbing rule with an empty description, and metadata without `test_module`.
Re-run the shim probe recorded in `Surprises & discoveries` (scratch script in
`/tmp/12-2-6-probe/`, `stdin=subprocess.DEVNULL`) and confirm the same exit
status and stderr. Go/no-go: gates green and probe unchanged.

Stage B (EP-M1, refactor only): create the private module
`cmd_mox/unittests/_replay_helpers.py`. Move `RecordedInvocationSpec` and the
builders from `cmd_mox/unittests/test_replay_session.py`, fold in the duplicate
copies from `cmd_mox/unittests/test_invocation_matcher.py`, and expose them
without the leading underscore (`make_recorded_invocation`,
`write_fixture_file`, `make_invocation`). Move `_RECORDED_INVOCATIONS` from
`test_fixture_file.py` into the module as `recorded_invocations()`, and add
`fixture_metadata()`, `fixture_files()`, `replay_scenarios()`,
`oracle_find_match()`, and `classify_scenario()`. Update the existing tests to
import them. Their assertions must not change.

Stage C (EP-M2, red then green): add the LOAD-1 property and golden test (green
before and after), the LOAD-2 corpus and snapshot, and the LOAD-3 examples.
Also add these loading gap tests to `test_replay_session.py`: `str` path
accepted by `ReplaySession`, retry allowed after a failed `load()`, directory
path raising an `OSError` subclass, malformed version string through `load()`,
integer `version` (pinning the existing `expected str` message), and empty
recordings list. Mark the defect rows `xfail(strict=True)`, observe `XFAIL`,
and record one real failure transcript in `Artefacts and notes`. Then, in
`cmd_mox/record/fixture.py`, add a private
`_schema_error(path, expected, value)` that builds the message with a JSON type
name (`null`, `boolean`, `number`, `string`, `list`, `object`), and private
readers `_read_str`, `_read_int`, `_read_str_list`, `_read_str_mapping`,
`_read_optional_str`, and `_read_object_list`, each taking the mapping, key,
field path, and, where applicable, a default supplied through a `_REQUIRED`
sentinel. Call them from `RecordedInvocation.from_dict()` and
`FixtureMetadata.from_dict()`, check the top-level value and the `recordings`
and `scrubbing_rules` containers before use, and format `KeyError` in the
backstop with `exc.args[0]`. Pass `encoding="utf-8"` to `write_text()` in
`save()` and `encoding="utf-8-sig"` to `read_text()` in `load()`. Remove the
`xfail` markers. Commit subjects for this milestone start with "Fix" and say
the defect was found while testing for 12.2.6.

Stage D (EP-M3): add discriminating examples to
`cmd_mox/unittests/test_invocation_matcher.py`: missing live env key in strict
mode, empty `env_subset`, all candidates consumed, empty recordings list, lower
`sequence` later in the list, equal `sequence` falling back to list order, and
`env_subset_size` deciding in fuzzy mode. Add
`cmd_mox/unittests/test_invocation_matcher_properties.py` with MATCH-1,
MATCH-2, the class witnesses, the reachability checks, and the three mutant
witnesses. These characterize existing behaviour and pass on first run; the red
stage for each property is its mutant witness, which is written first and
observed failing against the property's check before the property is committed.

Stage E (EP-M4): add `cmd_mox/unittests/test_replay_session_state_machine.py`
(CONS-1 and its scripted mutant control); extend the thread-safety class in
`test_replay_session.py` with CONS-2; add `TestVerifyAllConsumedMessage`
(MSG-1) and fuzzy-mode exhaustion at session level. In
`cmd_mox/unittests/test_controller_replay.py`, add: strict exhaustion through
`_response_for_replay` raises `UnexpectedCommandError` (message snapshot);
fuzzy exhaustion falls back and records the invocation exactly once; two
doubles keep independent consumption; verify with two failing doubles reports
the first registered double. Add the BDD scenarios and step functions in
`Validation and acceptance`, confirming each new step binds with
`pytest --collect-only`.

Stage F (EP-M5): documentation and roadmap updates; see
`Interfaces and dependencies`.

## Milestones and plateaus

- EP-M1: shared test builders and strategies exist; all existing tests pass.
  Requirements: enabling work for RM-12.2.6. Acceptance: `make test` pass count
  equals the Stage A baseline. Conformance: no production change; no new
  dependency; no lint suppressions added. Recovery: revert the commit.
  Remaining gaps: all new tests. Compatibility decision: none (test-only
  surface).
- EP-M2: schema-invalid fixtures are rejected with field-path messages, and
  files use UTF-8 with byte order mark tolerance on load. Requirements:
  RM-12.2.6 loading, TDD-9.3, TDD-9.5.2. Acceptance: LOAD-1 to LOAD-3 and the
  loading gap tests pass; the listed red rows were observed failing first; the
  golden fixture loads. Conformance: only `fixture.py` changed; no schema
  version change; every writer-emitted value still loads. Recovery: revert the
  milestone's commits; the tests can be kept with `xfail(strict=True)` if the
  fix must be postponed. Remaining gaps: matcher and consumption tests.
  Compatibility decision: no source compatibility layer. Persisted-format
  consideration: fixtures written by any released CmdMox version use exactly
  the accepted types, so they still load; hand-edited fixtures that relied on
  coercion (`1.0`, `true`, numeric strings, non-string `test_module`) are now
  rejected, which is an accepted tightening documented in the usage guide.
- EP-M3: matcher behaviour fully pinned. Requirements: RM-12.2.6 matcher,
  TDD-9.5.5, TDD-9.10.12, TDD-9.10.13. Acceptance: new examples, MATCH-1,
  MATCH-2, class witnesses, and reachability checks pass; each mutant witness
  is rejected. Conformance: no production change. Recovery: revert. Remaining
  gaps: consumption. Compatibility decision: none.
- EP-M4: consumption tracking pinned at session, controller, BDD, and
  end-to-end levels. Requirements: RM-12.2.6 consumption, TDD-9.10.15.
  Acceptance: CONS-1, CONS-2, MSG-1, controller tests, and BDD scenarios pass;
  the state-machine mutant is rejected. Conformance: no production change; the
  strict-miss deviation is pinned, not altered. Recovery: revert. Remaining
  gaps: documentation. Compatibility decision: none.
- EP-M5: documentation updated and roadmap item 12.2.6 marked done. Acceptance:
  `make fmt`, `make markdownlint`, and `make nixie` pass, plus all four code
  gates. Compatibility decision: none.

Each milestone ends with `make check-fmt`, `make typecheck`, `make lint`, and
`make test` passing (run sequentially, preferably by the `scrutineer` agent),
then a commit. At each boundary, update `Progress`, `Surprises & discoveries`,
and `Outcomes & retrospective`, and re-check the conformance items above.

## Concrete steps

Run everything from the repository root. Capture gate output with `tee`:

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
uv run pytest cmd_mox/unittests/test_invocation_matcher_properties.py -q --durations=5
uv run pytest cmd_mox/unittests/test_replay_session_state_machine.py -q --durations=5
uv run coverage run -m pytest cmd_mox/unittests/test_replay_session_state_machine.py -q --durations=5
uv run pytest cmd_mox/unittests/test_replay_session.py cmd_mox/unittests/test_fixture_file.py --snapshot-update
uv run pytest tests/test_replay_session_bdd.py tests/test_controller_bdd.py -q
uv run pytest tests/test_controller_bdd.py --collect-only -q
```

Expected red output for an EP-M2 defect row before the fix, with the
`xfail(strict=True)` marker removed, is for example:

```plaintext
FAILED ...::TestFixtureSchemaValidation::test_rejects_wrong_type[recordings[0].args-string]
  Failed: DID NOT RAISE <class 'ValueError'>
```

and for the encoding subprocess test:

```plaintext
EncodingWarning: 'encoding' argument not specified
assert 1 == 0
```

For documentation-only commits run `make fmt`, `make markdownlint`, and
`make nixie`.

## Validation and acceptance

New BDD scenarios in `features/replay_session.feature`, reusing existing steps
where they exist (new steps are marked "new"):

```gherkin
  Scenario: replay session rejects a fixture with a corrupt recording
    Given a fixture file whose first recording stores args as a string
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded expecting an error
    Then the replay error message should contain "Invalid fixture schema: recordings[0].args"

  Scenario: duplicate recordings are consumed in sequence order
    Given a fixture file with "git" "status" recordings "one", "two", "three" in reverse sequence order
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    And a replay invocation of "git" with args "status" is matched 3 times
    Then the replay match stdout values are "one", "two", "three"
    And replay verify_all_consumed does not raise

  Scenario: an exhausted strict replay session returns none
    Given a fixture file with a recording of "git" with args "status"
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    And a replay invocation of "git" with args "status" is matched
    And a replay invocation of "git" with args "status" is matched again
    Then the replay match result is None
```

New steps: "a fixture file whose first recording stores args as a string", "the
replay session is loaded expecting an error" (`target_fixture="replay_error"`),
"a fixture file with "{cmd}" "{args}" recordings {stdouts} in reverse sequence
order", "a replay invocation of "{cmd}" with args "{args}" is matched {count:d}
times", and "the replay match stdout values are {stdouts}". The existing
`fixture_with_n_recordings` step is not changed, so its scenario keeps its
meaning.

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

  Scenario: exhausted strict replay fails the shim run but not verification
    Given a CmdMox controller
    And a git replay fixture exists
    And the command "git" is spied with that replay fixture
    When I replay the controller
    And I run the command "git" with arguments "status"
    Then the output should be "ok"
    When I run the command "git" with arguments "status" allowing failure
    Then the exit code should be 1
    And the stderr should contain "No fixture recording matches: git status"
    When I verify the controller
    Then the spy "git" should record 1 invocation

  Scenario: corrupt replay fixture is rejected when configuring the spy
    Given a CmdMox controller
    And a corrupt git replay fixture exists
    When the command "git" is spied with that replay fixture expecting an error
    Then the replay error message should contain "Invalid fixture schema"
    And the spy "git" should not have a replay session attached
```

New steps: "a git replay fixture with recordings "{first}" and "{second}"
exists", "I run the command "{cmd}" with arguments "{args}" allowing failure"
(worded to avoid the existing `expecting failure` pattern), "a corrupt git
replay fixture exists", "the command "{cmd}" is spied with that replay fixture
expecting an error" (`target_fixture="replay_error"`), and "the spy "{cmd}"
should not have a replay session attached". The second scenario's title and
final steps pin today's behaviour; if 12.2.7 is later implemented, that item
rewrites them.

Unit-level acceptance, by file:

- `cmd_mox/unittests/test_fixture_file.py`: `test_fixture_file_round_trips`
  and the golden test (LOAD-1), `TestFixtureSchemaValidation` (LOAD-2),
  `TestFixtureEncoding` (LOAD-3). The six LOAD-2 defect rows, the
  `EncodingWarning` subprocess test, and the byte-order-mark test fail before
  EP-M2 and pass after.
- `cmd_mox/unittests/test_replay_session.py`: loading gap cases,
  `TestVerifyAllConsumedMessage` (MSG-1), fuzzy-mode exhaustion, CONS-2.
- `cmd_mox/unittests/test_invocation_matcher.py`: the seven discriminating
  examples from Stage D.
- `cmd_mox/unittests/test_invocation_matcher_properties.py`: MATCH-1,
  MATCH-2, seven class witnesses, reachability checks, three mutant witnesses.
- `cmd_mox/unittests/test_replay_session_state_machine.py`: CONS-1, its
  scripted class demonstration, and its mutant control.
- `cmd_mox/unittests/test_controller_replay.py`: strict exhaustion with
  message snapshot, fuzzy exhaustion fallback, independent doubles, and
  first-failing-double verify.

Quality criteria: all four code gates pass at each milestone;
`make markdownlint` and `make nixie` pass for documentation; every Hypothesis
test finishes in under 10 seconds under `make test` and under 20 seconds in a
serial `coverage run`; every mutant witness test passes (meaning the check
rejected the mutant); every class witness and reachability test passes.

## Idempotence and recovery

All steps are additive edits under version control. Re-running tests is safe.
Snapshot updates are explicit (`--snapshot-update`) and must be followed by a
run without the flag. The golden fixture is generated once in Stage A and never
regenerated after EP-M2 begins; if it must be recreated, check out the
pre-EP-M2 commit to do so. Each milestone is one or more commits; revert with
`git revert <sha>`. Scratch probes live in `/tmp/12-2-6-probe/` and can be
deleted at any time.

## Artefacts and notes

The probe transcripts in `Surprises & discoveries` are the evidence for the
EP-M2 scope and the strict-miss observation. Stage A shim probe, as run during
review:

```plaintext
run1: rc=0 stdout='ok\n' stderr='' 0.13s
run2: rc=1 stdout='' stderr='No fixture recording matches: git status' 0.15s
spy invocations: 1 journal: 1
verify: passed
```

Record red and green transcripts for each defect row, the encoding tests, and
each mutant witness here during implementation.

## Interfaces and dependencies

No new dependencies. New test-only module
`cmd_mox/unittests/_replay_helpers.py` (not collected by pytest, excluded from
Skylos and mutation) exports:

```python
@dc.dataclass(frozen=True, slots=True)
class RecordedInvocationSpec:
    command: str = "git"
    args: tuple[str, ...] = ("status",)
    sequence: int = 0
    stdin: str = ""
    env_subset: Mapping[str, str] = dc.field(default_factory=dict)
    stdout: str = "ok\n"
    stderr: str = ""
    exit_code: int = 0

@dc.dataclass(frozen=True, slots=True)
class ReplayScenario:
    strict: bool
    invocation: Invocation
    recordings: tuple[RecordedInvocation, ...]
    consumed: frozenset[int]

def make_recorded_invocation(spec: RecordedInvocationSpec) -> RecordedInvocation: ...
def write_fixture_file(directory: Path, recordings: Sequence[RecordedInvocation]) -> Path: ...
def make_invocation(
    command: str, args: Sequence[str], *, stdin: str = "", env: Mapping[str, str] | None = None
) -> Invocation: ...
def recorded_invocations() -> st.SearchStrategy[RecordedInvocation]: ...
def fixture_metadata() -> st.SearchStrategy[FixtureMetadata]: ...
def fixture_files() -> st.SearchStrategy[FixtureFile]: ...
def replay_scenarios() -> st.SearchStrategy[ReplayScenario]: ...
def oracle_find_match(scenario: ReplayScenario) -> int | None: ...
def classify_scenario(scenario: ReplayScenario) -> str: ...
```

Field names and defaults follow the existing `RecordedInvocationSpec` in
`test_replay_session.py`; where the two existing copies differ, keep the
`test_replay_session.py` version and adapt the matcher tests. Every function
respects `max-args = 4` (keyword-only parameters count).

Private production helpers added to `cmd_mox/record/fixture.py`:

```python
_REQUIRED: typ.Final = object()

def _schema_error(path: str, expected: str, value: object) -> ValueError: ...
def _read_str(data: Mapping[str, object], key: str, path: str, default: str | object = _REQUIRED) -> str: ...
def _read_int(data: Mapping[str, object], key: str, path: str, default: int | object = _REQUIRED) -> int: ...
def _read_optional_str(data: Mapping[str, object], key: str, path: str) -> str | None: ...
def _read_str_list(data: Mapping[str, object], key: str, path: str) -> list[str]: ...
def _read_str_mapping(data: Mapping[str, object], key: str, path: str) -> dict[str, str]: ...
def _read_object_list(data: Mapping[str, object], key: str, path: str) -> list[Mapping[str, object]]: ...
```

`RecordedInvocation.from_dict()` and `FixtureMetadata.from_dict()` gain a
keyword-only `path` parameter only if needed to build field paths (for example
`recordings[0]`); if that would change a public signature, pass the path
through a private module-level function instead
(`_recording_from_dict (data, path)`) and keep `from_dict` delegating to it.

Documentation changes in EP-M5:

- `docs/python-native-command-mocking-design.md`: add 9.10.16 "Fixture field
  validation" (the accepted-types table, the message format, and the 12.3.2
  deferral) and 9.10.17 "UTF-8 fixture encoding" (RFC 8259, byte order mark
  tolerance, `ensure_ascii` unchanged); in 9.5.5 state that a missing live
  environment key never satisfies an `env_subset` pair and that equal
  `sequence` values fall back to list order; amend 9.10.15 with the
  strict-miss-over-shim observation and a reference to 12.2.7; in 9.8 note that
  verify stops at the first double with unconsumed recordings.
- `docs/usage-guide.md`: in "Creating and loading a replay session", state
  that fixtures are UTF-8, that a byte order mark is tolerated, and that a
  field of the wrong type raises `ValueError` starting
  `Invalid fixture schema:` with the field path (no longer coerced); in
  "Best-fit selection", state the equal-sequence rule; in "Matching modes",
  state that a strict miss makes the command exit 1 with the message on stderr
  and is not reported by `verify()`.
- `docs/developers-guide.md`: extend "Property-based testing" with the
  replay properties, the state machine, the oracle, the witness and
  seeded-mutant convention, and why mutant runs are never generated; extend
  "Snapshot testing" with the new `.ambr` files and the no-paths rule; add
  "Replay test helpers" describing `cmd_mox/unittests/_replay_helpers.py`.
- `docs/roadmap.md`: mark 12.2.6 done (`- [x]`). Item 12.2.7 is already
  present.

## Revision note

- 2026-10-23: initial draft.
- 2026-10-23: revised after a six-lens expert review. Changes: scrubbing-rule
  validation deferred to 12.3.2 and `scrubber.py` removed from scope (import
  cycle, `null` descriptions); explicit accepted-types table and nullability;
  single-message schema errors to avoid `KeyError` quoting; LOAD-3 rewritten
  around `EncodingWarning` and byte order mark tests because locale
  monkeypatching cannot fail; mutant controls and class coverage made
  deterministic with witnesses; scenario mode drawn so fuzzy classes are
  reachable; state machine teardown and timing bounds added; helper signatures
  reshaped for `max-args = 4`; BDD steps reuse existing phrasing; the Stage A
  shim probe was run and its result (exit 1, verify passes) is pinned,
  documented, and proposed as 12.2.7. Effect on remaining work: EP-M2 is
  smaller and touches one file; EP-M4 gains a definite end-to-end scenario;
  EP-M5 gains the strict-miss documentation.
- 2026-10-23: recorded the user's decisions. The defect fixes stay in the
  12.2.6 pull request as separate commits, and roadmap item 12.2.7 was added in
  the planning pull request. Effect on remaining work: EP-M5 no longer adds
  12.2.7; the design and usage documentation refer to it by number.
