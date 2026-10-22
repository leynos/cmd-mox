# Implement internal `atomic_write_json` for fixture and state files

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`,
`Decision log`, `Outcomes & retrospective`, `Conformance basis`, and
`Verification plan` must be kept up to date as work proceeds.

Status: DRAFT

## Purpose / big picture

Roadmap item `14.1.1` asks whether CmdMox can write JSON fixture and state
files durably without changing its public record and replay API. Today,
`cmd_mox.record.fixture.FixtureFile.save()` calls `Path.write_text(...)`
directly. If the process is interrupted or the disk fills part-way through that
call, the fixture on disk is left truncated, and the next replay fails with a
JSON decoding error. The file is also created with whatever permissions the
process umask allows, usually world-readable, although fixtures can contain
environment-derived values and command output.

After this change, CmdMox contains one internal persistence boundary,
`cmd_mox._atomic_write.atomic_write_json(path, payload, *, mode=0o600)`, that
every later JSON writer (fixtures in `14.1.2`, the state store in `14.3.1`, and
the side-effect helpers in `14.5.1`) can share. A maintainer can observe
success in five ways:

1. Writing a payload creates the target and any missing parent directories,
   and the file contains exactly the payload serialized as two-space-indented
   JSON with a trailing newline, encoded as UTF-8.
2. Injecting a failure into any step before the final rename (serialization,
   directory creation, temporary-file creation, writing, flushing, `fsync`, or
   `os.replace`) leaves the previous target file byte-for-byte unchanged and
   leaves no temporary file behind.
3. A child process killed with `SIGKILL` while paused just before the rename
   leaves the previous complete JSON document in place.
4. On POSIX, a successful write leaves the target with mode `0o600` (or the
   caller's requested mode) regardless of the process umask and the previous
   file's mode.
5. `make check-fmt`, `make typecheck`, `make lint`, and `make test` pass, and
   the new tests fail before the implementation and pass afterwards.

The helper is internal. It is not exported from `cmd_mox/__init__.py`, and no
public CmdMox behaviour changes until `14.1.2` routes `FixtureFile.save()`
through it.

## Relevant documentation and skills

Keep these references open while executing the plan:

- `docs/roadmap.md`, phase 14 and item `14.1.1` (and `14.1.2`, which consumes
  this helper).
- `docs/cmd-mox-fake-capabilities-design.md`, especially §§Stability policy,
  Problem, Goals, Prior art and constraints, Atomic JSON persistence
  (including Persistence exceptions and Failure semantics), Security and
  portability, and Verification strategy. This is the governing design.
- `docs/python-native-command-mocking-design.md` §9.5.3 (`FixtureFile`) and
  §9.10 (design decisions), for context on fixture persistence. No edit is
  planned there in `14.1.1`.
- `docs/developers-guide.md` §§Snapshot testing, Property-based testing, and
  Skylos dead-code policy.
- `docs/documentation-style-guide.md` for en-GB-oxendict spelling, 80-column
  wrapping, and ADR format.
- `AGENTS.md` for the quality gates and the Skylos policy.
- Skills: `execplans` (keep this document current), `python-router` then
  `python-errors-and-logging` (exception design and chaining),
  `python-types-and-apis` (the `JsonValue` alias and signature),
  `python-testing` (fixtures, parametrization, syrupy), `hypothesis` (property
  and stateful tests), `python-verification` (if escalation beyond Hypothesis
  is considered), `mutmut` (mutation audit), `skylos` (dead-code findings),
  `leta` or `codegraph-mcp` for navigation, and `en-gb-oxendict-style` for
  documentation edits.

## Constraints

- Do not change any public API. `atomic_write_json`, `AtomicWriteError`,
  `AtomicWriteStage`, and `JsonValue` must not be added to `cmd_mox/__init__.py`
  or to any `__all__`.
- Do not modify `cmd_mox/record/fixture.py` in this item. Routing
  `FixtureFile.save()` through the helper is roadmap item `14.1.2`, unless the
  user approves Option C of open decision `DEC-OPEN-1` below.
- Do not add runtime or development dependencies. Hypothesis, syrupy,
  pytest-bdd, pytest-timeout, and pytest-xdist are already in the `dev` group.
- Support Python 3.12 and later (`requires-python = ">=3.12"`). In particular,
  `os.fchmod` exists on Windows only from Python 3.13, so the code must not
  assume it.
- Keep Windows support intact. The helper must import and run on Windows, and
  any skipped POSIX-only step must be skipped explicitly in code.
- Create temporary files in the target's own directory so `os.replace` stays
  on one filesystem.
- Never roll back the target after `os.replace` succeeds.
- Follow the repository's Ruff, Pylint, DF12, ty, and Skylos policies without
  unexplained suppressions. Every suppression must carry a reason.
- Prose follows en-GB-oxendict spelling and wraps at 80 columns.

## Tolerances (exception triggers)

- Scope: if implementation needs more than 14 changed files or 1,100 net
  added lines (tests included), stop and escalate.
- Interface: if the helper must be exported, or any existing public signature
  must change, stop and escalate.
- Dependencies: if any new dependency appears necessary (for example
  `crosshair-tool` or `portalocker`), stop and escalate.
- Iterations: if a gate still fails after three fix attempts for the same
  cause, stop and escalate.
- Time: if any milestone takes more than four hours of active work, stop and
  record the reason before continuing.
- Ambiguity: if the governing design and this plan disagree in a way that
  changes on-disk bytes, error types, or file modes, stop and present options.
- Platform: if a planned test cannot be made reliable on Linux CI (flaky in
  more than one of ten local repetitions), stop and escalate rather than adding
  retries or sleeps.

## Risks

- Risk: the blocking Skylos scan reports `atomic_write_json` as dead code,
  because nothing in `cmd_mox/` calls it until `14.1.2` and tests are excluded
  from the scan by design. Severity: high. Likelihood: high. Mitigation: open
  decision `DEC-OPEN-1` must be settled before implementation starts.
- Risk: `os.umask` is process-wide, so tests that change it could race with
  other threads in the same worker. Severity: low. Likelihood: low.
  Mitigation: change the umask only inside a context-managed fixture that
  restores it, and keep those tests single-threaded. `pytest-xdist` workers
  are separate processes.
- Risk: `fsync` makes tests slow on some filesystems. Severity: low.
  Likelihood: medium. Mitigation: bound Hypothesis examples (`max_examples`
  of 50 for the round trip, 30 state-machine runs of at most 8 steps) and set
  `deadline=None`.
- Risk: on Windows, `os.replace` fails with `PermissionError` while another
  process holds the target open without delete sharing. Severity: medium.
  Likelihood: medium in parallel use. Mitigation: out of scope for `14.1.1`;
  the failure surfaces as `AtomicWriteError` with stage `replace`, the old file
  survives, and the limitation is documented. Retrying belongs with the lock
  work in `14.2`.
- Risk: some POSIX filesystems (certain network and FUSE mounts) reject
  `fsync` on a directory descriptor with `EINVAL`. Severity: medium.
  Likelihood: low. Mitigation: the design requires reporting post-replace
  synchronization failures, so the helper reports them with
  `target_replaced=True`. Revisit in `14.1.2` if fixture saving trips on such a
  filesystem.
- Risk: mutation testing (`.github/workflows/mutation-testing.yml`) mutates the
  new module, and weak assertions on mode bits, flags, or cleanup would let
  mutants survive. Severity: low. Likelihood: medium. Mitigation: Stage D runs
  `mutmut` on the module and promotes surviving mutants to tests.
- Risk: the new unit tests do not run on Windows CI, because the Windows job
  runs only `make windows-smoke`. Severity: medium. Likelihood: certain unless
  addressed. Mitigation: add the new unit-test module to `WINDOWS_SMOKE_ARGS`
  in the `Makefile`.

## Progress

- [x] (2026-10-23) Renamed the branch to
  `14-1-1-implement-internal-atomic-write-json` with `origin` tracking.
- [x] (2026-10-23) Reconnaissance of code, tests, tooling, and documentation.
- [x] (2026-10-23) External research on `os.replace`, `os.fsync`,
  `os.fchmod`, `python-atomicwrites`, and the `fake-uv.py` prior art.
- [x] (2026-10-23) First draft of this plan.
- [ ] Expert-panel design review and revision.
- [ ] User approval of the plan and of `DEC-OPEN-1`.
- [ ] Stage A: red tests (EP-M1).
- [ ] Stage B: green implementation (EP-M2).
- [ ] Stage C: Windows coverage, refactor, and mutation audit (EP-M3).
- [ ] Stage D: documentation and roadmap (EP-M4).

## Surprises & discoveries

- Observation: there is no `docs/users-guide.md`; the consumer guide is
  `docs/usage-guide.md`. Evidence: `ls docs`. Impact: user-facing notes, if
  any, go to `docs/usage-guide.md`. None are needed in `14.1.1`.
- Observation: no existing test asserts file permissions, umask behaviour, or
  the exact on-disk bytes of a fixture. Evidence: repository-wide search for
  `S_IMODE`, `st_mode`, and `umask` under `tests/` and `cmd_mox/unittests/`.
  Impact: the permission and snapshot tests here are the first of their kind,
  so they set the convention recorded in `docs/developers-guide.md`.
- Observation: the Windows CI job runs only `make windows-smoke`, whose
  `WINDOWS_SMOKE_ARGS` list three test files. Evidence: `Makefile` lines 54-59
  and `.github/workflows/ci.yml`. Impact: the plan adds the new unit-test
  module to that list so the Windows branches are exercised for real.
- Observation: the cited `fake-uv.py` raw URL in the design document returns
  HTTP 404 through anonymous fetches, although the file exists in the
  `leynos/dev-env-rocky` repository and is readable through `gh api`.
  Evidence: Firecrawl scrape returned 404; `gh api
  repos/leynos/dev-env-rocky/contents/...` succeeded. Impact: the repository
  is probably private. The footnote stays, but the plan quotes the relevant
  prior art below.
- Observation: `fake-uv.py` uses a fixed `.tmp` sibling opened with
  `O_CREAT | O_TRUNC`, which is safe there only because every write holds an
  exclusive lock. Evidence: `_write_installed_tools` in `fake-uv.py`. Impact:
  CmdMox cannot assume a lock until `14.2`, so the helper uses unique
  temporary names (`DEC-3`).

## Decision log

- Decision `DEC-1`: put the helper in a new private module
  `cmd_mox/_atomic_write.py`, beside `cmd_mox/fs_retry.py`. Rationale: the
  design places persistence helpers "beside" the internal filesystem
  utilities, but `fs_retry.py` is about retrying deletions. A dedicated module
  keeps that file cohesive and gives `14.5.1` (atomic text and executable
  writes) a natural home. The leading underscore matches `_path_utils.py` and
  `_validators.py` and signals the internal status required by the design's
  Stability policy. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-2`: separate pure serialization from the commit protocol.
  `_serialize_json(payload) -> bytes` is pure, and a private
  `_atomic_write_bytes(path, data, mode)` owns the filesystem sequence.
  `atomic_write_json` composes them. Rationale: serialization can then be
  tested exhaustively and by property without touching the filesystem, and
  `14.5.1` can reuse the byte-level commit for text files. Serialization runs
  before any filesystem mutation, so an unserializable payload leaves the
  filesystem untouched (not even the parent directory is created). Date/Author:
  2026-10-23, planning agent.

- Decision `DEC-3`: create the temporary file with
  `tempfile.mkstemp(dir=path.parent, prefix=f".{path.name[:64]}.",
  suffix=".tmp")` rather than the design's fixed name opened with
  `O_CREAT | O_TRUNC | O_WRONLY`. Rationale: `mkstemp` opens with `O_EXCL`, a
  random name, mode `0o600`, binary mode on Windows, and a non-inheritable
  descriptor. Without a lock (not available until `14.2`), two writers using
  a fixed temporary name could interleave bytes in the same temporary file and
  install a torn document. Unique names make concurrent writers last-writer-wins
  but never torn. The name prefix is truncated to 64 characters so that a
  target name close to the 255-byte `NAME_MAX` limit does not make the
  temporary name too long. The leading dot hides orphans on POSIX listings,
  and the `.tmp` suffix makes them recognizable. This deviates from the
  letter of the design's `os.open(...)` bullet, not from its intent; Stage D
  updates the design document. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-4`: apply the requested mode with `os.fchmod(fd, mode)`
  immediately after creating the temporary file and before writing any bytes.
  Where `os.fchmod` is missing (Windows on Python 3.12), call
  `os.chmod(tmp_path, mode)` instead. Rationale: `fchmod` is not subject to
  the umask, so the final mode equals the requested mode exactly. Applying it
  before writing means content never sits in a file more permissive than
  requested. On Windows only the read-only attribute is honoured; this is
  documented as best-effort, as the design requires. `mode` must be an `int`
  in the range `0` to `0o777`; anything else raises `ValueError` before any
  filesystem work, because it is a programming error rather than a
  persistence failure. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-5`: serialize with
  `json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n"`,
  encoded as UTF-8, and preserve mapping insertion order (no `sort_keys`).
  Rationale: `indent=2` plus a trailing newline matches the current
  `FixtureFile.save()` bytes, so `14.1.2` can adopt the helper without
  reformatting every fixture. Insertion order is deterministic for a given
  payload, and keeps the human-oriented layout of fixtures (`version` and
  `metadata` before `recordings`); `json.loads` preserves file order, so a
  load-then-save cycle is byte-stable. `ensure_ascii=True` keeps output
  byte-compatible with existing fixtures and, importantly, serializes lone
  surrogates (which appear when command-line arguments are decoded with
  `surrogateescape`) as `\udcXX` escapes instead of failing UTF-8 encoding.
  `allow_nan=False` rejects `NaN` and infinities, which are not valid JSON and
  would make files unreadable by strict parsers. The file is written in binary
  mode so Windows does not translate the newline to CRLF. This refines the
  design's "deterministic key ordering" bullet; Stage D records the
  refinement in the design document. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-6`: define `AtomicWriteError(OSError)` and a
  `AtomicWriteStage(enum.StrEnum)` in `cmd_mox/_atomic_write.py`.
  `AtomicWriteError` carries `path`, `stage`, and a `target_replaced`
  property that is true only for stage `sync_directory`. It is raised with
  `raise ... from exc`. Rationale: the design asks for an "`OSError`-like"
  error that names the target path and does not hide which step failed.
  `RobustRmtreeError(OSError)` in `fs_retry.py` is the house precedent. An
  explicit stage turns "which step failed" into data that callers and tests
  can rely on, and `target_replaced` answers the design's question of whether
  the old fixture is still complete. Errors are not exported in `14.1.1`, so
  they are not yet stable API. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-7`: catch `Exception` subclasses only at the boundaries where
  the stage is known, wrap them in `AtomicWriteError`, and let
  `BaseException` subclasses (`KeyboardInterrupt`, `SystemExit`) propagate
  unwrapped after cleanup. Cleanup failures never mask the primary error; they
  are attached with `BaseException.add_note(...)`. Rationale: interruption by
  Ctrl-C is exactly the "interrupted write" the roadmap names, and wrapping it
  would break normal interrupt handling. Date/Author: 2026-10-23, planning
  agent.

- Decision `DEC-8`: on POSIX, synchronize the parent directory by opening it
  with `os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)` and calling `os.fsync`.
  On Windows, skip it explicitly behind `_path_utils.IS_WINDOWS`, because
  Windows cannot open a directory with `os.open`, and Python's `os.replace`
  already uses `MoveFileExW`. Use plain `os.fsync` everywhere, not macOS
  `F_FULLFSYNC`. Rationale: the design permits skipping directory
  synchronization when unsupported, provided the skip is explicit and tested.
  Crash durability on filesystems that do not honour `fsync` is a stated
  non-goal, and no macOS CI exists to validate `F_FULLFSYNC`. The macOS gap is
  recorded as a residual risk in the design document. Date/Author:
  2026-10-23, planning agent.

- Decision `DEC-9`: when `path` is a symbolic link, replace the link itself
  rather than writing through it. Rationale: this is what `os.replace` does,
  and it matches `python-atomicwrites` and `fake-uv.py`. Writing through a
  link would put the temporary file in the link's directory but replace a file
  in another directory, which `os.replace` may reject across filesystems. The
  behaviour is documented and tested. A symlinked parent directory is
  unaffected. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-10`: do not use a Rust extension, Verus, Kani, or CrossHair.
  Rationale: the obligations concern the ordering of operating-system calls
  and their failure behaviour, not pure contractual business logic. A proof in
  a Rust extension would verify a model of the protocol, not the Python code
  that ships, and the repository has no Rust build (the stray `src/env.rs` is
  not wired to any build). CrossHair cannot explore filesystem side effects.
  The strongest available evidence is an exhaustive enumeration of the finite
  fault space (every stage times every prior state) plus a Hypothesis state
  machine over write sequences, plus a real process-kill test. Those are
  specified in `Verification plan`. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-11`: no ADR. Rationale: the decisions refine an existing,
  accepted design rather than choosing between architectures, and the
  documentation style guide reserves ADRs for substantive, hard-to-reverse
  choices. They are recorded in the design document in Stage D. Date/Author:
  2026-10-23, planning agent.

- Open decision `DEC-OPEN-1` (requires user approval before Stage A): how to
  satisfy the blocking Skylos dead-code scan while `atomic_write_json` has no
  production caller. Skylos scans only `cmd_mox/`, excludes tests on purpose
  (`--no-grep-verify`), and the policy in `docs/developers-guide.md` says to
  treat a report as genuine dead code until a runtime caller is verified, and
  never to use an exception to avoid a removal. Options:
  - Option A: add a typed entry-point rule for
    `cmd_mox._atomic_write.atomic_write_json` in
    `[[tool.skylos.dead_code.entrypoints]]`, mirrored in
    `EXPECTED_ENTRYPOINT_REASONS` and `EXPECTED_ENTRYPOINT_TYPES` in
    `tests/test_skylos_lint_contract.py`, with a reason naming roadmap
    `14.1.2` as the pending caller and stating that `14.1.2` removes the
    rule. This keeps the roadmap's item boundary but stretches the policy,
    which expects a verified existing caller.
  - Option B: keep `14.1.1` scoped as written and leave `make lint` failing
    until `14.1.2`. This breaks the requirement that all gates pass at every
    milestone, so it is not acceptable.
  - Option C: extend this plan with one final milestone that changes
    `FixtureFile.save()` to call `atomic_write_json(path, self.to_dict())`,
    which makes the helper live through a real caller and needs no exception.
    That absorbs the core of `14.1.2` (its fixture-level failure-injection
    tests and usage-guide note would follow in the same milestone or in
    `14.1.2`), and changes on-disk fixture permissions to `0o600`.
  The planning recommendation is recorded after the expert review below.

## Outcomes & retrospective

Not started. Complete this section at each milestone and at completion.

## Context and orientation

CmdMox is a Python library that replaces external commands with test doubles
during pytest runs. The relevant parts for this item are:

- `cmd_mox/record/fixture.py`: defines `FixtureFile`, the JSON document that
  record mode writes. `FixtureFile.save(path)` (around line 450) currently
  does `path.parent.mkdir(parents=True, exist_ok=True)` and then
  `path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")`. That is not
  atomic, has no `fsync`, uses the platform default encoding, and does not set
  a mode. This item does not change that file.
- `cmd_mox/fs_retry.py`: the existing internal filesystem helper module
  (retrying `unlink` and `rmtree`). It defines `RobustRmtreeError(OSError)`,
  the precedent for an `OSError` subclass that carries a path and chains its
  cause.
- `cmd_mox/_path_utils.py`: defines `IS_WINDOWS = os.name == "nt"`. Tests
  simulate Windows branches with
  `monkeypatch.setattr(path_utils, "IS_WINDOWS", True)`.
- `cmd_mox/unittests/`: unit tests, named `test_<module>.py`. The model to
  imitate is `cmd_mox/unittests/test_fs_retry.py`, which injects failures by
  monkeypatching attributes of the module under test, for example
  `monkeypatch.setattr(fs_retry.shutil, "rmtree", ...)`.
- `features/`, `tests/steps/`, and `tests/test_*_bdd.py`: behavioural tests
  using pytest-bdd. Each scenario is bound with an `@scenario(...)` decorator,
  and step modules are registered through a star import with a reasoned
  `ruff: ignore` comment, as in `tests/test_recording_session_bdd.py`.
- Snapshots use syrupy's default amber serializer, stored in
  `__snapshots__/` beside the test module and updated with
  `pytest --snapshot-update`.
- Hypothesis is used through explicit decorators. File-touching properties
  must not use the function-scoped `tmp_path` fixture inside `@given`
  (Hypothesis rejects that with a health check), so they create a
  `tempfile.TemporaryDirectory()` per example.
- Quality gates (`Makefile`): `make fmt`, `make check-fmt`, `make lint`
  (Ruff, Pylint on PyPy, the DF12 Pylint tier, ambrleaks, the blocking Skylos
  scan, and the typos spelling check), `make typecheck` (ty), `make test`
  (`pytest -v -n auto`), `make markdownlint`, and `make nixie`. The pytest
  timeout is 30 seconds per test.

Terms used in this plan:

- Atomic replacement: renaming a fully written temporary file over the target
  with `os.replace`, so readers see either the whole old file or the whole
  new file, never a mixture.
- `fsync`: an operating-system call asking the kernel to flush a file's data
  (or a directory's entries) to stable storage.
- Torn write: a target file that contains only part of a document because the
  writer stopped part-way.
- Stage: one named step of the write protocol, used to attribute failures.
- Fault injection: replacing an operating-system call in the module under
  test with a function that raises, to simulate a failure at a chosen stage.

Prior art. `fake-uv.py` in `leynos/dev-env-rocky` writes state like this:

```python
fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as state_file:
    state_file.write(json.dumps(installed_tools, sort_keys=True))
    state_file.flush()
    os.fsync(state_file.fileno())
os.replace(tmp_path, STATE_PATH)
# then open STATE_PATH.parent with os.O_RDONLY and os.fsync it
```

`python-atomicwrites` (now archived) uses `tempfile.mkstemp` in the target
directory, `flush` plus `fsync` (with `F_FULLFSYNC` on macOS), a rename, and a
directory `fsync` on POSIX, and `MoveFileExW` with `MOVEFILE_WRITE_THROUGH` on
Windows. Python documents that `os.replace` is atomic on success when both
paths are on one filesystem, that a buffered file must be flushed before
`os.fsync`, and that `os.fchmod` gained Windows support in Python 3.13.

## Conformance basis

Upstream artefacts, at the branch point `d22bb65`:

- `docs/roadmap.md` item `14.1.1` (`RM-14.1.1`), with success criterion
  `RM-14.1.1-S1`: "interrupted or failing writes preserve the previous
  complete JSON file", and `RM-14.1.1-S2`: "successful writes leave
  owner-restricted files on POSIX".
- `docs/cmd-mox-fake-capabilities-design.md` (Status: Draft). Traced
  elements:
  - `FCD-STAB`: helpers are internal and may move without deprecation.
  - `FCD-AJP-1` to `FCD-AJP-7`: the seven "Required behaviour" bullets of
    §Atomic JSON persistence (create parent; open the temporary file with the
    mode and `fchmod`; deterministic serialization with trailing newline;
    flush and `fsync`; `os.replace`; parent-directory `fsync`; remove the
    temporary file on failure).
  - `FCD-EXC`: §Persistence exceptions (`AtomicWriteError`, path, chaining,
    stage not hidden).
  - `FCD-FAIL`: §Failure semantics (old file survives failures before
    replacement; post-replace directory-sync failure reported, no rollback).
  - `FCD-SEC`: §Security and portability (default `0o600`; Windows
    best-effort; explicit, tested directory-sync skip).
  - `FCD-VER`: §Verification strategy (the persistence invariant).
- There is no Terms of Reference document for this phase. ADR 001 (linting
  architecture) and `AGENTS.md` govern the gates.

Trace links:

```plaintext
RM-14.1.1-S1 -> FCD-AJP-1..5,7 + FCD-FAIL -> EP-M1, EP-M2 -> INV-PRESERVE, INV-NO-RESIDUE, LEM-STAGE
RM-14.1.1-S1 -> FCD-FAIL -> EP-M2 -> INV-KILL (tests/test_atomic_write_process_boundary.py)
RM-14.1.1-S2 -> FCD-AJP-2 + FCD-SEC -> EP-M2 -> INV-MODE
FCD-AJP-3 -> DEC-5 -> EP-M2 -> INV-COMMIT, SNAP-FORMAT
FCD-AJP-6 + FCD-SEC -> DEC-8 -> EP-M2, EP-M3 -> INV-POST-REPLACE, LEM-WINDOWS-SKIP
FCD-EXC -> DEC-6, DEC-7 -> EP-M2 -> LEM-STAGE, LEM-BASE-EXCEPTION
FCD-STAB -> Constraints -> EP-M4 -> no export (checked in review)
```

Known deviations from the letter of the design, each to be recorded in the
design document in Stage D: `DEC-3` (unique `mkstemp` temporary file instead
of a fixed `O_TRUNC` name), `DEC-5` (insertion order rather than sorted keys),
and `DEC-8` (no `F_FULLFSYNC`).

## Verification plan

The implementation is deliberately structured so the obligations are
checkable: serialization is a pure function; the commit protocol is a fixed
sequence of named stages, each wrapping one operating-system call that tests
can replace; and the stage set is an enumeration that tests can iterate. The
finite fault space (seven stages times two prior states, plus the no-fault
case) is small enough to enumerate exhaustively.

Axioms (trusted, not verified):

- `AXM-REPLACE`: on POSIX, `os.replace(src, dst)` with both paths in one
  directory is atomic: observers see the old or new inode, never a mixture.
  On Windows, `os.replace` uses `MoveFileExW` with
  `MOVEFILE_REPLACE_EXISTING`, which is not documented as atomic but replaces
  the target as one metadata operation on NTFS.
- `AXM-FSYNC`: after `os.fsync(fd)` returns, the file's data is on stable
  storage on filesystems that honour `fsync`; after `os.fsync` on a directory
  descriptor returns, the directory entry is durable on Linux ext4, XFS, and
  Btrfs.
- `AXM-MKSTEMP`: `tempfile.mkstemp` creates a new file with `O_EXCL`, mode at
  most `0o600`, and a name unique in its directory.
- `AXM-FCHMOD`: `os.fchmod` sets permission bits exactly, independent of the
  umask.
- `AXM-JSON`: `json.loads(json.dumps(v)) == v` for every value in the JSON
  domain: `None`, `bool`, `int`, finite `float`, `str` (including lone
  surrogates under `ensure_ascii=True`), and lists and string-keyed dicts of
  these.
- `AXM-SIGKILL`: a process killed by `SIGKILL` performs no further system
  calls, so no Python cleanup runs.
- `AXM-SAMEFS`: pytest's `tmp_path` and `tempfile.TemporaryDirectory()` reside
  on one local filesystem.

Obligations:

- Obligation `INV-COMMIT`: for every JSON-domain payload `p` and every prior
  state, a successful call leaves `path` containing exactly
  `_serialize_json(p)`, and `json.loads` of the file equals `p`.
  - Method: Hypothesis property test plus syrupy snapshots.
  - Rationale: the payload domain is open-ended, so sampled properties are
    the proportionate tool; snapshots pin the exact byte format that `14.1.2`
    depends on.
  - Domain: `st.recursive` over JSON scalars (including `st.text()` with
    surrogates and finite floats), lists, and dicts with text keys; at most
    50 examples, `deadline=None`. Snapshot cases: empty object, nested
    mapping with insertion order that is not sorted, top-level list,
    top-level scalar, non-ASCII text, and a lone surrogate.
  - Artefact: `cmd_mox/unittests/test_atomic_write.py`
    (`test_round_trip_preserves_payload`, `test_serialized_bytes_snapshot`),
    `cmd_mox/unittests/__snapshots__/test_atomic_write.ambr`.
  - Evidence: fails in Stage A (strict `xfail`, stub raises
    `NotImplementedError`); passes in Stage B.
  - Non-vacuity: use `hypothesis.event()` to classify payloads by top-level
    type and by whether they contain non-ASCII text; the run must report every
    class. The snapshot of the unsorted mapping must show insertion order, so
    a mutation adding `sort_keys=True` fails it.

- Obligation `INV-PRESERVE`: for every stage `s` before `replace` (that is,
  `serialize`, `prepare_directory`, `create_temporary`, `write`, `sync_file`,
  and `replace` itself) and every prior state (target absent, or target
  holding a complete document `D0`), an injected failure at `s` leaves the
  target exactly as it was: absent stays absent, `D0` stays byte-identical.
  - Method: exhaustive parameterized test over the full product of stages and
    prior states.
  - Rationale: the fault space is finite and small, so exhaustive enumeration
    is a complete check of the protocol's failure branches at the
    granularity of named stages.
  - Domain: `AtomicWriteStage` members before `sync_directory` times
    `{absent, present}`.
  - Artefact: `test_failure_before_replacement_preserves_target` in
    `cmd_mox/unittests/test_atomic_write.py`, driven by a `FAULTS` table that
    maps each stage to the module attribute to patch (`json.dumps` via
    `_serialize_json`, `Path.mkdir`, `tempfile.mkstemp`, the file object's
    `write`, `os.fsync`, `os.replace`). The `write` fault writes half of the
    bytes before raising `OSError(errno.ENOSPC, ...)`, to model a full disk.
  - Evidence: Stage A strict `xfail`; Stage B passes for all 12 cases.
  - Non-vacuity: `test_fault_table_covers_every_stage` asserts that the keys
    of `FAULTS` equal the set of `AtomicWriteStage` members, so adding a stage
    without a fault case fails. A negative control,
    `test_naive_writer_violates_preservation`, runs the same checker against
    a deliberately naive writer (`Path.write_text` directly on the target)
    with the half-write fault and asserts the checker reports the torn file.
    This proves the checker can detect the defect it guards against.

- Obligation `INV-NO-RESIDUE`: after any failure handled inside the helper,
  the target directory contains no file matching `.<name>.*.tmp`, and no file
  descriptor opened by the helper remains open.
  - Method: assertions inside the exhaustive test above, plus a Linux-only
    descriptor count from `/proc/self/fd` before and after each case.
  - Artefact: the same parameterized test, and
    `test_failures_do_not_leak_descriptors`.
  - Non-vacuity: the descriptor check is validated by a control that opens a
    descriptor without closing it and asserts the counter detects the leak.

- Obligation `INV-POST-REPLACE`: a failure at `sync_directory` leaves the
  target holding the new document, raises `AtomicWriteError` with
  `stage == "sync_directory"` and `target_replaced is True`, and performs no
  rollback.
  - Method: named example test (POSIX only).
  - Artefact: `test_directory_sync_failure_reports_replaced_target`.
  - Non-vacuity: the test asserts the new content is present, so a mutation
    that restores the old file fails it.

- Obligation `INV-MODE`: on POSIX, after a successful write the target's
  permission bits equal the requested mode, for umask values `0o000`,
  `0o022`, and `0o077`, for requested modes `0o600` (default), `0o640`, and
  `0o644`, and for a prior target with mode `0o644` or `0o666`.
  - Method: parameterized test over that finite table; `ValueError` for
    modes outside `0..0o777` and for non-integers.
  - Artefact: `test_mode_is_exact_regardless_of_umask` and
    `test_invalid_mode_is_rejected_before_io`.
  - Non-vacuity: umask `0o000` with the default mode distinguishes a correct
    `fchmod` from relying on `mkstemp`'s default only when combined with mode
    `0o640` and `0o644`, which `mkstemp` alone would never produce; a mutant
    that drops `fchmod` therefore fails those rows.

- Obligation `INV-KILL`: a writer process killed with `SIGKILL` after the
  temporary file is synchronized but before `os.replace` leaves the previous
  complete document at the target.
  - Method: end-to-end test across a real process boundary. The child imports
    the module, wraps `_atomic_write.os.replace` so that it prints `READY`,
    flushes, and blocks; the parent waits for `READY`, kills the child, and
    inspects the directory.
  - Artefact: `tests/test_atomic_write_process_boundary.py`
    (`test_killed_writer_leaves_previous_document`), skipped on Windows.
  - Evidence: Stage A strict `xfail`; Stage B passes. The test also asserts
    that exactly one orphaned `.<name>.*.tmp` file exists, documenting the
    known residue after a hard kill.
  - Non-vacuity: the orphan assertion proves the child reached the paused
    point after writing the temporary file, so the kill really interrupted a
    write in progress.

- Obligation `INV-NO-TORN-READ`: while several writer processes repeatedly
  write distinct complete documents to one target, a concurrent reader only
  ever observes one of those documents in full.
  - Method: end-to-end stress test with a bounded number of iterations
    (three writers, 50 writes each, a reader polling until the writers exit),
    POSIX only.
  - Artefact: `test_concurrent_writers_never_expose_torn_documents` in
    `tests/test_atomic_write_process_boundary.py`.
  - Non-vacuity: the reader records how many distinct documents it saw and
    asserts at least two, proving it overlapped with the writers. The naive
    writer from the negative control above is not used here, because torn
    reads under the naive writer are probabilistic.

- Obligation `INV-SEQUENCE` (stateful): for any sequence of up to eight
  writes, each either succeeding or failing at a randomly chosen stage, the
  target always equals the serialization of the last successful payload (or
  is absent if none succeeded), and no temporary file remains.
  - Method: Hypothesis `RuleBasedStateMachine` with a model of "last
    committed bytes".
  - Rationale: this checks that earlier failures do not poison later writes,
    for example through leftover temporary files or descriptors.
  - Artefact: `TestAtomicWriteSequences` in
    `cmd_mox/unittests/test_atomic_write.py`, 30 examples of at most eight
    steps.
  - Non-vacuity: rules emit `event()` labels for success and for each failure
    stage; the run must show every label.

- Lemma `LEM-SERIALIZE-FIRST`: an unserializable payload (a `set`, a circular
  list, `float("nan")`) raises `AtomicWriteError` with stage `serialize`, its
  `__cause__` is the original `TypeError` or `ValueError`, and the filesystem
  is untouched (the missing parent directory is still missing).
  - Method: parameterized test over those three payloads.
  - Artefact: `test_unserializable_payload_touches_nothing`.

- Lemma `LEM-STAGE`: for every injected fault, the raised `AtomicWriteError`
  has the matching `stage`, `path` equal to the target, and `__cause__`
  identical to the injected exception object.
  - Method: asserted inside the exhaustive `INV-PRESERVE` test.

- Lemma `LEM-BASE-EXCEPTION`: a `KeyboardInterrupt` raised during the write
  stage propagates as `KeyboardInterrupt` (not wrapped), and the temporary
  file is removed.
  - Method: named example test.
  - Artefact: `test_keyboard_interrupt_cleans_up_and_propagates`.

- Lemma `LEM-CLEANUP-NOTE`: if removing the temporary file also fails, the
  primary `AtomicWriteError` still propagates, and its `__notes__` mention the
  temporary path.
  - Method: named example test.

- Lemma `LEM-WINDOWS-SKIP`: when `_path_utils.IS_WINDOWS` is true, the helper
  does not open the parent directory, and a write still succeeds; when
  `os.fchmod` is unavailable, the helper falls back to `os.chmod`.
  - Method: unit tests that simulate both conditions with `monkeypatch`, plus
    the real Windows run added in EP-M3, which executes the whole unit module
    on `windows-latest`.
  - Artefact: `test_windows_skips_directory_sync` and
    `test_missing_fchmod_falls_back_to_chmod`.

- Lemma `LEM-SYMLINK`: when the target is a symbolic link, a successful write
  replaces the link with a regular file and leaves the link's former
  destination unchanged (POSIX only).
  - Artefact: `test_symlink_target_is_replaced_not_followed`.

Residual gaps: crash durability (power loss) cannot be tested in CI and rests
on `AXM-FSYNC`; macOS `fsync` does not flush the drive cache (`DEC-8`); Windows
replacement is not documented as atomic (`AXM-REPLACE`); the end-to-end tests
do not run on Windows.

Mutation audit: Stage C runs `mutmut` on `cmd_mox/_atomic_write.py` only and
promotes each surviving, meaningful mutant to a test. Equivalent mutants are
recorded in `Surprises & discoveries`.

## Plan of work

Stage 0 (no code): settle `DEC-OPEN-1` with the user and record the answer.
Go/no-go: Stage A must not start until the plan is approved.

Stage A (EP-M1, red): create `cmd_mox/_atomic_write.py` containing only the
interface: the module docstring, the `JsonValue` type alias, the
`AtomicWriteStage` enumeration, `AtomicWriteError`, and an
`atomic_write_json` stub that raises `NotImplementedError`. Add every test
named in `Verification plan`, the BDD feature, and the snapshot test, with
`@pytest.mark.xfail(strict=True, reason="14.1.1: atomic_write_json not yet
implemented")` on each test that exercises the stub. Tests that check only the
interface (for example, the stage-coverage test and the
`AtomicWriteError` attribute test) pass immediately and carry no marker.
Apply the `DEC-OPEN-1` outcome so `make lint` passes. Validate: the focused
test run shows the new tests as `XFAIL`; all four gates pass.

Stage B (EP-M2, green): implement `_serialize_json`, `_atomic_write_bytes`,
and `atomic_write_json`, remove every `xfail` marker, and generate the
snapshot with `--snapshot-update`. Inspect the generated `.ambr` file by hand
before committing. Validate: focused tests pass; all four gates pass.

Stage C (EP-M3, Windows and refactor): add
`cmd_mox/unittests/test_atomic_write.py` to `WINDOWS_SMOKE_ARGS` in the
`Makefile`; refactor for clarity without behaviour change; run the mutation
audit. Validate: all four gates pass; the Windows CI job passes on the pull
request.

Stage D (EP-M4, documentation): update
`docs/cmd-mox-fake-capabilities-design.md`, `docs/developers-guide.md`, and
`docs/roadmap.md` as described under `Milestones and plateaus`. Validate:
`make markdownlint`, `make nixie`, and all four code gates pass.

## Milestones and plateaus

### EP-M1: interface and red tests

- Outcome: the private interface exists, every planned test exists and fails
  for the expected reason under strict `xfail`, and the repository is green.
- Requirements and gaps: prepares `RM-14.1.1-S1`, `RM-14.1.1-S2`, and every
  `FCD-*` element.
- Acceptance evidence: focused pytest output listing each new test as
  `XFAIL` with reason `14.1.1: atomic_write_json not yet implemented`.
- Conformance check:
  - No requirement is satisfied yet; all are specified by tests.
  - The module layout follows `DEC-1` and `DEC-2`.
  - Upstream assumptions unchanged.
  - No public export; no dependency change.
  - No trust-boundary or persisted-format change.
  - Trace links current.
- Recovery: revert the milestone commit.
- Remaining gaps: implementation.
- Compatibility decision: none.

### EP-M2: working helper

- Outcome: `atomic_write_json` satisfies every obligation in `Verification
  plan` on Linux.
- Requirements and gaps: discharges `RM-14.1.1-S1`, `RM-14.1.1-S2`,
  `FCD-AJP-1..7`, `FCD-EXC`, `FCD-FAIL`, and the POSIX part of `FCD-SEC`.
- Acceptance evidence: all new tests pass; snapshot file committed.
- Conformance check:
  - Requirements above satisfied.
  - Design followed, with the recorded `DEC-3`, `DEC-5`, and `DEC-8`
    refinements.
  - Upstream assumptions unchanged.
  - No public export; no dependency change.
  - The helper defines a persisted format (`DEC-5`) but nothing persists
    through it yet.
  - Trace links current.
- Recovery: revert to EP-M1; the strict `xfail` markers return.
- Remaining gaps: Windows execution evidence; documentation.
- Compatibility decision: none.

### EP-M3: Windows coverage and hardening

- Outcome: the unit module runs on `windows-latest` in CI, the code is
  refactored for readability, and surviving mutants are addressed.
- Requirements and gaps: discharges the Windows part of `FCD-SEC` and
  `LEM-WINDOWS-SKIP` on a real Windows host.
- Acceptance evidence: the pull request's Windows smoke job passes with the
  new module listed in its log; mutation audit notes recorded.
- Conformance check: as for EP-M2, plus the `Makefile` change is limited to
  `WINDOWS_SMOKE_ARGS`.
- Recovery: remove the module from `WINDOWS_SMOKE_ARGS` and record the
  Windows failure as a discovery and an escalation.
- Remaining gaps: documentation.
- Compatibility decision: none.

### EP-M4: documentation and roadmap

- Outcome: documentation describes the helper, its conventions, and the
  refinements; roadmap item `14.1.1` is marked done.
- Changes:
  - `docs/cmd-mox-fake-capabilities-design.md` §Atomic JSON persistence:
    record the module path, signature (`mode` keyword-only), `JsonValue`,
    unique `mkstemp` temporary files (`DEC-3`), the exact serialization
    settings and insertion order (`DEC-5`), `AtomicWriteStage` and
    `target_replaced` (`DEC-6`), `BaseException` handling (`DEC-7`), the
    Windows and macOS synchronization limits (`DEC-8`), and symlink handling
    (`DEC-9`). Add a short "Implementation status" note that `14.1.1` is
    implemented and `14.1.2` adopts it.
  - `docs/developers-guide.md`: add a section "Atomic JSON persistence"
    between "Property-based testing" and "IPC request dispatch", stating that
    internal JSON writers must call `cmd_mox._atomic_write.atomic_write_json`
    rather than `Path.write_text`, describing the error contract, and
    recording the test conventions introduced here (POSIX-only mode
    assertions with `stat.S_IMODE`, the umask fixture, fault tables keyed by
    `AtomicWriteStage`, and `TemporaryDirectory` inside Hypothesis tests).
    If `DEC-OPEN-1` Option A is chosen, add the temporary entry-point rule to
    the Skylos section's narrative with its removal obligation.
  - `docs/usage-guide.md`: no change, because no public behaviour changes in
    `14.1.1`. If `DEC-OPEN-1` Option C is chosen, add a note under "Fixture
    file format" that fixtures are written atomically with mode `0o600`.
  - `docs/roadmap.md`: change `- [ ] 14.1.1.` to `- [x] 14.1.1.`.
- Acceptance evidence: `make markdownlint` and `make nixie` pass; the four code
  gates pass.
- Conformance check: reconcile `DEC-3`, `DEC-5`, and `DEC-8` with the design
  document; confirm `FCD-STAB` (no export).
- Recovery: documentation-only commits revert cleanly.
- Compatibility decision: none.

## Concrete steps

Run every command from the repository root,
`/home/leynos/.lody/repos/github---leynos---cmd-mox/worktrees/7f849fe1-6e1e-46f4-8efe-207369025e5e`.
Log names follow the pattern
`/tmp/$ACTION-cmd-mox-14-1-1-implement-internal-atomic-write-json.out`.

Focused test run (used for red and green checks):

```bash
set -o pipefail
uv run --group dev -- pytest -v \
  cmd_mox/unittests/test_atomic_write.py \
  tests/test_atomic_json_persistence_bdd.py \
  tests/test_atomic_write_process_boundary.py \
  2>&1 | tee /tmp/focused-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
```

Expected in Stage A (abridged):

```plaintext
cmd_mox/unittests/test_atomic_write.py::test_fault_table_covers_every_stage PASSED
cmd_mox/unittests/test_atomic_write.py::test_failure_before_replacement_preserves_target[write-present] XFAIL
...
tests/test_atomic_write_process_boundary.py::test_killed_writer_leaves_previous_document XFAIL
```

Expected in Stage B: every listed test `PASSED` (or `SKIPPED` for POSIX-only
tests on Windows).

Snapshot generation (Stage B only, then review the file):

```bash
uv run --group dev -- pytest cmd_mox/unittests/test_atomic_write.py \
  -k snapshot --snapshot-update
git diff -- cmd_mox/unittests/__snapshots__/test_atomic_write.ambr
```

Gates, run sequentially after each milestone (prefer delegating the full run
to the `scrutineer` agent):

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/check-fmt-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
make typecheck 2>&1 | tee /tmp/typecheck-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
NO_COLOR=1 make lint 2>&1 | tee /tmp/lint-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
make test 2>&1 | tee /tmp/test-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
```

Documentation gates (Stage D, and for this plan):

```bash
set -o pipefail
make markdownlint 2>&1 | tee /tmp/markdownlint-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
make nixie 2>&1 | tee /tmp/nixie-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
```

Mutation audit (Stage C):

```bash
uv run --group dev -- mutmut run --paths-to-mutate cmd_mox/_atomic_write.py
uv run --group dev -- mutmut results
```

If the installed `mutmut` version reads targets only from
`[tool.mutmut]` in `pyproject.toml`, run it with a temporary local override
and do not commit that override. Record the version and the exact command used
in `Surprises & discoveries`.

Commit after each milestone with an imperative subject, for example
`Add failing tests for atomic JSON writes`, and keep commits atomic.

## Validation and acceptance

Behaviour to observe after Stage B:

- Calling `atomic_write_json(tmp_path / "a" / "b.json", {"z": 1, "a": [1,
  2]})` creates `a/`, and the file contains exactly:

  ```plaintext
  {
    "z": 1,
    "a": [
      1,
      2
    ]
  }
  ```

  followed by one newline, with mode `0o600` on POSIX.
- With `os.replace` patched to raise `PermissionError`, the call raises
  `AtomicWriteError` whose `stage` is `replace`, whose `target_replaced` is
  false, whose `__cause__` is the `PermissionError`, and the previous file is
  byte-identical.

Behavioural specification, `features/atomic_json_persistence.feature`:

```gherkin
Feature: Atomic JSON persistence
  CmdMox writes JSON fixture and state files so that readers only ever see
  a complete document, and failed writes never damage the previous one.

  Scenario: A new JSON document is written with owner-only permissions
    Given an empty state directory
    When the payload {"tools": {"ruff": "0.16.4"}} is written to "state/tools.json"
    Then "state/tools.json" contains the payload {"tools": {"ruff": "0.16.4"}}
    And "state/tools.json" is readable and writable only by its owner
    And the state directory contains no temporary files

  Scenario: A failed replacement keeps the previous document
    Given "tools.json" already contains the payload {"tools": {}}
    And the final rename will fail
    When the payload {"tools": {"ruff": "0.16.4"}} is written to "tools.json"
    Then the write fails with an atomic write error at stage "replace"
    And "tools.json" still contains the payload {"tools": {}}
    And the state directory contains no temporary files

  Scenario: An unserializable payload leaves the filesystem untouched
    Given an empty state directory
    When an unserializable payload is written to "nested/tools.json"
    Then the write fails with an atomic write error at stage "serialize"
    And "nested" does not exist

  Scenario: Repeated writes replace the whole document
    Given "tools.json" already contains the payload {"tools": {"a": "1", "b": "2"}}
    When the payload {"tools": {}} is written to "tools.json"
    Then "tools.json" contains the payload {"tools": {}}
```

The permission step skips its assertion on Windows with an explanatory
message; the step implementation lives in
`tests/steps/atomic_json_persistence.py`, and the bindings in
`tests/test_atomic_json_persistence_bdd.py`, one `@scenario(...)` function per
scenario. Steps must not reach into private state other than the helper's own
module, and payloads are parsed from the step text with `json.loads`.

Red-Green-Refactor evidence to record in `Artefacts and notes`:

- Red: the focused command above, showing `XFAIL` for every behaviour test,
  with the strict reason string.
- Green: the same command, all `PASSED`, after removing the markers.
- Refactor: the same command plus the four gates, all passing.

Quality criteria (what "done" means):

- Tests: the focused suite and `make test` pass; every obligation in
  `Verification plan` has a passing artefact.
- Verification: `INV-*` and `LEM-*` discharged as described, with non-vacuity
  controls passing; mutation audit recorded.
- Lint and type checking: `make check-fmt`, `make lint`, and
  `make typecheck` pass with no new unexplained suppressions.
- Documentation: `make markdownlint` and `make nixie` pass.
- Security: default mode `0o600`; no world-readable temporary files at any
  point on POSIX.

## Idempotence and recovery

Every step can be repeated. Tests create files only under pytest temporary
directories. The umask fixture restores the previous umask in a `finally`
block. If the snapshot is generated wrongly, delete
`cmd_mox/unittests/__snapshots__/test_atomic_write.ambr` and regenerate it.
Each milestone is one or more commits on the branch; `git revert` of a
milestone's commits returns to the previous plateau.

## Artefacts and notes

Record red, green, and gate transcripts here during implementation, trimmed to
the lines that prove the result.

## Interfaces and dependencies

No new dependencies. Standard library modules used: `enum`, `errno` (tests),
`json`, `os`, `stat` (tests), `tempfile`, `pathlib`, and `typing`.

In `cmd_mox/_atomic_write.py`, define:

```python
type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


class AtomicWriteStage(enum.StrEnum):
    """Named steps of the atomic write protocol, in execution order."""

    SERIALIZE = "serialize"
    PREPARE_DIRECTORY = "prepare_directory"
    CREATE_TEMPORARY = "create_temporary"
    WRITE = "write"
    SYNC_FILE = "sync_file"
    REPLACE = "replace"
    SYNC_DIRECTORY = "sync_directory"


class AtomicWriteError(OSError):
    """Raised when a durable JSON write cannot be completed."""

    def __init__(self, path: Path, stage: AtomicWriteStage) -> None: ...

    path: Path
    stage: AtomicWriteStage

    @property
    def target_replaced(self) -> bool: ...


def atomic_write_json(
    path: Path, payload: JsonValue, *, mode: int = 0o600
) -> None: ...


def _serialize_json(payload: JsonValue) -> bytes: ...


def _atomic_write_bytes(path: Path, data: bytes, mode: int) -> None: ...
```

`CREATE_TEMPORARY` covers `mkstemp` and the `fchmod` that follows it.
`WRITE` covers writing and flushing the buffered file object. `SYNC_FILE`
covers `os.fsync` and closing the descriptor. The sequence inside
`_atomic_write_bytes` is: create the parent directory; create the temporary
file; apply the mode; write the bytes through `os.fdopen(fd, "wb")`; flush;
`os.fsync`; close; `os.replace(tmp_path, path)`; then, on POSIX, open,
`fsync`, and close the parent directory. Any `Exception` before the replace
is wrapped as `AtomicWriteError(path, stage)` after removing the temporary
file; any `BaseException` that is not an `Exception` triggers the same
cleanup and then propagates unchanged.

`AtomicWriteError.__init__` builds the message
`f"atomic write to {path} failed during {stage}"` and calls
`super().__init__(msg)`, following `RobustRmtreeError`. Keep the public-facing
docstrings in NumPy style with `Parameters`, `Raises`, and `Examples`
sections, and keep private helper docstrings to one line.
