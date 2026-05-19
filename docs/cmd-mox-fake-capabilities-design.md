# CmdMox fake capabilities design

Status: Draft  
Audience: CmdMox maintainers and contributors  
Companion documents:
[`python-native-command-mocking-design.md`](./python-native-command-mocking-design.md),
[`cmd-mox-roadmap.md`](./cmd-mox-roadmap.md)

This design extends CmdMox with reusable helpers for realistic command fakes.
The immediate trigger is the `fake-uv.py` fixture used by
`dev-env-rocky`, which combines durable JSON state writes, file-backed command
state, advisory locking, subcommand dispatch, and filesystem side effects in a
single command double.[^1] CmdMox already has the `.runs(...)` hook needed to
express that behaviour, but each test suite must currently rebuild the
infrastructure around it.

The design keeps the public command-double model intact. The first delivery
slice hardens fixture persistence with an internal `atomic_write_json(...)`
helper. The second slice introduces a reusable file-backed state store that
`.runs(...)` handlers can use. Routing and side-effect helpers come later
because they reduce boilerplate rather than protecting persisted data.

## Stability policy

This design separates implementation helpers from future public APIs:

- `atomic_write_json(...)`, `file_lock(...)`, and any platform-specific lock
  backend are internal helpers. They may move between private modules without
  deprecation as long as documented CmdMox behaviour remains intact.
- `JsonStateStore`, `SubcommandRouter`, and filesystem side-effect helpers are
  candidate public APIs. They should first ship as explicitly provisional
  exports or documented examples. They become stable only after the usage guide
  names them as public API and assigns compatibility guarantees.
- Exception classes named in this design are stable once exported from the
  public package namespace. Internal call sites may still wrap or chain lower
  level operating-system exceptions.

The roadmap therefore treats documentation and adoption as a graduation step:
helpers are not public merely because they exist in the implementation.

## Problem

`FixtureFile.save()` currently creates parent directories and writes JSON with
`Path.write_text(...)`. That is easy to understand but weak under interrupted
writes, parallel test activity, and permission-sensitive fixture directories.
A partial write can leave a fixture unreadable, and the write path does not set
an explicit file mode.

Stateful command fakes expose the same weakness. Package managers, cloud
command-line interfaces (CLIs), `git`, `docker`, and `kubectl` often mutate
state in one invocation and report it in a later invocation. A `.runs(...)`
handler can model that state in memory, but in-memory state disappears when the
fake spans multiple processes or when a test intentionally invokes the same
fake command through independent process boundaries.

## Goals

- Persist fixture JSON through an atomic replacement sequence:
  temporary file, flush, file `fsync`, `os.replace`, and parent-directory
  `fsync` where the platform supports it.
- Restrict newly created JSON state files to owner read/write permissions by
  default (`0o600` on POSIX-style filesystems).
- Provide a file-backed state-store helper for `.runs(...)` handlers without
  changing the `CommandDouble` lifecycle.
- Provide a lock-with-timeout primitive that serializes shared file-backed
  state and reports contention separately from unrelated filesystem failures.
- Add lower-priority helpers for argv subcommand routing and filesystem side
  effects such as creating executable shims.

## Non-goals

- Replacing `.runs(...)` as the extension point for dynamic command behaviour.
- Introducing a full package-manager, cloud-CLI, `git`, `docker`, or
  `kubectl` simulator into CmdMox core.
- Guaranteeing crash durability on filesystems that do not honour `fsync` or
  atomic rename semantics.
- Designing a distributed lock. Locks are local to the host running the tests.
- Making router and side-effect helpers mandatory for command fakes.

## Prior art and constraints

The `fake-uv.py` fixture demonstrates the target behaviour: state mutations are
guarded by a sibling lock file, writes go through a `0o600` temporary file,
the temporary file is flushed and `fsync` is called on it,
`os.replace(...)` installs the new state, and `fsync` is called on the parent
directory after replacement.[^1]

Python documents `os.replace(...)` as an atomic replacement on POSIX when the
source and destination are on the same filesystem, and documents the need to
flush a buffered file before calling `os.fsync(...)` on its descriptor.[^2]
CmdMox should therefore create temporary files in the target directory rather
than in a global temporary location.

Existing Python locking libraries such as `filelock` and `portalocker` prove
that a small lock object with timeout semantics is a familiar user-facing
shape.[^3] CmdMox should not add a dependency before implementation pressure
requires it, but the internal API should leave room for a POSIX and Windows
backend.

CmdMox already contains retry-oriented filesystem helpers in
`cmd_mox.fs_retry`, and record mode already persists fixtures through
`FixtureFile.save()`. The new persistence helpers should live beside those
internal filesystem utilities, while state-store and router helpers should
stay separate from the record/replay fixture schema.

## Architecture

The fake-capabilities extension has five components:

- `atomic_write_json(path, payload, mode=0o600)`: internal JSON persistence
  helper used first by `FixtureFile.save()`.
- `file_lock(path, timeout=...)`: internal context manager that acquires an
  exclusive advisory lock, retries only on lock contention, and raises a
  timeout-specific error when the deadline expires.
- `JsonStateStore`: helper for reading and mutating a JSON document under the
  lock and atomic-write contracts.
- `SubcommandRouter`: optional argv router for `.runs(...)` handlers.
- Filesystem side-effect helpers: optional functions for creating files,
  executable scripts, and command shims as part of fake command behaviour.

The dependency direction is deliberate. The state store depends on the lock
and atomic JSON helper. The router and side-effect helpers may use the state
store, but the state store does not know about command routing.

## Atomic JSON persistence

`atomic_write_json(path, payload, mode=0o600)` writes JSON data to a sibling
temporary file and replaces the target only after the temporary file has been
fully written. The helper owns serialization so all fixture and state files use
consistent formatting.

Required behaviour:

- Create `path.parent` before opening the temporary file.
- Open the temporary file with `os.open(..., O_CREAT | O_TRUNC | O_WRONLY,
  mode)` and call `os.fchmod(...)` where available.
- Serialize JSON with deterministic key ordering when the payload is a plain
  mapping, plus a trailing newline for Git-friendly diffs.
- Flush the buffered file object and call `os.fsync(...)` before replacement.
- Replace the target with `os.replace(tmp_path, path)`.
- Open and `fsync` the parent directory after replacement where the operating
  system supports directory file descriptors.
- Remove the temporary file on failure if it still exists.

`FixtureFile.save()` should delegate to this helper by converting the fixture
to a dictionary and passing the destination path. `FixtureFile.load()` can
remain a plain JSON read because atomic replacement means readers either see
the old complete file or the new complete file.

### Persistence exceptions

`atomic_write_json(...)` should raise `AtomicWriteError` when the helper cannot
complete a durable write. The exception should include the target path and
chain the original exception with `raise ... from ...`. It should not hide
whether the root cause was JSON serialization, temporary-file creation,
`fsync`, replacement, or parent-directory synchronization.

Callers that only need ordinary failure handling can treat `AtomicWriteError`
as an `OSError`-like persistence failure. Callers that need to preserve an old
fixture can catch `AtomicWriteError` specifically and report that the previous
target file should still be complete when replacement did not occur.

### Failure semantics

The old target file must remain in place when serialization, flush, `fsync`, or
replacement fails before `os.replace(...)` succeeds. Once replacement succeeds,
a later parent-directory `fsync` failure should be reported to the caller
because durability is uncertain, but the helper must not try to roll back the
target file.

## Lock with timeout

The lock primitive serializes access to a lock file near the JSON document
being protected. It should acquire an exclusive lock using non-blocking calls
inside a monotonic-deadline loop.

Required behaviour:

- Create the lock file with restricted permissions (`0o600`) when possible.
- Retry only contention errors (`EACCES`, `EAGAIN`, and `EWOULDBLOCK` on
  POSIX; equivalent transient lock errors on Windows).
- Raise `LockTimeoutError` when the timeout expires.
- Re-raise unrelated filesystem errors immediately.
- Release the lock in a `finally` block.

The POSIX backend can use the `fcntl.flock()` function. The Windows backend
can use an internal implementation around `msvcrt.locking` or a small
dependency such as `portalocker` if implementation proves that
standard-library locking is too limited. The public design requirement is the
timeout and error contract, not the backend mechanism.

`LockTimeoutError` should carry the lock path and timeout value. It may inherit
from `TimeoutError` so generic timeout handling still works, but callers that
need to distinguish lock contention from IPC timeouts can catch the CmdMox
exception directly.

## File-backed state store

`JsonStateStore` gives `.runs(...)` handlers a small persistence surface for
stateful fakes. A handler should not need to know the atomic-write or lock
protocol to model command state.

Proposed API shape:

```python
store = JsonStateStore(path, default_factory=dict)

def fake_uv(invocation):
    with store.transaction() as state:
        state["installed"]["ansible-core"] = "1.0.0"
    return Response()
```

The transaction context loads the current state under the lock, yields a
mutable object, and writes the new state only when the transaction marks itself
dirty. The simplest form can treat every successful transaction as dirty. A
later optimization can expose explicit `read()` and `update(updater)` methods
to avoid writing after read-only operations.

Required behaviour:

- Initialize missing state from `default_factory`.
- Validate that loaded state is JSON-compatible and report corrupted files with
  the path included in the error.
- Hold the lock across read, mutation, and write so updates do not race.
- Write through `atomic_write_json(...)`.
- Keep the state schema caller-owned. CmdMox should not impose a global schema
  on command-specific fake state.
- Support a path namespace that can be rooted in a test temporary directory or
  in CmdMox-managed replay state.

The first implementation should keep this helper outside the fixture replay
schema. Fixture files record observed command interactions; state-store files
represent mutable fake internals. Combining the formats would make replay
fixtures harder to review.

## Subcommand router helper

`SubcommandRouter` reduces boilerplate for argv-driven protocol simulators.
The helper should be opt-in and composable with `.runs(...)`.

Proposed API shape:

```python
router = SubcommandRouter()
router.command(("tool", "list"), list_tools)
router.command(("tool", "install"), install_tool, prefix=True)
router.command(("tool", "uninstall"), uninstall_tool, arity=3)

cmd_mox.stub("uv").runs(router)
```

Required behaviour:

- Match exact argv tuples by default.
- Optionally support prefix matches for commands where flags and operands
  follow a fixed subcommand prefix.
- Enforce `router.command(..., arity=N)` before handler dispatch when arity is
  set. The `uninstall_tool` example with `arity=3` requires exactly three argv
  elements; mismatches should produce the same deterministic route error path
  as other router validation failures.
- Produce deterministic errors for no match and ambiguous match.
- Pass the original `Invocation` to the selected handler.
- Return `Response` or the same tuple shape already accepted by `.runs(...)`.

This helper should not duplicate CmdMox expectation matching. It routes inside
a matched command double after the shim has already identified the executable.

## Filesystem side-effect helpers

Realistic fakes often create files as observable side effects. `fake-uv.py`
creates executable command shims after an install operation so later Molecule
steps can invoke `ansible-playbook`.[^1]

CmdMox can provide small helper functions rather than a side-effect framework:

- `write_text_file(path, content, mode=0o600, atomic=True)`
- `write_executable(path, content, mode=0o755, atomic=True)`
- `create_shim(path, target, mode=0o755)` for simple script shims

These helpers should use the same parent-directory creation and atomic-write
policy where possible. Executable helpers must set execute bits after the
content has been written and replaced. Cleanup remains the test's
responsibility unless the helper receives an explicit CmdMox-managed cleanup
registry in a later phase.

## Security and portability

The default file mode for JSON fixture and state writes is `0o600` because
fixtures and fake state may contain environment-derived values or command
outputs. Callers may pass a different mode when they intentionally want shared
fixtures, but restrictive permissions should be the default.

Windows cannot express POSIX mode bits exactly. The implementation should still
accept the `mode` parameter for API consistency, apply what the platform can
honour, and document any weaker guarantees. Tests should assert observable
permissions on POSIX and observable behaviour on Windows.

Expected Windows behaviour:

- `mode=0o600` is best-effort. The helper should avoid making files executable
  or world-writable, but it must document that it does not rewrite Access
  Control Lists (ACLs) to emulate POSIX owner-only permissions.
- Executable side-effect helpers should create `.cmd`, `.bat`, `.exe`, or
  extensionless files according to the caller's requested path. They should
  not treat POSIX execute bits as meaningful on Windows.
- Directory synchronization after `os.replace(...)` may be skipped when the
  platform cannot open or flush a directory handle through supported APIs.
  Skipping must be explicit in code and covered by a Windows-specific test or
  documented capability flag.
- The first Windows lock backend should either use `msvcrt.locking` with a
  documented lock-byte convention or use `portalocker`. The implementation
  must not silently downgrade to an unlocked state store.

Locks are advisory. CmdMox can coordinate cooperating writers, including its
own fixture and state helpers, but it cannot stop unrelated processes from
writing directly to the same file.

## Verification strategy

The persistence invariant is: after any failed write before replacement,
readers still observe the previous complete JSON file. After a successful
write, readers observe a complete JSON file equal to the serialized payload.

The state-store invariant is: concurrent transactions cannot lose successful
updates when all writers use the state-store API. This should be checked with
multi-threaded and multi-process tests that mutate disjoint keys in the same
state document.

The lock invariant is: timeout failures occur only after the configured
deadline while the lock is contended, and unrelated filesystem errors surface
without waiting for the deadline.

Router tests should cover exact matches, prefix matches, missing commands, and
ambiguous routes. Side-effect helper tests should cover executable bit
application, parent-directory creation, repeated writes, and cleanup by
existing CmdMox teardown utilities.

## Roadmap impact

The roadmap should add a phase after the native launcher work because these
helpers build on the existing record/replay and filesystem utility foundation.
The phase should prioritize:

1. Atomic JSON persistence and `FixtureFile.save()` adoption.
2. Lock-with-timeout utility.
3. File-backed state store for `.runs(...)` handlers.
4. Subcommand router examples and helper API.
5. Filesystem side-effect helpers.
6. Documentation updates and examples for package-manager-style fakes.

[^1]:
    `fake-uv.py` in `dev-env-rocky`,
    <https://raw.githubusercontent.com/leynos/dev-env-rocky/refs/heads/main/ansible/roles/uv_tools/molecule/rocky10/files/fake-uv.py>.

[^2]:
    Python `os.replace` and `os.fsync` documentation,
    <https://docs.python.org/3/library/os.html#os.replace>.

[^3]:
    `filelock` documentation, <https://py-filelock.readthedocs.io/>, and
    `portalocker`, <https://github.com/wolph/portalocker>.
