# Implement internal `atomic_write_json` for fixture and state files

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: DRAFT (awaiting approval, including open decision `DEC-OPEN-1`)

## Purpose / big picture

Roadmap item `14.1.1` asks whether CmdMox can write JSON fixture and state
files durably without changing its public record and replay API. Today,
`cmd_mox.record.fixture.FixtureFile.save()` calls `Path.write_text(...)`
directly. If the process is interrupted, or the disk fills part-way through
that call, the fixture on disk is left truncated, and the next replay fails
with a JSON decoding error. The file is also created with whatever permissions
the process umask allows, usually world-readable, although fixtures can contain
environment-derived values and command output.

After this change, CmdMox has one internal persistence boundary in the private
module `cmd_mox/_atomic_write.py`:

- `atomic_write_json(path, payload, *, mode=0o600)` serializes a JSON payload
  and commits it atomically.
- `atomic_write_bytes(path, data, *, mode=0o600)` commits already-encoded
  bytes, for the text and executable helpers planned in roadmap item `14.5.1`.

Later JSON writers (the state store in `14.3.1`, the side-effect helpers in
`14.5.1`) share this boundary. Under the recommended resolution of `DEC-OPEN-1`
(Option C), `FixtureFile.save()` is routed through the helper in the same
change, which also delivers roadmap item `14.1.2`.

A maintainer can observe success in these ways:

1. Writing a payload creates the target and any missing parent directories,
   and the file contains exactly the payload serialized as two-space-indented
   JSON with a trailing newline, encoded as UTF-8. For fixtures, those bytes
   are identical to what `FixtureFile.save()` writes today.
2. Injecting a failure into any step before the final rename leaves the
   previous target byte-for-byte unchanged and adds no file to the directory.
3. A child process killed with `SIGKILL` while paused just before the rename
   leaves the previous complete JSON document in place.
4. On POSIX, a successful write leaves the target with mode `0o600` (or the
   caller's requested mode) regardless of the process umask and the previous
   file's mode.
5. Under Option C, a recorded fixture is owner-only on POSIX, and a fixture
   save that fails before the rename keeps the previous fixture intact.
6. `make check-fmt`, `make typecheck`, `make lint`, and `make test` pass, and
   the new tests fail before the implementation and pass afterwards.

The helper module is internal. Nothing from it is exported from
`cmd_mox/__init__.py`.

## Relevant documentation and skills

Keep these references open while executing the plan:

- `docs/roadmap.md`, phase 14, items `14.1.1` and `14.1.2`.
- `docs/cmd-mox-fake-capabilities-design.md`, the governing design and the
  component architecture document for phase 14. Read §§Stability policy,
  Problem, Goals, Prior art and constraints, Architecture, Atomic JSON
  persistence (with Persistence exceptions and Failure semantics), Filesystem
  side-effect helpers, Security and portability, and Verification strategy.
- `docs/python-native-command-mocking-design.md` §9.5.3 (`FixtureFile`) and
  §9.10 (design decisions), updated under Option C.
- `docs/usage-guide.md` §§Recording sessions and Fixture file format, updated
  under Option C. There is no `docs/users-guide.md`; this is the consumer guide.
- `docs/developers-guide.md` §§Snapshot testing, Property-based testing, and
  Skylos dead-code policy.
- `docs/documentation-style-guide.md` for en-GB-oxendict spelling, 80-column
  wrapping, and ADR format.
- `AGENTS.md` for the quality gates and the Skylos policy.
- Skills: `execplans` (keep this document current); `python-router`, then
  `python-errors-and-logging` (exception design and chaining),
  `python-types-and-apis` (the `JsonValue` and `JsonInput` aliases),
  `python-testing` (fixtures, parametrization, syrupy), `hypothesis`
  (round-trip properties), `python-verification` (only if escalation beyond the
  planned methods is considered), `mutmut` (mutation audit), `skylos`
  (dead-code findings), `leta` or `codegraph-mcp` for navigation, and
  `en-gb-oxendict-style` for documentation edits.

## Constraints

- Do not change any public signature. `atomic_write_json`,
  `atomic_write_bytes`, `AtomicWriteError`, `AtomicWriteStage`, `JsonValue`, and
  `JsonInput` must not be added to `cmd_mox/__init__.py` or to any `__all__`.
- Do not change `FixtureFile.load()` or the fixture schema. Under Option A,
  do not modify `cmd_mox/record/fixture.py` at all. Under Option C, the only
  change there is the body of `FixtureFile.save()`.
- Do not add runtime or development dependencies. Hypothesis, syrupy,
  pytest-bdd, pytest-timeout, and pytest-xdist are already in the `dev` group.
- Support Python 3.12 and later (`requires-python = ">=3.12"`). `os.fchmod`
  exists on Windows only from Python 3.13, so the code must not assume it.
- Keep Windows support intact. The helper must import and run on Windows, and
  any skipped POSIX-only step must be skipped explicitly in code.
- Create temporary files in the target's own directory so `os.replace` stays
  on one filesystem.
- Never roll back the target after `os.replace` succeeds.
- Follow the repository's Ruff, Pylint, DF12, ty, and Skylos policies without
  unexplained suppressions. Every suppression must carry a reason. Do not add a
  Skylos exception for a symbol that has no verified runtime caller.
- Prose follows en-GB-oxendict spelling and wraps at 80 columns.

## Tolerances (exception triggers)

- Scope: if implementation needs more than 18 changed files or 1,300 net
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
  changes on-disk bytes, error types, or file modes beyond the deviations
  listed in `Conformance basis`, stop and present options.
- Platform: if a planned test is flaky in more than one of ten local
  repetitions, stop and escalate rather than adding retries or sleeps.

## Risks

- Risk: the blocking Skylos scan reports the helper as dead code if nothing in
  `cmd_mox/` calls it, because tests are excluded from the scan by design.
  Severity: high. Likelihood: certain under Option A. Mitigation: settle
  `DEC-OPEN-1` before Stage A. Option C removes the risk by giving the helper a
  real caller in the same commit.
- Risk: under Option C, fixture files written on WSL drvfs, Docker Desktop
  bind mounts, virtiofs, or some FUSE mounts could fail because those
  filesystems reject `fsync` on a directory. Severity: high. Likelihood:
  medium. Mitigation: `DEC-8` treats "directory sync unsupported" errors as an
  explicit, logged skip and raises only for genuine I/O errors.
- Risk: on Windows, `os.replace` fails with `PermissionError` while another
  process (an xdist worker, an indexer, an antivirus scanner) holds the target
  open without delete sharing. `Path.write_text` did not have that failure
  mode. Severity: medium. Likelihood: medium. Mitigation: `DEC-12` applies a
  bounded retry to `PermissionError` from the replace step on Windows only,
  reusing `cmd_mox.fs_retry.RetryConfig`.
- Risk: a hard kill (`SIGKILL`, out-of-memory killer, or pytest-timeout's
  thread method, which calls `os._exit`) leaves an orphaned hidden temporary
  file beside a fixture, which `git add -A` could commit. Severity: medium.
  Likelihood: low. Mitigation: a fixed, documented temporary-name pattern, a
  recommended `.gitignore` line in the usage and developer guides, and an
  orphan sweep deferred to `14.2`, where the lock makes a sweep safe.
- Risk: each write now costs two `fsync` calls (about 16 ms per write measured
  on this host's ext4, versus 0.14 ms without `fsync`). Severity: low.
  Likelihood: certain. Mitigation: acceptable for one fixture save per test;
  protocol-logic tests replace the sync steps with counting no-ops; the cost is
  recorded in the design document for `14.3`, which may write per command
  invocation.
- Risk: `os.umask` is process-wide. Severity: low. Likelihood: low.
  Mitigation: change it only inside a fixture that restores it in `finally`;
  xdist workers are separate processes.
- Risk: mutation testing (`.github/workflows/mutation-testing.yml`) mutates
  the new module. Severity: low. Likelihood: medium. Mitigation: Stage C runs a
  time-boxed `mutmut` audit on the module.
- Risk: the new unit tests would not run on Windows CI, because that job runs
  only `make windows-smoke`. Severity: medium. Likelihood: certain unless
  addressed. Mitigation: add the new unit-test module to `WINDOWS_SMOKE_ARGS`
  in the `Makefile`.

## Progress

- [x] (2026-10-23) Renamed the branch to
  `14-1-1-implement-internal-atomic-write-json` with `origin` tracking.
- [x] (2026-10-23) Reconnaissance of code, tests, tooling, and documentation
  by a team of three Wyvern agents.
- [x] (2026-10-23) External research on `os.replace`, `os.fsync`,
  `os.fchmod`, `python-atomicwrites`, and the `fake-uv.py` prior art.
- [x] (2026-10-23) First draft of this plan (commit `a3bac1f`).
- [x] (2026-10-23) Expert-panel design review (verdict: proceed with
  conditions) and revision of this plan to meet those conditions.
- [ ] User approval of the plan and of `DEC-OPEN-1`.
- [ ] Stage A: red tests (EP-M1).
- [ ] Stage B: green implementation and adoption (EP-M2).
- [ ] Stage C: Windows coverage, refactor, and mutation audit (EP-M3).
- [ ] Stage D: documentation and roadmap (EP-M4).

## Surprises & discoveries

- Observation: there is no `docs/users-guide.md`; the consumer guide is
  `docs/usage-guide.md`. Evidence: `ls docs`. Impact: user-facing notes go to
  `docs/usage-guide.md`.
- Observation: no existing test asserts file permissions, umask behaviour, or
  the exact on-disk bytes of a fixture. Evidence: repository-wide search for
  `S_IMODE`, `st_mode`, and `umask`. Impact: the conventions introduced here
  are recorded in `docs/developers-guide.md`.
- Observation: the Windows CI job runs only `make windows-smoke`, whose
  `WINDOWS_SMOKE_ARGS` list three test files. Evidence: `Makefile` lines 54-59
  and `.github/workflows/ci.yml`. Impact: the plan adds the new unit-test
  module to that list.
- Observation: the `fake-uv.py` raw URL cited by the design returns HTTP 404
  to anonymous fetches, but `gh api` can read the file. Evidence: Firecrawl
  scrape returned 404; `gh api repos/leynos/dev-env-rocky/contents/...`
  succeeded. Impact: the repository is probably private, so the relevant prior
  art is quoted below.
- Observation: `fake-uv.py` uses a fixed `.tmp` sibling opened with
  `O_CREAT | O_TRUNC`, which is safe there only because every write holds an
  exclusive lock. Impact: CmdMox has no lock until `14.2`, so the helper uses
  unique temporary names (`DEC-3`).
- Observation (expert-panel experiments): `io.BufferedWriter.write` cannot be
  monkeypatched (immutable type); `os.fsync` is shared by the file and
  directory sync steps; and Hypothesis's example database itself calls
  `tempfile.mkstemp` and `Path.mkdir`. Impact: fault injection uses one private
  seam function per stage inside `cmd_mox/_atomic_write.py` (`DEC-13`), never
  global patches of `os`, `tempfile`, or `pathlib`.
- Observation (expert-panel experiments): truncating `path.name[:64]` counts
  characters, so a 62-emoji name (253 bytes) still produced `ENAMETOOLONG`.
  Opening a parent directory with mode `0o300` for `fsync` raises
  `PermissionError` although `mkstemp` and `os.replace` succeed. Counting
  `/proc/self/fd` entries differed in 369 of 2,000 comparisons when a
  background thread was opening files. Impact: `DEC-3`, `DEC-8`, and the
  descriptor-leak check were revised.
- Observation: on this host, a bare `python3 -c` run from `/tmp` imported
  another agent's `/tmp/bisect.py` in place of the standard library module.
  Impact: child processes in tests are launched with `-I` (isolated mode) and
  an explicit `sys.path` entry for the repository root.

## Decision log

- Decision `DEC-1`: put the helpers in a new private module
  `cmd_mox/_atomic_write.py`, beside `cmd_mox/fs_retry.py`. Rationale: the
  design places persistence helpers "beside" the internal filesystem utilities,
  but `fs_retry.py` is about retrying deletions. A dedicated module keeps that
  file cohesive and gives `14.5.1` a natural home. The leading underscore marks
  the whole module internal, as the design's Stability policy requires, so the
  functions inside it carry no underscore and can be imported by other private
  modules without protected-access lint findings. Date/Author: 2026-10-23,
  planning agent, revised after expert review.

- Decision `DEC-2`: separate pure serialization from the commit protocol.
  `_serialize_json(payload) -> bytes` is pure;
  `atomic_write_bytes(path, data, *, mode)` owns the filesystem sequence;
  `atomic_write_json` composes them. Serialization runs before any filesystem
  mutation, so an unserializable payload leaves the filesystem untouched (not
  even the parent directory is created). Rationale: serialization can be tested
  by property without touching the filesystem, and `14.5.1` reuses the
  byte-level commit. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-3`: create the temporary file with
  `tempfile.mkstemp(dir=path.parent, prefix=_temporary_prefix(path.name),
  suffix=".tmp")`,
  where `_temporary_prefix` returns `"." + name + "."`, with the name
  truncated to at most 64 bytes of its UTF-8 file-system encoding (dropping any
  split trailing character). This replaces the design's fixed name opened with
  `O_CREAT | O_TRUNC | O_WRONLY`. A target whose name is empty (`Path("")`,
  `Path("/")`, `Path(".")`) raises `ValueError` before any filesystem work.
  Rationale: `mkstemp` opens with `O_EXCL`, a random name, mode at most
  `0o600`, binary mode on Windows, and a non-inheritable descriptor. Without a
  lock, two writers sharing a fixed temporary name could interleave bytes in
  one temporary file and install a torn document; unique names make concurrent
  writers last-writer-wins but never torn. Truncating by bytes keeps the
  temporary name within `NAME_MAX` (255 bytes) for multibyte names.
  Date/Author: 2026-10-23, planning agent, revised after expert review.

- Decision `DEC-4`: apply the requested mode to the temporary file with
  `os.fchmod(fd, mode)` immediately after creating it and before writing any
  bytes. Where `os.fchmod` is missing (Windows on Python 3.12), call
  `os.chmod(tmp_path, mode)` instead. Validate `mode` before any filesystem
  work: it must be an `int` that is not a `bool`, lie in `0` to `0o777`, and
  include owner read and write (`mode & 0o600 == 0o600`); otherwise raise
  `ValueError`. Rationale: `fchmod` is not subject to the umask, so the final
  mode equals the requested mode exactly. Applying it before writing means
  content never sits in a more permissive file, and it means a `0o755`
  executable from `14.5.1` appears at its final path already executable, which
  is safer than the design's "set execute bits after replacement". The owner
  read and write requirement prevents two traps found in review: a mode without
  owner write sets the Windows read-only attribute, so the next `os.replace`
  onto that target fails; a mode without owner read makes the file unreadable
  to its own writer. Date/Author: 2026-10-23, planning agent, revised after
  expert review.

- Decision `DEC-5`: serialize with
  `json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n"`,
  encoded as UTF-8, and preserve mapping insertion order (no `sort_keys`).
  Rationale: `indent=2` plus a trailing newline, with `ensure_ascii` at its
  default, reproduces the current `FixtureFile.save()` bytes, so adoption does
  not reformat any fixture. Insertion order is deterministic for a given
  payload and keeps fixtures' human-oriented layout (`version` and `metadata`
  before `recordings`); `json.loads` preserves file order, so load-then-save is
  byte-stable. `ensure_ascii=True` serializes lone surrogates (produced when
  arguments are decoded with `surrogateescape`) as `\udcXX` escapes instead of
  failing to encode. `allow_nan=False` rejects `NaN` and infinities, which are
  not valid JSON; every fixture field is a string, integer, `None`, or a list
  or mapping of those, so fixtures are unaffected. The file is written in
  binary mode so Windows does not translate the newline to CRLF. Canonical key
  order for mutable state is the caller's responsibility; the design document
  records this for `14.3`. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-6`: define `AtomicWriteError(OSError)` and
  `AtomicWriteStage(enum.StrEnum)`. `AtomicWriteError` carries `path`, `stage`,
  and a plain attribute `target_replaced`, computed in `__init__` as
  `stage in POST_REPLACE_STAGES`. The message states whether the previous file
  was left intact or replaced. When the cause is an `OSError`, the wrapper
  copies its `errno` and `strerror` and sets `filename` to the target, by
  calling `super().__init__(cause.errno, message, str(path))`; otherwise
  `errno` is `None`. It is always raised with `raise ... from exc`. A
  `serialize` stage means a caller bug (an unserializable payload), and the
  docstring says so. Rationale: the design asks for an "`OSError`-like" error
  that names the target and does not hide which step failed, and
  `RobustRmtreeError(OSError)` is the house precedent. Copying `errno` keeps
  `except OSError` handlers that check `ENOSPC` working. Like
  `RobustRmtreeError`, the custom constructor does not survive pickling; that
  is acceptable while the error is internal. Date/Author: 2026-10-23, planning
  agent, revised after expert review.

- Decision `DEC-7`: wrap only `Exception` subclasses in `AtomicWriteError`.
  `BaseException` subclasses (`KeyboardInterrupt`, `SystemExit`, pytest's
  `Failed`) trigger the same cleanup and then propagate unchanged. Cleanup is
  gated on a "replaced" flag rather than on code position, uses
  `missing_ok=True`, and never masks the primary error; a cleanup failure is
  attached with `BaseException.add_note(...)`. Rationale: Ctrl-C is exactly the
  "interrupted write" the roadmap names, and wrapping it would break normal
  interrupt handling. Date/Author: 2026-10-23, planning agent.

- Decision `DEC-8`: on POSIX, synchronize the parent directory by opening it
  with `os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)` and calling `os.fsync`.
  Treat `EACCES` or `EPERM` when opening the directory, and `EINVAL`,
  `ENOTSUP`, or `EOPNOTSUPP` from `fsync`, as "directory synchronization
  unsupported": skip explicitly, log once at debug level with the path, and
  return. Raise `AtomicWriteError` (stage `sync_directory`,
  `target_replaced=True`) for any other error, such as `EIO`. On Windows, skip
  behind `_path_utils.IS_WINDOWS`. Use plain `os.fsync`, not macOS
  `F_FULLFSYNC`. Rationale: the design permits skipping directory
  synchronization where the platform cannot do it, provided the skip is
  explicit and tested. Those error numbers mean "not supported here", not "the
  write is in doubt", and raising for them would make every fixture save fail
  on WSL drvfs, virtiofs, or a write-only parent directory, where
  `Path.write_text` works today. Crash durability on filesystems that do not
  honour `fsync` is a stated non-goal, and no macOS CI exists to validate
  `F_FULLFSYNC`. Date/Author: 2026-10-23, planning agent, revised after expert
  review.

- Decision `DEC-9`: when `path` is a symbolic link, replace the link itself
  rather than writing through it. Replacement creates a new inode, so it also
  breaks hard links and drops the previous file's ownership, group, access
  control lists, and extended attributes. Rationale: this is what `os.replace`
  does, and it matches `python-atomicwrites` and `fake-uv.py`. Writing through
  a link would put the temporary file in a different directory from the file it
  replaces. The behaviour is documented and tested. A symlinked parent
  directory is unaffected. Date/Author: 2026-10-23, planning agent, revised
  after expert review.

- Decision `DEC-10`: do not use a Rust extension, Verus, Kani, or CrossHair.
  Rationale: the obligations concern the ordering of operating-system calls and
  their failure behaviour, not pure contractual business logic. A Verus proof
  would verify a model of the protocol, not the Python that ships, and would
  bring a Rust build, per-platform wheels, and a toolchain the project does not
  operate (the stray `src/env.rs` is not wired to any build). CrossHair cannot
  explore filesystem side effects. The rigorous argument available is an
  induction over write sequences, discharged by exhaustive enumeration (see
  `LEM-INDUCTION` in `Verification plan`): the helper keeps no state between
  calls, and every handled call ends in one of the finite prior states the
  enumeration starts from, so the single-call and pairwise enumerations cover
  every sequence of writes. The mode-validation predicate is pure and has a
  tiny domain, so it is checked exhaustively over all 512 permission values.
  Date/Author: 2026-10-23, planning agent, revised after expert review.

- Decision `DEC-11`: no ADR. Rationale: the decisions refine an existing
  design rather than choosing between architectures, and the documentation
  style guide reserves ADRs for substantive, hard-to-reverse choices. The
  refinements are recorded in `docs/cmd-mox-fake-capabilities-design.md`, and
  under Option C a short entry is added to
  `docs/python-native-command-mocking-design.md` §9.10. Date/Author:
  2026-10-23, planning agent.

- Decision `DEC-12`: on Windows only, retry a `PermissionError` from the
  replace step with a bounded policy,
  `RetryConfig(max_attempts=5, retry_delay=0.05)` from `cmd_mox.fs_retry`,
  before raising `AtomicWriteError` at stage `replace`. Rationale: Windows
  refuses to replace a file that another process holds open without delete
  sharing, which is common with antivirus scanners, indexers, and parallel test
  workers. Under Option C this would otherwise be a regression for fixture
  saves. POSIX has no such failure, so it does not retry. Date/Author:
  2026-10-23, planning agent, after expert review.

- Decision `DEC-13`: implement each protocol step as a private module-level
  seam function wrapping exactly one operating-system call, with a one-to-one
  mapping to `AtomicWriteStage` members:

  - `_serialize_json` for `serialize`;
  - `_make_parent` for `prepare_directory`;
  - `_create_temporary` for `create_temporary`;
  - `_apply_mode` for `apply_mode`;
  - `_write_all` for `write`;
  - `_fsync_file` for `sync_file`;
  - `_close_temporary` for `close_temporary`;
  - `_replace` for `replace`;
  - `_fsync_directory` for `sync_directory`.

  `_write_all` writes with `os.write` in a loop until every byte is written, so
  there is no user-space buffer. That satisfies the design's "flush before
  `fsync`" intent without an `os.fdopen` ownership handoff. Rationale: tests
  patch `cmd_mox._atomic_write._replace` and similar names, which affects only
  this module, not other code (including Hypothesis's example database) running
  in the same process. One seam per stage makes stage attribution exact, and it
  exposes the important cleanup path where the temporary file exists but
  `fchmod` fails. Date/Author: 2026-10-23, planning agent, after expert review.

- Decision `DEC-14`: type the payload parameter as a covariant
  `JsonInput = None | bool | int | float | str | Sequence[JsonInput] |
  Mapping[str, JsonInput]`,
  and use the invariant
  `JsonValue = None | bool | int | float | str | list[JsonValue] |
  dict[str, JsonValue]`
  only as the round-trip domain in tests. Rationale: the expert panel checked
  with ty 0.0.74 that an invariant alias rejects ordinary values such as
  `dict[str, str]`, which `14.3` callers will pass, while `dict[str, Any]` from
  `FixtureFile.to_dict()` is accepted either way. Tuples type-check under
  `JsonInput` and serialize as lists, so they are outside the round-trip
  domain. Date/Author: 2026-10-23, planning agent, after expert review.

- Decision `DEC-15`: make `mode` keyword-only in both helpers. Rationale: it
  prevents positional confusion between payload and mode, and the helpers are
  internal, so this deviation from the roadmap's written signature is free. The
  same convention is recommended for the `14.5.1` helpers. Date/Author:
  2026-10-23, planning agent.

- Decision `DEC-16`: replace two drafted tests. The concurrent-writer stress
  test becomes a deterministic interleaving test, and the standalone
  `features/atomic_json_persistence.feature` is dropped under Option C in
  favour of two scenarios in `features/recording_session.feature`. Rationale:
  the stress test's evidence depended on scheduling and mostly exercised the
  kernel's atomic rename, an axiom. The only repository-owned property it
  touched, unique temporary names, is checked deterministically by pausing
  writer A at a seam while writer B completes. Every existing feature file
  describes a user-facing flow, and the fixture-recording flow is the
  user-facing face of this helper. Under Option A, the standalone feature is
  kept, because there is then no user-facing flow. Date/Author: 2026-10-23,
  planning agent, after expert review.

- Open decision `DEC-OPEN-1` (requires user approval before Stage A): how to
  satisfy the blocking Skylos dead-code scan. Skylos scans only `cmd_mox/`,
  excludes tests on purpose (`--no-grep-verify`), and the policy in
  `docs/developers-guide.md` and `AGENTS.md` treats a report as genuine dead
  code until a runtime caller is verified. Every exception must name its
  verified runtime caller and must never be used to avoid a removal.
  - Option A: keep `14.1.1` alone. Add a typed entry-point rule for
    `cmd_mox._atomic_write.atomic_write_json`, mirrored in
    `EXPECTED_ENTRYPOINT_REASONS` and `EXPECTED_ENTRYPOINT_TYPES` in
    `tests/test_skylos_lint_contract.py`, with a reason naming `14.1.2` as
    the pending caller, and amend the `14.1.2` roadmap entry so that removing
    the rule is a success criterion. This keeps the roadmap boundary but
    breaks the written policy, because no verified runtime caller exists yet.
  - Option B: leave `make lint` failing until `14.1.2`. Rejected, because
    every milestone must pass all gates.
  - Option C (recommended): deliver `14.1.1` and `14.1.2` together. The
    commit that adds the helper also changes `FixtureFile.save()` to
    `atomic_write_json(path, self.to_dict())`, so the helper is live through a
    real caller and no exception is needed. `14.1.2`'s success criteria
    (existing record-mode round trips pass; fixture-level failure injection
    proves old contents survive) and its user-facing documentation join this
    plan, and both roadmap items are marked done. The user-visible change is
    that fixtures become mode `0o600` on POSIX and are written atomically.
    Git tracks only the execute bit, so committed fixtures are unaffected.
  Recommendation: Option C, supported by all three expert reviewers. The rest
  of this plan is written for Option C, with the Option A differences noted
  where they apply.

## Design review outcome

A panel of six specialist lenses (structure, alternatives, scaling, contracts,
failure modes, long-term viability) reviewed the first draft. Verdict: proceed
with conditions. Every condition is now reflected above:

- Resolve the Skylos question honestly: Option C, with the helper and its
  caller landing in one commit (`DEC-OPEN-1`, `EP-M2`).
- Make the stage contract consistent: nine stages with one seam each, a
  `PRE_REPLACE_STAGES` and `POST_REPLACE_STAGES` split, and a fault table keyed
  by stage (`DEC-13`, `INV-PRESERVE`).
- Replace the unworkable global monkeypatches with module-local seams
  (`DEC-13`).
- Add the "orphan already present" prior state, check residue by exact
  directory listing, and replace the Hypothesis state machine with an induction
  argument over exhaustive single and pairwise fault enumeration
  (`LEM-INDUCTION`).
- Truncate the temporary prefix by bytes, reject empty names, and treat
  unsupported directory synchronization as an explicit skip (`DEC-3`, `DEC-8`).
- Preserve `errno` in the wrapper, accept a covariant payload type, and
  validate modes exhaustively, including rejecting `bool` and modes without
  owner read and write (`DEC-4`, `DEC-6`, `DEC-14`).
- Replace the timing-dependent stress test with a deterministic interleaving
  test, and move behavioural coverage to the fixture-recording feature
  (`DEC-16`).
- Prove fixture byte compatibility directly, and cut snapshots to three
  representative cases (`INV-COMMIT`, `INV-FIXTURE-BYTES`).
- Name the design document's §Architecture as the component architecture
  document and update it; record key ordering, locking on a sibling file, the
  `fsync` cost, and the orphan pattern for later phase 14 items (`EP-M4`).

Pre-mortem scenarios that shaped the design:

1. Orphaned temporary files from a hard-killed writer get committed next to
   fixtures. Mitigation: a fixed, documented pattern, a recommended
   `.gitignore` line, and a sweep deferred to `14.2`, where the lock makes it
   safe.
2. Fixture saves fail on WSL, Docker Desktop, or FUSE mounts because the
   directory `fsync` is rejected. Mitigation: `DEC-8`.
3. Windows fixture saves fail while another process holds the fixture open.
   Mitigation: `DEC-12`.

Strongest alternative considered: a context-manager writer API
(`with atomic_writer(path) as f:`). It supports streaming but gives up
serialize-before-mutate and makes errors raised in the `with` body ambiguous.
`atomic_write_bytes` already provides the reuse point `14.5.1` needs, so the
plan keeps the function form. Linux `O_TMPFILE` with `linkat` would avoid
orphans but needs a Linux-only second code path and still needs a rename to
replace an existing file; it is noted as a possible future mitigation only.

## Outcomes & retrospective

Not started. Complete this section at each milestone and at completion.

## Context and orientation

CmdMox is a Python library that replaces external commands with test doubles
during pytest runs. The relevant parts for this item are:

- `cmd_mox/record/fixture.py`: defines `FixtureFile`, the JSON document that
  record mode writes. `FixtureFile.save(path)` (around line 450) currently does
  `path.parent.mkdir(parents=True, exist_ok=True)` and then
  `path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")`. That is not
  atomic, has no `fsync`, uses the platform default encoding, and does not set
  a mode. `FixtureFile.load()` reads with `json.loads(path.read_text())`, which
  stays unchanged. `RecordingSession.finalize()` in `cmd_mox/record/session.py`
  calls `save()`.
- `cmd_mox/fs_retry.py`: the existing internal filesystem helper module. It
  defines `RetryConfig` and `RobustRmtreeError(OSError)`, the precedent for an
  `OSError` subclass that carries a path and chains its cause.
- `cmd_mox/_path_utils.py`: defines `IS_WINDOWS = os.name == "nt"`. Tests
  simulate Windows branches with
  `monkeypatch.setattr(path_utils, "IS_WINDOWS", True)`.
- `cmd_mox/unittests/`: unit tests, named `test_<module>.py`. The model to
  imitate is `cmd_mox/unittests/test_fs_retry.py`. Fixture tests live in
  `cmd_mox/unittests/test_fixture_file.py`.
- `features/`, `tests/steps/`, and `tests/test_*_bdd.py`: behavioural tests
  using pytest-bdd. Each scenario is bound with an `@scenario(...)` decorator,
  and step modules are registered through a star import carrying a reasoned
  `ruff: ignore` comment, as in `tests/test_recording_session_bdd.py`, whose
  steps live in `tests/steps/recording_session.py`.
- Snapshots use syrupy's default amber serializer, stored in `__snapshots__/`
  beside the test module and updated with `pytest --snapshot-update`.
- Hypothesis is used through explicit decorators. Properties that touch files
  must not use the function-scoped `tmp_path` fixture inside `@given`;
  Hypothesis rejects that with a health check. They create a
  `tempfile.TemporaryDirectory()` per example instead.
- Quality gates (`Makefile`): `make fmt`, `make check-fmt`, `make lint`
  (Ruff, Pylint on PyPy, the DF12 Pylint tier, ambrleaks, the blocking Skylos
  scan, and the typos spelling check), `make typecheck` (ty), `make test`
  (`pytest -v -n auto`), `make markdownlint`, and `make nixie`. The pytest
  timeout is 30 seconds per test.

Terms used in this plan:

- Atomic replacement: renaming a fully written temporary file over the target
  with `os.replace`, so readers see either the whole old file or the whole new
  file, never a mixture.
- `fsync`: an operating-system call asking the kernel to flush a file's data,
  or a directory's entries, to stable storage.
- Torn write: a target file that contains only part of a document because the
  writer stopped part-way.
- Stage: one named step of the write protocol, used to attribute failures.
- Seam: a small private function wrapping one operating-system call, which a
  test can replace to inject a failure at exactly one stage.
- Orphan: a temporary file left behind when a writer is killed before it can
  clean up.

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

- `docs/roadmap.md` item `14.1.1` (`RM-14.1.1`), with success criteria
  `RM-14.1.1-S1` ("interrupted or failing writes preserve the previous complete
  JSON file") and `RM-14.1.1-S2` ("successful writes leave owner-restricted
  files on POSIX").
- Under Option C, `docs/roadmap.md` item `14.1.2` (`RM-14.1.2`), with
  `RM-14.1.2-S1` ("existing record-mode fixture round-trips still pass") and
  `RM-14.1.2-S2` ("new failure-injection tests prove old fixture contents
  survive failed writes"), and its constraint that `FixtureFile.load()` and the
  fixture schema do not change.
- `docs/cmd-mox-fake-capabilities-design.md` (Status: Draft). Traced
  elements:
  - `FCD-STAB`: helpers are internal and may move without deprecation.
  - `FCD-ARCH`: §Architecture, the component list and dependency direction.
  - `FCD-AJP-1` to `FCD-AJP-7`: the seven "Required behaviour" bullets of
    §Atomic JSON persistence (create parent; open the temporary file with the
    mode and `fchmod`; deterministic serialization with a trailing newline;
    flush and `fsync`; `os.replace`; parent-directory `fsync`; remove the
    temporary file on failure), plus `FCD-AJP-ADOPT`, the instruction that
    `FixtureFile.save()` delegates to the helper while `load()` stays a plain
    read.
  - `FCD-EXC`: §Persistence exceptions.
  - `FCD-FAIL`: §Failure semantics.
  - `FCD-SFX`: §Filesystem side-effect helpers (execute bits).
  - `FCD-SEC`: §Security and portability.
  - `FCD-VER`: §Verification strategy.
- `docs/python-native-command-mocking-design.md` §9.5.3 (`FixtureFile`).
- There is no Terms of Reference document for this phase. ADR 001 (linting
  architecture) and `AGENTS.md` govern the gates.

Trace links:

```plaintext
RM-14.1.1-S1 -> FCD-AJP-1..5,7 + FCD-FAIL -> EP-M1, EP-M2 -> INV-PRESERVE, INV-NO-RESIDUE, LEM-STAGE, LEM-INDUCTION
RM-14.1.1-S1 -> FCD-FAIL -> EP-M2 -> INV-KILL
RM-14.1.1-S2 -> FCD-AJP-2 + FCD-SEC -> DEC-4 -> EP-M2 -> INV-MODE, LEM-MODE-DOMAIN
FCD-AJP-3 -> DEC-5 -> EP-M2 -> INV-COMMIT, INV-FIXTURE-BYTES
FCD-AJP-6 + FCD-SEC -> DEC-8 -> EP-M2, EP-M3 -> INV-POST-REPLACE, LEM-DIRSYNC-UNSUPPORTED, LEM-WINDOWS
FCD-EXC -> DEC-6, DEC-7 -> EP-M2 -> LEM-STAGE, LEM-ERRNO, LEM-BASE-EXCEPTION
FCD-AJP-ADOPT + RM-14.1.2-S1 -> EP-M2 -> existing test_fixture_file.py and recording-session BDD suites
RM-14.1.2-S2 -> EP-M2 -> test_save_failure_preserves_previous_fixture, BDD "failed fixture save"
FCD-STAB + FCD-ARCH -> Constraints -> EP-M4 -> no export; design §Architecture updated
FCD-SFX -> DEC-4 -> EP-M4 -> design text amended
```

Known deviations from the letter of the design, each recorded in the design
document in Stage D:

- `DEC-3`: a unique `mkstemp` temporary file instead of a fixed name opened
  with `O_TRUNC`.
- `DEC-4`: modes are applied to the temporary file before the rename,
  including for future executable helpers, and must include owner read and
  write.
- `DEC-5`: insertion order instead of sorted keys.
- `DEC-8`: "unsupported" directory-sync errors are skipped rather than
  reported; no `F_FULLFSYNC`.
- `DEC-13`: unbuffered `os.write` instead of a flushed buffered file object.
- `DEC-15`: `mode` is keyword-only.

## Verification plan

The implementation is structured so its obligations are checkable.
Serialization is a pure function. The commit protocol is a fixed sequence of
nine named stages, each a seam wrapping one operating-system call that tests
can replace without touching other code. The stage set is an enumeration that
tests iterate, and the space of faults and prior states is finite and small
enough to enumerate exhaustively.

Axioms (trusted, not verified):

- `AXM-REPLACE`: on POSIX, `os.replace(src, dst)` with both paths in one
  directory is atomic: observers see the old or the new inode, never a mixture.
  On Windows, `os.replace` uses `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`,
  which replaces the target as one metadata operation on NTFS but is not
  documented as atomic.
- `AXM-FSYNC`: after `os.fsync(fd)` returns, the file's data is on stable
  storage on filesystems that honour `fsync`; after `os.fsync` on a directory
  descriptor returns, the entry is durable on Linux ext4, XFS, and Btrfs.
- `AXM-MKSTEMP`: `tempfile.mkstemp` creates a new file with `O_EXCL`, mode at
  most `0o600`, and a name unique in its directory.
- `AXM-FCHMOD`: `os.fchmod` sets permission bits exactly, independent of the
  umask.
- `AXM-JSON`: `json.loads(json.dumps(v)) == v` for every value in the
  `JsonValue` domain: `None`, `bool`, `int`, finite `float`, `str` (including
  lone surrogates under `ensure_ascii=True`), and lists and string-keyed dicts
  of these.
- `AXM-SIGKILL`: a process killed by `SIGKILL` performs no further system
  calls, so no Python cleanup runs.
- `AXM-SAMEFS`: pytest's `tmp_path` and `tempfile.TemporaryDirectory()` reside
  on one local filesystem.

Prior states used throughout: `absent` (no target), `present` (target holds a
complete document `D0`), and `orphaned` (target holds `D0`, and the directory
already contains an orphan named like a temporary file from an earlier killed
writer). `INV-KILL` proves the `orphaned` state is reachable.

Obligations:

- Obligation `INV-COMMIT`: for every payload `p` in the `JsonValue` domain, a
  successful write leaves the target containing exactly `_serialize_json(p)`,
  and `json.loads` of the file equals `p`.
  - Method: Hypothesis property over the pure serializer, plus a smaller
    Hypothesis property through the filesystem, plus three syrupy snapshots.
  - Rationale: the payload domain is open-ended, so sampled properties are
    proportionate; snapshots pin the byte format that fixtures depend on.
  - Domain: `st.recursive` over JSON scalars (text including surrogates,
    finite floats), lists, and text-keyed dicts. The pure property runs the
    default 100 examples; the filesystem property runs 25 examples with
    `deadline=None`, with `_fsync_file` and `_fsync_directory` replaced by
    counting no-ops because it checks protocol logic, not durability.
    Snapshot cases: a mapping whose insertion order is not sorted, non-ASCII
    text together with a lone surrogate, and a top-level scalar.
  - Artefact: `cmd_mox/unittests/test_atomic_write.py`
    (`test_serialization_round_trips`, `test_written_file_round_trips`,
    `test_serialized_bytes_snapshot`), and
    `cmd_mox/unittests/__snapshots__/test_atomic_write.ambr`.
  - Evidence: red in Stage A (strict `xfail`); green in Stage B.
  - Non-vacuity: `hypothesis.event()` classifies payloads by top-level type
    and by whether they contain non-ASCII text or surrogates; the statistics
    (`pytest --hypothesis-show-statistics`) must show every class. The
    unsorted-mapping snapshot fails under a `sort_keys=True` mutation.

- Obligation `INV-FIXTURE-BYTES`: for every fixture-shaped payload,
  `_serialize_json(p)` equals `(json.dumps(p, indent=2) + "\n").encode()`, the
  bytes `FixtureFile.save()` produced before this change.
  - Method: Hypothesis property reusing the `_RECORDED_INVOCATIONS` strategy
    from `cmd_mox/unittests/test_fixture_file.py` to build `FixtureFile`
    values, plus one named example with non-ASCII arguments.
  - Artefact: `test_serialization_matches_legacy_fixture_bytes` in
    `cmd_mox/unittests/test_fixture_file.py`.
  - Non-vacuity: a mutation to `ensure_ascii=False` or `indent=4` fails it.

- Obligation `INV-PRESERVE`: for every stage `s` in `PRE_REPLACE_STAGES`
  (`serialize`, `prepare_directory`, `create_temporary`, `apply_mode`, `write`,
  `sync_file`, `close_temporary`, `replace`) and every prior state (`absent`,
  `present`, `orphaned`), an injected failure at `s` leaves the target as it
  was (absent stays absent; `D0` stays byte-identical) and leaves the directory
  listing exactly as it was before the call.
  - Method: exhaustive parameterized test over the full product, 8 stages by
    3 prior states, 24 cases.
  - Rationale: the fault space is finite and small, so enumeration is a
    complete check of the protocol's failure branches at seam granularity.
  - Artefact: `test_failure_before_replacement_preserves_target`, driven by a
    `FAULTS` table mapping each `AtomicWriteStage` to its seam name and a
    fault wrapper. Partial-effect wrappers call the real seam first and then
    raise: the `write` fault writes half the bytes and then raises
    `OSError(errno.ENOSPC, ...)`; the `apply_mode` fault runs after the real
    temporary file exists.
  - Evidence: red in Stage A; green in Stage B for all 24 cases.
  - Non-vacuity: `test_fault_table_covers_every_stage` asserts that the keys
    of `FAULTS` equal the set of `AtomicWriteStage` members and that each
    named seam exists on the module, so a new stage without a fault, or a
    renamed seam, fails. A negative control,
    `test_naive_writer_violates_preservation`, runs the same checker against
    a deliberately naive writer (truncate the target, then write half the
    bytes) and asserts that the checker reports the torn file.

- Obligation `INV-NO-RESIDUE`: after any failure handled inside the helper,
  the directory contains no file that was not there before the call, and no
  descriptor opened by the helper remains open.
  - Method: exact directory-listing comparison inside the `INV-PRESERVE` and
    `INV-POST-REPLACE` tests, plus a descriptor check on Linux.
  - Artefact: the same tests, and `test_failures_do_not_leak_descriptors`,
    which lists `/proc/self/fd`, resolves each entry with `os.readlink`, and
    counts only targets under the test's temporary directory (including the
    `" (deleted)"` suffix). It is skipped where `/proc/self/fd` is missing.
  - Non-vacuity: a control opens a descriptor in the temporary directory
    without closing it and asserts the check detects it. The listing
    comparison, unlike a glob, also catches orphans whose names are truncated.

- Obligation `INV-POST-REPLACE`: a failure at `sync_directory` (any stage in
  `POST_REPLACE_STAGES`) that is not "unsupported" leaves the target holding
  the new document, raises `AtomicWriteError` with
  `stage == AtomicWriteStage.SYNC_DIRECTORY` and `target_replaced is True`, and
  performs no rollback.
  - Method: named example test with `OSError(errno.EIO, ...)`, POSIX only.
  - Artefact: `test_directory_sync_failure_reports_replaced_target`.
  - Non-vacuity: the test asserts that the new content is present, so a
    mutation that restores the old file fails it.

- Lemma `LEM-INDUCTION`: for any finite sequence of writes to one target, in
  which each write succeeds or fails at any stage, the target equals the
  serialization of the last committed payload (or the initial state if none
  committed), and no residue accumulates. A write commits when it succeeds or
  fails at a stage in `POST_REPLACE_STAGES`.
  - Argument: the helper keeps no state between calls (it reads no module
    globals that writes modify). By `INV-PRESERVE`, `INV-POST-REPLACE`, and
    `INV-NO-RESIDUE`, every handled call maps a state in
    {`absent`, `present`, `orphaned`} to a state in the same set, with the
    target equal to the last committed document. By induction on sequence
    length, the property holds for every sequence. The exhaustive
    enumerations discharge the induction step for every stage and prior
    state.
  - Method: the argument above, plus `test_fault_pairs_compose`, an
    exhaustive check of every ordered pair of operations, each drawn from the
    nine faults plus success (100 cases), with the sync seams replaced by
    counting no-ops. It confirms the step composes, for example that a
    failure leaves nothing that disturbs the next write.
  - Non-vacuity: the pairwise test asserts that each case's first operation
    actually reached its stage (the fault wrapper records that it fired), so
    no case passes because the fault never triggered.

- Obligation `INV-MODE`: on POSIX, after a successful write the target's
  permission bits equal the requested mode. The table covers umask values
  `0o000`, `0o022`, and `0o077`; requested modes `0o600` (default), `0o640`,
  `0o644`, and `0o755`; and prior targets with mode `0o644` and `0o666`.
  - Method: parameterized test over that finite table, with a fixture that
    sets the umask and restores it in `finally`.
  - Artefact: `test_mode_is_exact_regardless_of_umask`.
  - Non-vacuity: the default-mode rows are weak on their own, because
    `mkstemp` already yields `0o600`. The `0o640`, `0o644`, and `0o755` rows
    kill a mutant that drops `fchmod`, and the umask `0o077` rows kill a
    mutant that applies `mode & ~umask`. The test asserts that both kinds of
    rows are present in the table.

- Lemma `LEM-MODE-DOMAIN`: the mode validator accepts exactly the integers
  `m` with `0 <= m <= 0o777` and `m & 0o600 == 0o600`, and rejects everything
  else with `ValueError` before any filesystem work.
  - Method: exhaustive test over all 512 values from `0` to `0o777`, plus
    `-1`, `0o1000`, `True`, `0.0`, and `"600"`.
  - Artefact: `test_mode_validation_is_exact`.
  - Non-vacuity: the domain contains 128 accepted and 384 rejected values;
    the test asserts both counts.

- Obligation `INV-KILL`: a writer process killed with `SIGKILL` after the
  temporary file is synchronized and closed, but before `os.replace`, leaves
  the previous complete document at the target.
  - Method: end-to-end test across a real process boundary. The parent
    launches `[sys.executable, "-I", "-c", CHILD, target, repo_root]` with
    `cwd=tmp_path`, where `repo_root` comes from
    `Path(cmd_mox.__file__).parents[1]`, and the child inserts it at the
    front of `sys.path`. The child replaces `_atomic_write._replace` with a
    function that prints `READY`, flushes, and blocks on `sys.stdin.read()`;
    if the parent dies, the child reads end-of-file and exits without
    replacing. The parent waits for `READY` with `selectors` and a ten-second
    timeout, sends `SIGKILL`, waits up to five seconds, and in `finally` kills,
    waits, and closes all three pipes.
  - Artefact: `tests/test_atomic_write_process_boundary.py`
    (`test_killed_writer_leaves_previous_document`), skipped on Windows.
  - Evidence: red in Stage A; green in Stage B. A prototype ran 10 of 10
    times in 0.09 to 0.22 seconds each during the expert review.
  - Non-vacuity: receiving `READY` proves the child paused inside the
    protocol after writing; the test also asserts that exactly one orphan
    exists, documenting the known residue after a hard kill.

- Obligation `INV-INTERLEAVE`: two writers to one target cannot corrupt each
  other through shared temporary files. Writer A, paused at the
  `close_temporary` seam, and writer B, which completes meanwhile, use
  different temporary names. When A resumes, the target holds A's document
  (last writer wins), and no residue remains.
  - Method: deterministic test with a thread for writer A, gated by a
    `threading.Event` inside a wrapped seam, and a timeout on every wait.
  - Artefact: `test_interleaved_writers_use_distinct_temporaries`.
  - Non-vacuity: the test records both temporary names and asserts that they
    differ, so a fixed-name mutant fails it.

- Lemma `LEM-SERIALIZE-FIRST`: an unserializable payload (a `set`, a circular
  list, `float("nan")`) raises `AtomicWriteError` with stage `serialize`. Its
  `__cause__` is the original `TypeError` or `ValueError`, and the filesystem
  is untouched: the missing parent directory is still missing.
  - Artefact: `test_unserializable_payload_touches_nothing`, parameterized
    over the three payloads.

- Lemma `LEM-STAGE`: for every injected fault, the raised `AtomicWriteError`
  has the matching `stage`, `path` equal to the target, `target_replaced` equal
  to `stage in POST_REPLACE_STAGES`, and `__cause__` identical to the injected
  exception object. Asserted inside the `INV-PRESERVE` test.

- Lemma `LEM-ERRNO`: when the cause is an `OSError`, the wrapper's `errno` and
  `strerror` equal the cause's, and its `filename` is the target path; when the
  cause is not an `OSError`, `errno` is `None`.
  - Artefact: `test_error_preserves_cause_errno`.

- Lemma `LEM-BASE-EXCEPTION`: a `KeyboardInterrupt` raised at the `write`
  stage propagates as `KeyboardInterrupt`, not wrapped, and the temporary file
  is removed.
  - Artefact: `test_keyboard_interrupt_cleans_up_and_propagates`.

- Lemma `LEM-CLEANUP-NOTE`: if removing the temporary file also fails, the
  primary `AtomicWriteError` still propagates, and its `__notes__` mention the
  temporary path.
  - Artefact: `test_cleanup_failure_is_noted_not_raised`.

- Lemma `LEM-DIRSYNC-UNSUPPORTED`: on POSIX, `PermissionError` when opening
  the directory, and `EINVAL`, `ENOTSUP`, or `EOPNOTSUPP` from the directory
  `fsync`, result in a successful write, with one debug log record that names
  the directory.
  - Artefact: `test_unsupported_directory_sync_is_skipped`, parameterized
    over those four cases, plus a real-filesystem example with a parent
    directory of mode `0o300`, which reproduces the `PermissionError` case
    without patching.
  - Non-vacuity: the `EIO` case in `INV-POST-REPLACE` shows that the skip is
    not a catch-all.

- Lemma `LEM-WINDOWS`: when `_path_utils.IS_WINDOWS` is true, the helper does
  not call `_fsync_directory`'s operating-system work; when `os.fchmod` is
  unavailable, `_apply_mode` falls back to `os.chmod`; and a `PermissionError`
  from `_replace` is retried up to five attempts on Windows only, then raised
  at stage `replace`.
  - Method: unit tests simulating each condition with `monkeypatch`, plus a
    real Windows run in EP-M3, which executes the whole unit module on
    `windows-latest`.
  - Artefact: `test_windows_skips_directory_sync`,
    `test_missing_fchmod_falls_back_to_chmod`, and
    `test_windows_retries_replace_permission_errors`.

- Lemma `LEM-NAMES`: `_temporary_prefix` keeps the encoded prefix at most 66
  bytes (dot, 64 bytes, dot) without splitting a character, and a write to a
  253-byte multibyte name succeeds. Empty names (`Path("")`, `Path("/")`,
  `Path(".")`) raise `ValueError`. A target that is a directory fails at stage
  `replace` and leaves no residue.
  - Artefact: `test_long_multibyte_names_are_supported`,
    `test_empty_names_are_rejected`, and
    `test_directory_target_fails_at_replace`.

- Lemma `LEM-SYMLINK`: when the target is a symbolic link, a successful write
  replaces the link with a regular file and leaves the link's former
  destination unchanged (POSIX only).
  - Artefact: `test_symlink_target_is_replaced_not_followed`.

- Obligation `INV-FIXTURE-ADOPTION` (Option C): `FixtureFile.save()` writes
  through the helper. Existing round-trip tests in
  `cmd_mox/unittests/test_fixture_file.py` and the recording-session
  behavioural suite pass unchanged. A save that fails at stage `replace` raises
  `AtomicWriteError` and leaves the previous fixture loadable and
  byte-identical. A saved fixture is mode `0o600` on POSIX.
  - Artefact: `test_save_failure_preserves_previous_fixture` and
    `test_save_writes_owner_only_fixture` in
    `cmd_mox/unittests/test_fixture_file.py`, and the two new scenarios in
    `features/recording_session.feature`.
  - Non-vacuity: the mode test fails against the old `write_text` code under
    umask `0o022` (the file would be `0o644`); this is the red evidence.

Residual gaps: crash durability (power loss) cannot be tested in CI and rests on
`AXM-FSYNC`. macOS `fsync` does not flush the drive cache (`DEC-8`). Windows
replacement is not documented as atomic (`AXM-REPLACE`). The process tests do
not run on Windows. Orphans after a hard kill are documented, not swept
(`14.2`).

Mutation audit: Stage C runs `mutmut` on `cmd_mox/_atomic_write.py` only,
time-boxed to one hour, and promotes each surviving, meaningful mutant to a
test. Equivalent mutants are recorded in `Surprises & discoveries`.

## Plan of work

Stage 0 (no code): obtain approval of this plan and the user's choice for
`DEC-OPEN-1`, then record both in `Decision log`. Stage A must not start before
that.

Stage A (EP-M1, red): write every test named in `Verification plan`. Committed
tests must not leave the repository red, and a stub module would itself be dead
code under Skylos, so no production code is added in this stage. Tests reach
the helper through a fixture,
`atomic_write = importlib.import_module("cmd_mox._atomic_write")`, which raises
`ModuleNotFoundError` until Stage B, and each such test carries
`@pytest.mark.xfail(strict=True, raises=ModuleNotFoundError,
reason="14.1.1: cmd_mox._atomic_write not implemented yet")`.
The fixture-level mode and failure tests (Option C) fail against the current
`write_text` code for the right reason (`AssertionError` on mode `0o644`, and
`ModuleNotFoundError` for the seam), so they carry strict `xfail` markers
naming those exceptions. Add the two new scenarios to
`features/recording_session.feature` with matching steps and bindings, marked
the same way. Validate: the focused run lists each new test as `XFAIL` with its
reason; all four gates pass; commit.

Stage B (EP-M2, green, one commit): create `cmd_mox/_atomic_write.py` with the
interface in `Interfaces and dependencies`; change `FixtureFile.save()` to call
`atomic_write_json(path, self.to_dict())` (Option C); replace the import
fixture with a normal module import; remove every `xfail` marker; and generate
the snapshot with `--snapshot-update`, then inspect the `.ambr` file by hand.
Validate: the focused tests and the existing fixture and recording suites pass;
all four gates pass; commit.

Stage C (EP-M3, Windows and hardening): add
`cmd_mox/unittests/test_atomic_write.py` to `WINDOWS_SMOKE_ARGS` in the
`Makefile`; refactor for clarity without behaviour change; run the mutation
audit and promote survivors. Validate: all four gates pass; commit; the Windows
CI job passes on the pull request.

Stage D (EP-M4, documentation): update the documents listed under EP-M4 and
mark the roadmap items done. Validate: `make markdownlint`, `make nixie`, and
all four code gates pass; commit.

## Milestones and plateaus

### EP-M1: red tests

- Outcome: every planned test exists and fails for its expected reason under
  strict `xfail`; no production code changes; the repository is green.
- Requirements and gaps: specifies `RM-14.1.1-S1`, `RM-14.1.1-S2`,
  `RM-14.1.2-S1`, `RM-14.1.2-S2`, and every traced `FCD-*` element.
- Acceptance evidence: focused pytest output listing each new test as
  `XFAIL`, with the reason strings above.
- Conformance check:
  - No requirement is satisfied yet; each is specified by a test.
  - Design unchanged.
  - Upstream assumptions unchanged.
  - No public interface or dependency change.
  - No trust-boundary or persisted-format change.
  - Trace links current.
- Recovery: revert the milestone commit.
- Remaining gaps: implementation and adoption.
- Compatibility decision: none.

### EP-M2: working helper, adopted by `FixtureFile.save()`

- Outcome: the helper satisfies every obligation in `Verification plan` on
  Linux, and fixtures are saved through it.
- Requirements and gaps: discharges `RM-14.1.1-S1`, `RM-14.1.1-S2`,
  `RM-14.1.2-S1`, `RM-14.1.2-S2`, `FCD-AJP-1..7`, `FCD-AJP-ADOPT`, `FCD-EXC`,
  `FCD-FAIL`, and the POSIX part of `FCD-SEC`.
- Acceptance evidence: all new and existing tests pass; the snapshot file is
  committed; `make lint` passes with no new Skylos exception.
- Conformance check:
  - Requirements above satisfied.
  - Design followed, with the deviations listed in `Conformance basis`.
  - Upstream assumptions unchanged.
  - No public export; no dependency change.
  - Persisted format: fixture bytes unchanged (`INV-FIXTURE-BYTES`); fixture
    mode becomes `0o600` on POSIX, an intended, documented change.
  - Trace links current.
- Recovery: revert the milestone commit; EP-M1's strict `xfail` markers
  return.
- Remaining gaps: Windows execution evidence; documentation.
- Compatibility decision: none. Existing fixtures on disk stay loadable,
  because `load()` and the schema do not change.

### EP-M3: Windows coverage and hardening

- Outcome: the unit module runs on `windows-latest` in CI, the code is
  refactored for readability, and surviving mutants are addressed.
- Requirements and gaps: discharges the Windows part of `FCD-SEC` and
  `LEM-WINDOWS` on a real Windows host.
- Acceptance evidence: the pull request's Windows smoke job passes, with the
  new module listed in its log; mutation audit notes recorded.
- Conformance check: as for EP-M2; the `Makefile` change is limited to
  `WINDOWS_SMOKE_ARGS`.
- Recovery: remove the module from `WINDOWS_SMOKE_ARGS`, record the Windows
  failure in `Surprises & discoveries`, and escalate.
- Remaining gaps: documentation.
- Compatibility decision: none.

### EP-M4: documentation and roadmap

- Outcome: documentation describes the helper, its conventions, and the
  deviations; the roadmap marks the delivered items done.
- Changes:
  - `docs/cmd-mox-fake-capabilities-design.md`, which serves as the component
    architecture document for phase 14:
    - In §Architecture, give the module path `cmd_mox/_atomic_write.py`, list
      `atomic_write_bytes` beside `atomic_write_json`, and restate their
      internal status.
    - In §Atomic JSON persistence, record the signatures, `JsonInput`, every
      deviation listed in `Conformance basis`, the stage enumeration and
      `target_replaced`, `errno` preservation, `BaseException` handling,
      symlink and inode replacement effects (`DEC-9`), the Windows replace
      retry (`DEC-12`), the orphan name pattern and its sweep deferred to
      `14.2`, and the measured `fsync` cost.
    - Add an "Implementation status" note stating that `14.1.1` and `14.1.2`
      are implemented.
    - In §File-backed state store, state that canonical key order is the
      caller's responsibility (`DEC-5`), and that locks must be taken on a
      sibling lock file, never on the JSON target, whose inode is replaced on
      every write.
    - In §Filesystem side-effect helpers, replace "set execute bits after the
      content has been written and replaced" with the `DEC-4` behaviour.
  - `docs/python-native-command-mocking-design.md`: in §9.5.3, state that
    `save()` writes atomically through the internal helper with mode `0o600`;
    add a §9.10 entry, "Atomic fixture persistence", that summarizes the
    decision and links to the fake-capabilities design.
  - `docs/usage-guide.md`: under "Fixture file format", add a short note that
    fixtures are written atomically, so an interrupted recording never leaves
    a truncated fixture; that new fixtures are owner-only (`0o600`) on POSIX;
    that a failed save raises an `OSError` subclass and leaves the previous
    fixture intact; and that an orphan matching `.<name>.*.tmp` may remain
    after a hard kill, with a suggested `.gitignore` line (`.*.json.*.tmp`).
  - `docs/developers-guide.md`: add a section "Atomic file persistence"
    between "Property-based testing" and "IPC request dispatch". It states
    that internal JSON and state writers must call
    `cmd_mox._atomic_write.atomic_write_json` or `atomic_write_bytes` rather
    than `Path.write_text`, summarizes the error contract, and records the
    test conventions introduced here: POSIX-only mode assertions with
    `stat.S_IMODE`, the umask fixture, fault tables keyed by
    `AtomicWriteStage` that patch module-local seams, `TemporaryDirectory`
    inside Hypothesis tests, and launching child processes with `-I`.
  - `docs/roadmap.md`: change `- [ ] 14.1.1.` and `- [ ] 14.1.2.` to
    `- [x]`. Under Option A, mark only `14.1.1`, and amend `14.1.2` to name
    removal of the temporary Skylos rule as a success criterion.
  - Under Option A only: do not edit `docs/usage-guide.md` or
    `docs/python-native-command-mocking-design.md`, and describe the
    temporary entry-point rule and its removal obligation in the developers
    guide's Skylos section.
- Acceptance evidence: `make markdownlint` and `make nixie` pass; the four
  code gates pass.
- Conformance check: every deviation is reconciled with the design document;
  `FCD-STAB` confirmed (no export).
- Recovery: documentation commits revert cleanly.
- Compatibility decision: none.

## Concrete steps

Run every command from the repository root,
`/home/leynos/.lody/repos/github---leynos---cmd-mox/worktrees/7f849fe1-6e1e-46f4-8efe-207369025e5e`.
Log names follow the pattern
`/tmp/$ACTION-cmd-mox-14-1-1-implement-internal-atomic-write-json.out`.

Focused test run, used for red and green checks:

```bash
set -o pipefail
uv run --group dev -- pytest -v \
  cmd_mox/unittests/test_atomic_write.py \
  cmd_mox/unittests/test_fixture_file.py \
  tests/test_recording_session_bdd.py \
  tests/test_atomic_write_process_boundary.py \
  2>&1 | tee /tmp/focused-cmd-mox-14-1-1-implement-internal-atomic-write-json.out
```

Expected in Stage A (abridged):

```plaintext
cmd_mox/unittests/test_atomic_write.py::test_failure_before_replacement_preserves_target[write-present] XFAIL
cmd_mox/unittests/test_fixture_file.py::test_save_writes_owner_only_fixture XFAIL
tests/test_atomic_write_process_boundary.py::test_killed_writer_leaves_previous_document XFAIL
```

Expected in Stage B: every listed test `PASSED`, with POSIX-only tests
`SKIPPED` on Windows.

Snapshot generation, Stage B only, followed by a manual review of the file:

```bash
uv run --group dev -- pytest cmd_mox/unittests/test_atomic_write.py \
  -k snapshot --snapshot-update
git diff -- cmd_mox/unittests/__snapshots__/test_atomic_write.ambr
```

Hypothesis classification evidence:

```bash
uv run --group dev -- pytest cmd_mox/unittests/test_atomic_write.py \
  -k round_trips --hypothesis-show-statistics
```

Gates, run sequentially after each milestone (prefer delegating the full run to
the `scrutineer` agent):

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

Mutation audit (Stage C). The repository configures `mutmut` in `[tool.mutmut]`
in `pyproject.toml`; restrict the run to the new module with a temporary,
uncommitted local override of `paths_to_mutate`, then:

```bash
uv run --group dev -- mutmut run
uv run --group dev -- mutmut results
```

Record the `mutmut` version, the override used, and the results in
`Surprises & discoveries`, then discard the override with
`git checkout -- pyproject.toml`.

Commit after each milestone with an imperative subject, for example
`Add failing tests for atomic JSON writes`, and keep commits atomic.

## Validation and acceptance

Behaviour to observe after Stage B:

- Calling `atomic_write_json(tmp_path / "a" / "b.json", {"z": 1, "a": [1, 2]})`
  creates `a/`, and the file contains exactly the following, then one newline,
  with mode `0o600` on POSIX:

  ```plaintext
  {
    "z": 1,
    "a": [
      1,
      2
    ]
  }
  ```

- With `_replace` patched to raise `PermissionError(errno.EACCES, ...)`, the
  call raises `AtomicWriteError` whose `stage` is `replace`, whose
  `target_replaced` is false, whose `errno` is `EACCES`, and whose `__cause__`
  is the `PermissionError`; the previous file is byte-identical, and the
  directory listing is unchanged.
- Recording a session with `RecordingSession` and finalizing it leaves the
  fixture with mode `0o600` on POSIX and the same bytes as before the change.

Behavioural specification. Under Option C, append these scenarios to
`features/recording_session.feature`, implement their steps in
`tests/steps/recording_session.py`, and bind each with its own `@scenario`
function in `tests/test_recording_session_bdd.py`:

```gherkin
  Scenario: recorded fixture is readable only by its owner
    Given a recording session targeting "fixtures/git_status.json"
    When the session records a "git" invocation and finalizes
    Then the fixture file loads with 1 recording
    And the fixture file is readable and writable only by its owner
    And the fixture directory contains only the fixture file

  Scenario: failed fixture save keeps the previous fixture
    Given an existing fixture "fixtures/git_status.json" with 1 recording
    And the final rename of fixture writes will fail
    When a new session records 2 invocations to the same fixture and finalizes
    Then finalizing fails with an atomic write error at stage "replace"
    And the fixture file still loads with 1 recording
    And the fixture directory contains only the fixture file
```

Reuse existing steps from `tests/steps/recording_session.py` where their
wording matches; the step text above may be adjusted to match existing step
phrasing, provided the scenario meaning is unchanged, and this section must
then be updated to quote the final text. The owner-only step asserts on POSIX
and is skipped on Windows with an explanatory reason. The rename-failure step
patches `cmd_mox._atomic_write._replace` through `monkeypatch`.

Under Option A, the same behaviours are specified instead in a standalone
`features/atomic_json_persistence.feature`, with steps in
`tests/steps/atomic_json_persistence.py` and bindings in
`tests/test_atomic_json_persistence_bdd.py`, calling the helper directly.

Red-Green-Refactor evidence to record in `Artefacts and notes`:

- Red: the focused command, showing `XFAIL` for every new test with its
  strict reason; for the fixture mode test, the underlying `AssertionError`
  reports mode `0o644`.
- Green: the same command, with everything `PASSED`, after the markers are
  removed.
- Refactor: the same command plus the four gates, all passing.

Quality criteria (what "done" means):

- Tests: the focused suite and `make test` pass; every obligation in
  `Verification plan` has a passing artefact.
- Verification: every `INV-*` and `LEM-*` item is discharged as described,
  with its non-vacuity control passing; the mutation audit is recorded.
- Lint and type checking: `make check-fmt`, `make lint`, and `make typecheck`
  pass with no new unexplained suppressions and no new Skylos exception (Option
  C).
- Documentation: `make markdownlint` and `make nixie` pass.
- Security: default mode `0o600`; on POSIX, no temporary file is ever more
  permissive than the requested mode.

## Idempotence and recovery

Every step can be repeated. Tests create files only under pytest temporary
directories or `tempfile.TemporaryDirectory()`. The umask fixture restores the
previous umask in `finally`. Child processes are killed and reaped in
`finally`. If the snapshot is generated wrongly, delete
`cmd_mox/unittests/__snapshots__/test_atomic_write.ambr` and regenerate it.
Each milestone is one or more commits; `git revert` of a milestone's commits
returns the branch to the previous plateau.

## Artefacts and notes

Record red, green, and gate transcripts here during implementation, trimmed to
the lines that prove the result.

## Interfaces and dependencies

No new dependencies. Standard-library modules used: `collections.abc`, `enum`,
`errno`, `json`, `logging`, `os`, `pathlib`, `tempfile`, and `typing`, plus
`cmd_mox._path_utils` and `cmd_mox.fs_retry.RetryConfig`. Tests also use
`importlib`, `selectors`, `signal`, `stat`, `subprocess`, and `threading`.

In `cmd_mox/_atomic_write.py`, define:

```python
type JsonInput = (
    None
    | bool
    | int
    | float
    | str
    | cabc.Sequence[JsonInput]
    | cabc.Mapping[str, JsonInput]
)
type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


class AtomicWriteStage(enum.StrEnum):
    """Named steps of the atomic write protocol, in execution order."""

    SERIALIZE = "serialize"
    PREPARE_DIRECTORY = "prepare_directory"
    CREATE_TEMPORARY = "create_temporary"
    APPLY_MODE = "apply_mode"
    WRITE = "write"
    SYNC_FILE = "sync_file"
    CLOSE_TEMPORARY = "close_temporary"
    REPLACE = "replace"
    SYNC_DIRECTORY = "sync_directory"


POST_REPLACE_STAGES: typ.Final = frozenset({AtomicWriteStage.SYNC_DIRECTORY})
PRE_REPLACE_STAGES: typ.Final = frozenset(AtomicWriteStage) - POST_REPLACE_STAGES


class AtomicWriteError(OSError):
    """Raised when a durable atomic write cannot be completed."""

    path: Path
    stage: AtomicWriteStage
    target_replaced: bool

    def __init__(
        self, path: Path, stage: AtomicWriteStage, cause: BaseException
    ) -> None: ...


def atomic_write_json(
    path: Path, payload: JsonInput, *, mode: int = 0o600
) -> None: ...


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None: ...
```

The private seams, each wrapping one operating-system call, are
`_serialize_json(payload) -> bytes`, `_make_parent(path) -> None`,
`_create_temporary(path) -> tuple[int, Path]`,
`_apply_mode(fd, tmp_path, mode) -> None`, `_write_all(fd, data) -> None`,
`_fsync_file(fd) -> None`, `_close_temporary(fd) -> None`,
`_replace(tmp_path, path) -> None` (with the Windows retry of `DEC-12`), and
`_fsync_directory(directory) -> None` (with the skip rules of `DEC-8`). The
helpers `_validate_mode(mode) -> None` and `_temporary_prefix(name) -> str` are
pure.

`atomic_write_bytes` validates the mode and the target name, then runs the
seams in the order listed in `AtomicWriteStage`, tracking the current stage and
whether the temporary file still exists. Any `Exception` raised before the
replace removes the temporary file (if it exists) and is re-raised as
`AtomicWriteError(path, stage, exc)` from `exc`; any other `BaseException` gets
the same cleanup and propagates unchanged. A failure in `_fsync_directory` that
is not "unsupported" raises
`AtomicWriteError(path, AtomicWriteStage.SYNC_DIRECTORY, exc)` without cleanup
or rollback. `atomic_write_json` calls `_serialize_json` (wrapping `TypeError`
and `ValueError` as stage `serialize`) and then `atomic_write_bytes`.

Keep NumPy-style docstrings with `Parameters`, `Raises`, and `Examples`
sections on `atomic_write_json`, `atomic_write_bytes`, `AtomicWriteError`, and
`AtomicWriteStage`, and one-line docstrings on the private seams.

Under Option C, `FixtureFile.save()` in `cmd_mox/record/fixture.py` becomes:

```python
def save(self, path: Path) -> None:
    """...existing docstring, plus a Raises section for AtomicWriteError..."""
    atomic_write_json(path, self.to_dict())
```

## Revision notes

- 2026-10-23: revised after the expert-panel review. Recommended Option C for
  `DEC-OPEN-1` and restructured the stages so the helper and its caller land in
  one commit. Introduced module-local seams (`DEC-13`), nine one-to-one stages,
  the `orphaned` prior state, and the induction argument (`LEM-INDUCTION`) in
  place of the Hypothesis state machine. Replaced the stress test with a
  deterministic interleaving test, and moved behavioural coverage to the
  recording-session feature. Added byte-based name truncation,
  unsupported-directory-sync skips, the Windows replace retry, `errno`
  preservation, a covariant payload type, exhaustive mode validation, and the
  fixture byte-compatibility property. Raised the scope tolerance to 18 files
  and 1,300 lines to cover `14.1.2`. Remaining work is unchanged in kind:
  approval, then Stages A to D.
