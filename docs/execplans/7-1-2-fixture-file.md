# Add Schema Migration Support for Forward Compatibility to FixtureFile

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

The `FixtureFile` class in `cmd_mox/record/fixture.py` currently hard-rejects
any fixture whose schema version is not exactly `"1.0"`. This means that when a
future version adds an optional field (minor bump to `"1.1"`) or the schema
evolves significantly (major bump to `"2.0"`), existing fixture files become
unloadable and newer fixture files cannot be read by older code.

After this change a developer can observe the following behaviour:

- A fixture file saved with an older schema version (e.g. `"0.9"`) is
  automatically migrated to the current `"1.0"` schema when loaded via
  `FixtureFile.load()` or `FixtureFile.from_dict()`.
- A fixture file saved with a higher minor version within the same major (e.g.
  `"1.1"`) loads successfully into `"1.0"` code because unknown fields are
  ignored and the data is structurally compatible.
- A fixture file with an incompatible major version and no registered migration
  path (e.g. `"99.0"`) raises `ValueError` with a clear message.
- A migration registry exists so future schema changes can register transform
  functions that chain together (e.g. v0 -> v1 -> v2).

This completes the final unchecked roadmap item under XII-A in
`docs/cmd-mox-roadmap.md`:

```plaintext
- [ ] Schema migration support for forward compatibility
```

## Constraints

- Python 3.12+ only; `from __future__ import annotations` in every file.
- Zero new external dependencies. Version comparison uses stdlib only.
- `@dataclass(slots=True)` for any new dataclasses.
- Import aliases per `.rules/python-typing.md`: `import datetime as dt`,
  `import dataclasses as dc`, `import typing as t`.
- Type-only imports go in `if t.TYPE_CHECKING:` blocks; runtime-needed types
  use inline imports where necessary.
- NumPy-style docstrings on all public functions and classes.
- Cyclomatic complexity max 9 per function (Ruff C90).
- Max 4 positional function parameters (Ruff PLR0913).
- Test-driven development (TDD) workflow per `AGENTS.md`: write tests first
  (red), then implement (green), then quality gates.
- The public signatures of `FixtureFile.from_dict()`, `FixtureFile.load()`,
  `FixtureFile.save()`, and `FixtureFile.to_dict()` must not change.
- `FixtureFile.SCHEMA_VERSION` remains `"1.0"` -- this plan adds the migration
  framework, not a new schema version.
- Existing tests must not break.
- Quality gates: `make check-fmt`, `make typecheck`, `make lint`, `make test`
  must all pass. For markdown changes: `make markdownlint`.

## Tolerances (exception triggers)

- Scope: if implementation requires changes to more than 8 files or 250 net
  new lines (excluding this execplan), stop and escalate.
- Interface: if any existing public API signature must change, stop and
  escalate.
- Dependencies: if a new external dependency is required, stop and escalate.
- Iterations: if quality gates still fail after 3 targeted fix attempts, stop
  and escalate with captured logs.
- Ambiguity: if the design spec is ambiguous on a normative point, document
  the interpretation in Decision Log and proceed with the conservative choice.

## Risks

- Risk: The design spec Section 9.11 example uses string comparison for
  versions (`if version < "1.0"`), which is unreliable for multi-digit versions
  (e.g. `"9.0" < "10.0"` is `False` as strings). Severity: medium. Likelihood:
  certain. Mitigation: Use `tuple[int, int]` comparison instead. Document this
  divergence from the spec's literal example in the Decision Log.

- Risk: Since v1.0 is the first schema version, there is no real pre-1.0 data
  to migrate. Tests must use synthetic data. Severity: low. Likelihood:
  certain. Mitigation: Provide a placeholder `_migrate_v0_to_v1` function that
  sets `"version": "1.0"`. This exercises the full migration pipeline
  end-to-end and serves as a template for future migrations.

- Risk: PEP 695 `type` statement syntax may cause issues with `ty` or `ruff`.
  Severity: low. Likelihood: low. Mitigation: Use `t.TypeAlias` or inline the
  callable type if needed.

## Progress

- [x] (2026-02-27) Write this ExecPlan and obtain approval.
- [x] (2026-02-27) Stage A: Write failing unit tests for version parsing and
  migration. 7 new tests (4 version parsing + 3 migration).
- [x] (2026-02-27) Stage B: Write failing behaviour-driven development (BDD)
  scenario for fixture migration. 1 new scenario with 4 step definitions.
- [x] (2026-02-27) Stage C: Implement migration infrastructure in
  `fixture.py`. Added `_parse_version`, `_migrate_v0_to_v1`, `_MIGRATIONS`
  registry, `_apply_migrations`; refactored `from_dict()`.
- [x] (2026-02-27) Stage D: Run quality gates, fix issues. Fixed 2 lint
  errors (UP040: use PEP 695 `type` keyword; RUF100: unused noqa directive).
- [x] (2026-02-27) Stage E: Update documentation. Roadmap checkbox marked,
  usage guide migration subsection added, design doc decision 9.10.8 added.
- [x] (2026-02-27) Stage F: Final quality gates. All pass: 606 tests passed,
  12 skipped; 0 lint errors; 0 type diagnostics; 0 markdown lint errors.

## Surprises & discoveries

- Observation: Ruff UP040 requires PEP 695 `type` statement instead of
  `t.TypeAlias` annotation. The `type _MigrationFn = ...` syntax works
  correctly with both `ty 0.0.19` and `ruff`. Evidence: `make lint` flagged
  `t.TypeAlias` as UP040; switching to `type` keyword resolved it. Impact:
  Future type aliases in this project should use PEP 695 `type` statement
  directly.

- Observation: PLR2004 (magic number comparison) is not enabled for this
  project's source files, only suppressed in test files via per-file-ignores. A
  `# noqa: PLR2004` on the `len(parts) != 2` check was unnecessary and
  triggered RUF100. Evidence: `make lint` flagged the unused noqa directive.
  Impact: Only add noqa comments after confirming the rule is actually enabled.

## Decision log

- Decision: Keep all migration functions in `cmd_mox/record/fixture.py` rather
  than a separate `migrations.py` module. Rationale: The migration table is
  small (initially one placeholder entry), tightly coupled to
  `FixtureFile.from_dict()`, and benefits from proximity to the schema
  definitions. A separate module would be premature abstraction. If the table
  grows beyond 3-4 entries, extraction can be a follow-up. Date/Author:
  2026-02-27 / DevBoxer agent.

- Decision: Use `tuple[int, int]` for version comparison, not string
  comparison. Rationale: String comparison of version numbers is unreliable
  (e.g. `"9.0" < "10.0"` is `False`). Tuple comparison with integers is
  correct, explicit, and requires no dependencies. This diverges from the
  literal code in Section 9.11.2 but preserves its intent. Date/Author:
  2026-02-27 / DevBoxer agent.

- Decision: Implement chainable migrations keyed by source major version
  (i.e. `_MIGRATIONS: dict[int, ...]`), not by exact `(major, minor)` tuple.
  Rationale: A migration from major version 0 applies to any 0.x variant.
  Keying by major avoids registering an entry for every possible minor version.
  Chaining lets each migration only know about two adjacent major versions
  (standard pattern from Django/Alembic). Date/Author: 2026-02-27 / DevBoxer
  agent.

- Decision: Minor version tolerance means same-major files always load. After
  migration, the in-memory `FixtureFile` normalizes its `version` field to
  `SCHEMA_VERSION` (`"1.0"`). Rationale: The semantic versioning contract says
  minor versions add optional fields but do not break existing readers. The
  existing `from_dict()` implementations use `.get()` with defaults, so unknown
  extra fields are silently ignored. Normalizing the version avoids carrying
  forward a version string that the code does not fully understand.
  Date/Author: 2026-02-27 / DevBoxer agent.

## Outcomes & retrospective

### Outcomes

All acceptance criteria met:

- `make test`: 606 passed, 12 skipped. 8 new tests (4 version parsing + 3
  migration unit tests + 1 BDD scenario).
- `make lint`: All checks passed.
- `make check-fmt`: All checks passed.
- `make typecheck`: 0 diagnostics (ty 0.0.19).
- `make markdownlint`: 0 errors.
- `docs/cmd-mox-roadmap.md`: "Schema migration support" checkbox marked `[x]`.
- `docs/usage-guide.md`: Contains "Schema versioning and migration" subsection.
- `docs/python-native-command-mocking-design.md`: Decision 9.10.8 added.
- Existing roundtrip tests (`test_save_and_load`, `test_from_dict_roundtrip`)
  pass unchanged.

### Retrospective

- The implementation was straightforward due to the clean existing codebase
  and well-defined design spec. Total net new code: ~90 lines in `fixture.py`,
  ~55 lines in test files, ~30 lines in BDD files, ~15 lines in docs.
- Two lint surprises (UP040 and PLR2004 noqa) were resolved in one iteration.
- The PEP 695 `type` statement works well with both `ty` and `ruff` on Python
  3.12+; prefer it over `t.TypeAlias` in this project going forward.
- The TDD red-green cycle was clean: 7 failing tests in red phase, all green
  after a single implementation pass.

## Context and orientation

CmdMox is a Python-native command mocking library. Its Record Mode captures
passthrough spy interactions and persists them as versioned JSON fixture files.
The predecessor ExecPlan (`docs/execplans/7-1-1-recording-session-class.md`,
status COMPLETE) implemented the `RecordingSession`, `FixtureFile`, and all
supporting models. This plan adds the final XII-A item: schema migration.

### Key files

- `cmd_mox/record/fixture.py` (189 lines) -- Contains `RecordedInvocation`,
  `FixtureMetadata`, and `FixtureFile` dataclasses. `FixtureFile.from_dict()`
  hard-rejects versions != `"1.0"` at line 161. `FixtureFile.load()` delegates
  to `from_dict()`. Module constant `_SCHEMA_VERSION = "1.0"` at line 22.
- `cmd_mox/record/scrubber.py` (64 lines) -- `ScrubbingRule` dataclass and
  `Scrubber` protocol, imported by `fixture.py`.
- `cmd_mox/record/__init__.py` (20 lines) -- Public re-exports.
- `cmd_mox/unittests/test_fixture_file.py` (207 lines) -- Unit tests for
  fixture models. `test_from_dict_rejects_incompatible_version` at line 179
  asserts version `"99.0"` raises `ValueError`.
- `features/recording_session.feature` (34 lines) -- BDD scenarios.
- `tests/steps/recording_session.py` (138 lines) -- Step definitions.
- `tests/test_recording_session_bdd.py` (36 lines) -- Scenario wiring.
- `docs/usage-guide.md` -- User-facing docs; "Recording sessions" section at
  lines 339-434.
- `docs/cmd-mox-roadmap.md` -- Roadmap; unchecked item at line 242.
- `docs/python-native-command-mocking-design.md` -- Design spec; Section 9.11
  covers versioning and forward compatibility.

### Build targets

- `make test` -- `uv run pytest -v -n auto`
- `make lint` -- `ruff check`
- `make check-fmt` -- `ruff format --check` + `mdformat --check`
- `make typecheck` -- `ty check`
- `make markdownlint` -- markdown linting
- `make fmt` -- auto-format

### Typecheck baseline

The current typecheck baseline is 0 diagnostics (per project memory notes in
the Qdrant vector store: the previous 2 diagnostics in `expectations.py` were
fixed).

## Plan of work

### Stage A: Write failing unit tests (red phase)

Edit `cmd_mox/unittests/test_fixture_file.py` to add:

1. A new `TestVersionParsing` class with 4 tests exercising the
   `_parse_version()` helper that will be added to `fixture.py`:
   - `test_parse_simple_version`: `_parse_version("1.0")` returns `(1, 0)`.
   - `test_parse_minor_version`: `_parse_version("1.1")` returns `(1, 1)`.
   - `test_parse_zero_version`: `_parse_version("0.9")` returns `(0, 9)`.
   - `test_parse_invalid_version_raises`: `_parse_version("abc")` raises
     `ValueError` matching `"Invalid"`.

2. Three new tests in the existing `TestFixtureFile` class:
   - `test_from_dict_migrates_old_version`: build a v1.0 fixture dict, set
     `version` to `"0.9"`, call `FixtureFile.from_dict()`, assert the result
     has `version == "1.0"` and recordings are intact.
   - `test_from_dict_tolerates_higher_minor_version`: build a v1.0 dict, set
     `version` to `"1.1"`, add an unknown key `"new_future_field"`, call
     `from_dict()`, assert `version == "1.0"` and data loads correctly.
   - `test_from_dict_rejects_incompatible_major_version`: build a v1.0 dict,
     set `version` to `"99.0"`, assert `from_dict()` raises `ValueError`
     matching `"99.0"`.

3. Remove or rename the existing `test_from_dict_rejects_incompatible_version`
   (line 179) since the new `test_from_dict_rejects_incompatible_major_version`
   covers the same scenario with a more precise name.

**Validation**: run `make test` -- new tests should fail (import errors or
assertion failures). Existing tests should still pass.

### Stage B: Write failing BDD scenario (red phase)

Add a fourth scenario to `features/recording_session.feature`:

```gherkin
Scenario: loading a fixture with an older schema version migrates it
  Given a v0.9 fixture file on disk
  When the fixture file is loaded
  Then the loaded fixture has version "1.0"
  And the loaded fixture contains 1 recording
```

Add step definitions in `tests/steps/recording_session.py`:

- `@given("a v0.9 fixture file on disk", target_fixture="fixture_path")`:
  create a temporary fixture JSON file with `"version": "0.9"` and one
  recording, write it to `tmp_path / "old_fixture.json"`, return the path.
- `@when("the fixture file is loaded", target_fixture="loaded_fixture")`:
  call `FixtureFile.load(fixture_path)` and return the result.
- `@then(parsers.parse('the loaded fixture has version "{version}"'))`:
  assert `loaded_fixture.version == version`.
- `@then(parsers.re(...))`: variant of existing recording-count step that
  uses `loaded_fixture`.

Wire the scenario in `tests/test_recording_session_bdd.py`.

**Validation**: run `make test` -- the new BDD test should fail. Existing BDD
tests should still pass.

### Stage C: Implement migration infrastructure (green phase)

Edit `cmd_mox/record/fixture.py` to add the following, all as private
(underscore-prefixed) module internals:

1. `_parse_version(version_str: str) -> tuple[int, int]` -- splits on `"."`,
   converts to `(int, int)`, raises `ValueError` on bad input.

2. `_migrate_v0_to_v1(data: dict[str, t.Any]) -> dict[str, t.Any]` -- a
   placeholder migration that sets `data["version"] = "1.0"` and returns
   `data`. This exercises the pipeline; v0.x is hypothetical.

3. `_MIGRATIONS: dict[int, tuple[tuple[int, int], t.Callable[...]]]` --
   maps source major version to `(target_version_tuple, migration_fn)`. Initial
   contents: `{0: ((1, 0), _migrate_v0_to_v1)}`.

4. `_apply_migrations(data: dict[str, t.Any]) -> dict[str, t.Any]` -- the
   core migration loop:
   - Parse the file's version and the current `_SCHEMA_VERSION` into tuples.
   - If the file's major matches the current major, return data unchanged
     (minor version tolerance).
   - If the file's major is higher than the current major, raise `ValueError`.
   - Otherwise, loop: look up `_MIGRATIONS[file_major]`, apply the migration
     function, update the file version tuple, repeat until majors match.
   - If no migration is found for a given major, raise `ValueError`.

5. Refactor `FixtureFile.from_dict()` to call `_apply_migrations(data)` as
   its first step, replacing the current hard version check. After migration,
   always set `version=cls.SCHEMA_VERSION` on the constructed instance (the
   data has been normalized).

**Validation**: run `make test` -- all tests (old and new) should pass.

### Stage D: Quality gates

Run in sequence, piping through `tee` per `CLAUDE.md` guidance:

```bash
set -o pipefail && make check-fmt 2>&1 | tee /tmp/checkfmt.log
set -o pipefail && make typecheck 2>&1 | tee /tmp/typecheck.log
set -o pipefail && make lint 2>&1 | tee /tmp/lint.log
set -o pipefail && make test 2>&1 | tee /tmp/test.log
```

Fix any issues found. Re-run all gates after fixes.

### Stage E: Documentation updates

1. `docs/cmd-mox-roadmap.md` line 242: change `[ ]` to `[x]` for "Schema
   migration support for forward compatibility".

2. `docs/usage-guide.md`: add a "Schema versioning and migration" subsection
   after the "Fixture file format" subsection (after line 423, before
   "Pipelines and shell syntax").

3. `docs/python-native-command-mocking-design.md`: add a design decision
   entry documenting the tuple-based version comparison and migration registry
   design.

4. Run `make markdownlint` to validate markdown changes.

### Stage F: Final validation and completion

Run all quality gates one final time. Update this ExecPlan to status COMPLETE.
Update Progress section with timestamps.

## Concrete steps

All commands run from `/home/user/project`.

1. Write the ExecPlan to `docs/execplans/7-1-2-fixture-file.md`.
2. Edit `cmd_mox/unittests/test_fixture_file.py`: add `TestVersionParsing`
   (4 tests), add 3 migration tests to `TestFixtureFile`, remove the old
   `test_from_dict_rejects_incompatible_version`.
3. Edit `features/recording_session.feature`: add migration scenario.
4. Edit `tests/steps/recording_session.py`: add step definitions for
   migration scenario.
5. Edit `tests/test_recording_session_bdd.py`: wire migration scenario.
6. Run `make test` -- confirm new tests fail, existing tests pass.
7. Edit `cmd_mox/record/fixture.py`: add `_parse_version`,
   `_migrate_v0_to_v1`, `_MIGRATIONS`, `_apply_migrations`; refactor
   `from_dict()`.
8. Run `make test` -- all tests pass.
9. Run `make check-fmt && make typecheck && make lint` -- all pass.
10. Edit `docs/cmd-mox-roadmap.md`: mark checkbox done.
11. Edit `docs/usage-guide.md`: add migration subsection.
12. Edit `docs/python-native-command-mocking-design.md`: add decision entry.
13. Run `make markdownlint`.
14. Run full quality gates one final time.
15. Update ExecPlan to COMPLETE.

## Validation and acceptance

The change is accepted when all of the following hold:

- `make test` passes with all new tests green:
  - `test_parse_simple_version`
  - `test_parse_minor_version`
  - `test_parse_zero_version`
  - `test_parse_invalid_version_raises`
  - `test_from_dict_migrates_old_version`
  - `test_from_dict_tolerates_higher_minor_version`
  - `test_from_dict_rejects_incompatible_major_version`
  - `test_fixture_migration_from_old_version` (BDD)
- `make lint` passes with no new violations.
- `make check-fmt` passes.
- `make typecheck` produces 0 diagnostics (matching current baseline).
- `make markdownlint` passes for edited markdown files.
- `docs/cmd-mox-roadmap.md` line 242 shows `[x]`.
- `docs/usage-guide.md` contains "Schema versioning and migration" subsection.
- Existing fixture roundtrip tests (`test_save_and_load`,
  `test_from_dict_roundtrip`) still pass unchanged.

Quality method:

```bash
set -o pipefail && make check-fmt 2>&1 | tee /tmp/checkfmt.log
set -o pipefail && make typecheck 2>&1 | tee /tmp/typecheck.log
set -o pipefail && make lint 2>&1 | tee /tmp/lint.log
set -o pipefail && make test 2>&1 | tee /tmp/test.log
set -o pipefail && make markdownlint 2>&1 | tee /tmp/mdlint.log
```

## Idempotence and recovery

All steps are idempotent. The migration framework is purely additive -- it does
not modify the on-disk format of v1.0 fixtures. `_apply_migrations()` is a
no-op for v1.0 data, so existing save/load roundtrips are unchanged.

If a step fails partway, re-running `make test` after fixing the issue is safe.
No state machines or filesystem mutations beyond JSON file I/O are involved.

## Artifacts and notes

### Files to create

- `docs/execplans/7-1-2-fixture-file.md` -- this ExecPlan.

### Files to modify

- `cmd_mox/record/fixture.py` -- add `_parse_version`, `_migrate_v0_to_v1`,
  `_MIGRATIONS`, `_apply_migrations`; refactor `from_dict()`.
- `cmd_mox/unittests/test_fixture_file.py` -- add `TestVersionParsing` class,
  3 migration tests, remove old version rejection test.
- `features/recording_session.feature` -- add migration BDD scenario.
- `tests/steps/recording_session.py` -- add step definitions.
- `tests/test_recording_session_bdd.py` -- wire new scenario.
- `docs/cmd-mox-roadmap.md` -- mark checkbox done.
- `docs/usage-guide.md` -- add migration subsection.
- `docs/python-native-command-mocking-design.md` -- add design decision.

Expected: ~80-100 net new lines in `fixture.py`, ~50-60 in test files, ~20-30
in BDD files, ~10 in docs. Total ~160-200 net new lines across 8 modified
files and 1 new file. Within the 250-line/8-file tolerance.

## Interfaces and dependencies

No new external dependencies. All new code uses only the Python standard
library.

### New private functions in `cmd_mox/record/fixture.py`

```python
def _parse_version(version_str: str) -> tuple[int, int]:
    """Parse a 'major.minor' version string into a comparable tuple."""
    ...

def _migrate_v0_to_v1(data: dict[str, t.Any]) -> dict[str, t.Any]:
    """Migrate a v0.x fixture dict to v1.0 format."""
    ...

def _apply_migrations(data: dict[str, t.Any]) -> dict[str, t.Any]:
    """Apply chained migrations to bring data up to the current schema."""
    ...

# Module-level registry:
_MIGRATIONS: dict[int, tuple[tuple[int, int], t.Callable[[dict[str, t.Any]], dict[str, t.Any]]]]
```

### Modified method (signature unchanged)

```python
@classmethod
def from_dict(cls, data: dict[str, t.Any]) -> FixtureFile:
    """Construct from a JSON-compatible mapping.

    Older schema versions are migrated forward automatically. Minor
    version differences within the same major version are tolerated.
    """
    ...
```
