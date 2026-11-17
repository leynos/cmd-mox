# Bound request size and concurrency on the Unix IPC server

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: IN PROGRESS

Branch: `issue-410-bound-request-size-and-concurrency-on-the-unix-ipc-server`

Roadmap item: none.

## Purpose / big picture

CmdMox shims exchange requests with a local IPC server. The Windows named-pipe
transport already refuses a message larger than 8 MiB and serves at most 64
clients at once. The POSIX Unix-socket transport had neither bound: it read to
EOF without a cap and asked the threading mixin for one worker per accepted
connection. A single peer could therefore exhaust server memory or threads.

After this change, both transports share one message-size cap, one
active-client cap, one bounded EOF reader, and one permit type. A Unix request
larger than 8 MiB is refused before dispatch and reported as a
`read_size_rejected` connection event. A 65th concurrently held Unix connection
is closed without a response frame and reported as a `rejected` connection
event. Windows behaviour, events, and tests stay as they are.

## Constraints

- Keep the Windows transport's observable behaviour, event vocabulary, and
  existing tests unchanged.
- Keep `cmd_mox.ipc.windows.PipeMessageTooLargeError` importable, with the same
  message, `args`, `received`/`limit` fields and `vars()`.
- Keep `named_pipe.MAX_MESSAGE_SIZE`, `_named_pipe_limits.MAX_ACTIVE_CLIENTS`,
  `ClientSlot`, and `acquire_client_slot` importable for existing tests.
- Unix keeps its single non-renewing read deadline; Windows keeps its
  per-step cooperative timeouts.
- No payload bytes, exception messages, or socket paths may enter logs or
  counter keys.
- All Python files stay within the DF12 800-line review ceiling.

## Tolerances (exception triggers)

- Files: more than 12 non-ExecPlan files changed.
- Code: more than 700 net lines (additions minus deletions), excluding this
  ExecPlan and test-scaffolding duplication fixes.
- Documentation: more than 200 net lines, excluding this ExecPlan.
- Iterations: stop after three failed attempts to make one gate pass.
- Ambiguity: stop and escalate if a requirement contradicts the existing
  Windows contracts.

## Risks

- Risk: moving `ClientSlot` could break test monkeypatch bindings.
  Severity: medium Likelihood: low Mitigation: re-export from
  `_named_pipe_limits` and `windows.py`, then run the named-pipe suites.
- Risk: the 64-client saturation test is timing-sensitive.
  Severity: medium Likelihood: medium Mitigation: poll bounded white-box state
  with deadlines, close every client in `finally`, and use the short socket
  path fixture.
- Risk: the shared reader changes the client's chunk size from 1 KiB to 64 KiB.
  Severity: low Likelihood: high Mitigation: update the single chunk-size
  assertion while keeping every deadline assertion intact.

## Progress

- [x] (2026-11-17 00:00Z) Reconnaissance and ExecPlan drafted.
- [x] (2026-11-17 01:30Z) `EP-M1` shared limits module and bounded EOF
  reader implemented; focused tests pass.
- [x] (2026-11-17 03:10Z) `EP-M1` all six gates pass (`make check-fmt`,
  `make typecheck`, `make test`, `make lint`, `make markdownlint`,
  `make nixie`) and the milestone is committed.
- [ ] `EP-M2` Unix size rejection before dispatch with bounded event.
- [ ] `EP-M3` Unix admission gate, saturation tests, and guides.
- [ ] Full gate run and draft PR.

## Surprises & discoveries

- Observation: `_read_all` asserted a 1 KiB chunk in
  `tests/test_ipc_client_request_unit.py`. Evidence: grep of `recv_sizes`.
  Impact: that one assertion moves to the shared 64 KiB chunk while the
  deadline assertions remain unchanged.
- Observation: the repository caps function arity at four arguments, so the
  planned three keyword-only reader tunables would fail `PLR0913`. Evidence:
  `/tmp/lint-cmd-mox-issue-410.out` line 4690. Impact: the tunables moved into
  a frozen `ReadOptions` dataclass, mirroring `PipeReadOptions`;
  `read_until_eof` now takes `(sock, deadline, options)`.
- Observation: the DF12 pylint tier flags a test double's `settimeout` method
  as `R9104 trivial-attribute-wrapper` when its body only records the value.
  Evidence: `tests/test_ipc_read_until_eof.py:36`. Impact: the double now
  rejects a negative timeout, as a real socket does, which is both
  non-trivial and a more faithful stand-in.

## Decision log

- D1. Decision: put the shared primitives in `cmd_mox/ipc/_limits.py` and
  re-export them from the transport-specific modules. Rationale: it gives one
  platform-neutral home without breaking existing import paths; deleting the
  old names would churn tests for no benefit. Date/Author: 2026-11-17,
  implementing agent.
- D2. Decision: keep `PipeMessageTooLargeError` as a subclass whose message
  prefix comes from a class attribute on `MessageTooLargeError`. Rationale:
  preserves the Windows message exactly while sharing the constructor and
  fields. Date/Author: 2026-11-17, implementing agent.
- D3. Decision: request `min(chunk_size, max_bytes - received + 1)` bytes per
  `recv`, so the read at the limit becomes a one-byte EOF probe. Rationale:
  distinguishes "exactly at the limit then EOF" from "one byte over" without
  buffering the overflow. Date/Author: 2026-11-17, implementing agent.
- D4. Decision: pass the reader tunables as a frozen `ReadOptions` dataclass
  instead of three keyword-only parameters. Rationale: the lint gate caps a
  definition at four arguments, and the named-pipe transport already models its
  tunables as `PipeReadOptions`. Date/Author: 2026-11-17, implementing agent.
- D5. Decision: both option dataclasses inherit the validated bounds from a
  shared `ReadBounds` base in `_limits.py`, rather than each defining its own
  `__post_init__`. Rationale: two identical one-line `__post_init__` methods
  tripped the duplication gate after the first extraction, while a shared base
  keeps one validation path and one definition of the 64 KiB receive chunk.
  `PIPE_CHUNK_SIZE` is now an alias of `DEFAULT_READ_CHUNK_SIZE`. A bound that
  cannot describe a usable read is still rejected when options are built.
  Date/Author: 2026-11-17, implementing agent.

## Outcomes & retrospective

Pending.

## Context and orientation

- `cmd_mox/ipc/server.py` owns the Unix transport. `_IPCHandler` reads a
  request and dispatches it; `_inner_server_cls()` builds `_InnerServer`, which
  subclasses the platform's threaded `socketserver` class.
- `cmd_mox/ipc/client.py` owns the Unix client. `_read_all()` is its unbounded
  EOF loop.
- `cmd_mox/ipc/socket_utils.py` owns Unix socket helpers.
- `cmd_mox/ipc/windows.py` owns `MAX_MESSAGE_SIZE` and
  `PipeMessageTooLargeError`.
- `cmd_mox/ipc/_named_pipe_limits.py` owns `MAX_ACTIVE_CLIENTS`, `ClientSlot`,
  and `acquire_client_slot`, plus named-pipe-only worker vocabulary.
- `cmd_mox/ipc/_observability.py` owns `IPCEvent` and the closed counter
  categories.
- Relevant guides: `docs/python-native-command-mocking-design.md`,
  `docs/ipc-timeout-migration-note.md`, `docs/usage-guide.md`.

## Conformance basis

No Terms of Reference or technical design revision governs this change. The
authority is GitHub issue #410 (code-quality audit, 2026-11-10) plus the coding
plan supplied with the task. Requirements derived from it:

- `REQ-SIZE`: both transports cap one message at 8 MiB.
- `REQ-CONCURRENCY`: both transports cap active clients at 64.
- `REQ-READER`: Unix uses one shared bounded EOF reader.
- `REQ-EVENTS`: Unix emits `read_size_rejected` and `rejected` connection
  events with bounded categories only.
- `REQ-DOCS`: the guides describe the shared bounds and permit ownership.

Trace: `REQ-SIZE -> EP-M1 -> test_read_until_eof_*`,
`REQ-READER -> EP-M1 -> test_unix_client_*`,
`REQ-EVENTS -> EP-M2 -> test_unix_server_rejects_*`,
`REQ-CONCURRENCY -> EP-M3 -> test_unix_server_refuses_client_65_*`,
`REQ-DOCS -> EP-M3 -> docs diffs`.

## Verification plan

### Abstract model

A read is a sequence of `recv` calls against a deadline and a byte budget.
`received` starts at 0. Each call requests at most
`min(chunk, limit - received + 1)` bytes. A non-empty result adds its length to
`received`; the read fails once `received > limit`. An empty result means EOF
and succeeds. Server admission is a non-blocking semaphore with 64 permits; one
permit is attached to one accepted socket and returned exactly once.

### Invariants and lemmas

- `INV-SIZE`: no path buffers more than `limit + 1` bytes.
- `INV-EOF`: a payload of exactly `limit` bytes followed by EOF succeeds.
- `INV-PERMIT`: every registered socket releases exactly one permit.
- `INV-EVENT`: a size rejection emits one event carrying `message_size` and no
  correlation id; an admission refusal emits one `rejected` event.
- `L-DEADLINE`: each `recv` receives the remaining, non-renewed budget.

### Axioms (trusted, not verified here)

- `AXIOM-SERVER`: `socketserver.BaseServer._handle_request_noblock` calls
  `shutdown_request` on a `process_request` failure; `process_request_thread`
  calls it in `finally`. Repository code may rely on this documented shape but
  must still prove its own release path with a stand-in.
- `AXIOM-SOCKET`: `socket.recv` returns at most the requested bytes and returns
  `b""` only at EOF.

### Methods per obligation

- `INV-SIZE` / `INV-EOF`: parameterized unit tests over fake sockets in
  `tests/test_ipc_read_until_eof.py`, including exact-limit and one-over cases.
  Negative control: a one-byte-over run must raise.
- `L-DEADLINE`: fake socket records `settimeout`; the existing Unix server test
  asserts `[1.0, 0.75, 0.5]`.
- `INV-PERMIT`: deterministic `_InnerServer` tests calling `process_request`,
  `process_request_thread`, and `shutdown_request` with fake requests, plus a
  real-socket saturation test.
- `INV-EVENT`: fake-handler test plus a real-socket oversize test.

### Formal verification decision

Model checking and formal proof are not proportionate: the properties are
finite bounds and a single permit latch, already covered by deterministic unit
tests plus state inspection. The residual gap is kernel socket scheduling,
covered by the real-socket tests.

### Suggested BDD features

Not adopted here; the repository's pytest suite already carries the observable
behaviour, and adding a BDD scenario is not requested.

## Plan of work

`EP-M1`: add `_limits.py`; re-export from `windows.py` and
`_named_pipe_limits.py`; add `read_until_eof`; route client and server reads
through it; add tests.

`EP-M2`: extend Unix outcomes, categories, and `_emit_connection_outcome`; catch
`MessageTooLargeError` before dispatch; add counter tests and a bounded
real-socket oversize test.

`EP-M3`: gate `_InnerServer.process_request` with a `BoundedSemaphore`; release
permits in `shutdown_request`; add saturation and deterministic admission
tests; update the three guides.

## Concrete steps

Working directory: repository root.

```plaintext
Red:    uv run pytest <new test> -> expected assertion or missing symbol
Green:  uv run pytest <new test> -> passes
Gates:  make test; make lint; make check-fmt; make typecheck
Docs:   make markdownlint; make nixie
```

## Validation and acceptance

- `make test` passes, including the new oversize and saturation tests.
- `make lint`, `make check-fmt`, `make typecheck`, `make markdownlint`, and
  `make nixie` pass.
- A request over the limit is refused before the callback runs.
- Client 65 is closed with no frame while 64 are held; a client is admitted
  once one permit returns.

## Idempotence and recovery

Each milestone is an independent commit; revert it with `git revert` if a gate
regresses. Tests create their own sockets and clean them up in `finally`.

## Artefacts and notes

Pending.

## Interfaces and dependencies

- `cmd_mox.ipc._limits.MessageTooLargeError`, `MAX_MESSAGE_SIZE`,
  `MAX_ACTIVE_CLIENTS`, `ClientSlot`, `acquire_client_slot`.
- `cmd_mox.ipc.socket_utils.read_until_eof(sock, deadline, options)`, where
  `options` is a `ReadOptions(max_bytes, chunk_size=65536, remaining_time=...)`
  frozen dataclass that inherits its validated bounds from
  `cmd_mox.ipc._limits.ReadBounds`.
- No new dependencies.

## Revision note

- 2026-11-17: Initial draft derived from the supplied coding plan.
