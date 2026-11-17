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
- [x] (2026-11-17 05:40Z) `EP-M2` Unix size rejection before dispatch with a
  bounded event, approved counter categories, and focused tests.
- [x] (2026-11-17 06:20Z) `EP-M3` Unix admission gate, deterministic and
  saturation tests, and the three guide updates.
- [x] (2026-11-17) All six gates pass on the combined `EP-M2`/`EP-M3` tree
  (`make check-fmt`, `make typecheck`, `make test` with 2193 passed and 16
  skipped, `make lint` including both pylint tiers, `ambrleaks`, and spelling,
  `make markdownlint`, `make nixie`).
- [x] (2026-11-17 04:10Z) Draft PR
  [#479](https://github.com/leynos/cmd-mox/pull/479) opened from the combined
  tree, with the issue, the plan, and the Lody session references.
- [x] (2026-11-17 06:50Z) CodeRabbit reviewed both commits and returned five
  findings: unbounded fragment storage in `read_until_eof`, a plan paragraph
  that implied a refused connection could revive, vacuous permit-return
  assertions, a socket leak in the test helper, and an unsynchronized
  readiness-probe assumption. All five are actioned, and the two CodeScene
  code-health violations the PR check reported are fixed too.
- [x] (2026-11-17 08:20Z) Branch pushed, then replayed onto `origin/main` at
  `c426792` (see D9). The replay was conflict-free, `range-diff` shows all five
  commits unchanged, and `git diff --check` is clean.
- [x] (2026-11-17 09:10Z) All six gates pass at `9877f98` (`make check-fmt`,
  `make typecheck`, `make lint` including both pylint tiers, `make test` with
  2202 passed and 16 skipped, `make markdownlint`, `make nixie`), the branch is
  force-pushed with lease onto the rebased history, and PR #479's every check
  is green, including CodeScene Code Health with six quality gates passed.
- [x] (2026-11-17 10:05Z) Final CodeRabbit pass at `6c8a4f3` returned two
  findings, both actioned: the reader test now injects a decreasing budget so a
  resolve-once implementation cannot pass, and the blocking probe no longer
  carries a handler deadline that the burst setup could outlive.
- [x] (2026-11-17 11:00Z) All six gates pass at `6e00ee5`, and CodeRabbit
  reviewed that head with zero findings. PR #479's checks are green there too:
  `Linux QA`, `Windows smoke`, `build-dist`, CodeScene Code Health and Gecko
  Security all pass.

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
  rejects a negative timeout, as a real socket does, which is both non-trivial
  and a more faithful stand-in.
- Observation: one size rejection produces two INFO records: the bounded event
  and the fixed diagnostic. Evidence: the first fake-handler test draft
  unpacked both when expecting one. Impact: the assertion selects the event by
  its structured `outcome`, and separately asserts the diagnostic message.
- Observation: a burst of 65 Unix `connect` calls can exhaust the default
  listen backlog (five) before the accept loop catches up, surfacing
  `BlockingIOError` in the client. Evidence: the first saturation test run
  failed at client six. Impact: the saturation client retries `connect` until
  the deadline; every retry still meets the same admission gate.
- Observation: the backend fixture does not use `IPCServer.start()`, because
  that start performs a readiness-probe connection whose permit would race the
  deterministic permit assertions. Evidence: `tests/test_ipc_server_limits.py`
  constructs the cached inner class directly. Impact: only the saturation test
  needs the real start path, and it now waits for the probe's own connection
  event and then for an empty slot map before bursting, rather than assuming
  the probe's permit returns before the burst is admitted.
- Observation: an early draft of the saturation test assumed the readiness
  probe returns its permit before the burst completes. Evidence: `start()`
  returns as soon as `connect()` succeeds and never observes the server's
  admission. Impact: the probe's worker could still hold a permit when the 64th
  burst client was admitted, refusing it and stalling the wait for 64 entered
  callbacks. The test now observes the probe's `ipc.server.connection` event
  and a zero slot count before connecting any burst client.
- Observation: the delete-oriented permit tests were vacuous at the default
  allowance. Evidence: replacing `slot.release()` with `pass` and running the
  five permit tests against a 64-permit backend passed all five, because the
  empty slot map and 63 free permits satisfied both assertions. Impact: each
  permit test now installs `BoundedSemaphore(1)`, under which the same mutant
  fails all six. Keeping the empty-map assertion still checks the bookkeeping
  half.
- Observation: `make check-fmt` runs `ruff format --check` only, so it cannot
  see import order. Evidence: the first gate run was green on format and red on
  `ruff check`'s `unsorted-imports` for the same file. Impact: treat
  `make lint` as the only authority on import ordering, and run it before
  claiming a file is clean.
- Observation: both pylint tiers object to otherwise ordinary thread
  primitives. Evidence: `R1732 consider-using-with` fired on
  `Semaphore.acquire`, and the DF12 tier's `R9104 trivial-attribute-wrapper`
  fired on the fake request's one-line `shutdown`. Impact: the saturation test
  now releases one waiter per queued token from a `queue.Queue`, which cannot
  be mistaken for a context manager, and the request double validates the
  shutdown mode as a real socket does instead of only recording it.
- Observation: CodeScene's advisory check rejected a five-argument emitter and
  an oversized test body. Evidence: the PR check flagged "Excess Number of
  Function Arguments" and "Large Method". Impact: `_emit_connection_outcome`
  now takes one frozen `_ConnectionReport`, so each call site names only the
  dimensions its outcome carries, and the oversized-request test is now five
  small helpers around a short body.
- Observation: a fragment-per-`recv` list bounds payload bytes but not the
  number of retained objects. Evidence: a peer dribbling one byte per receive
  could retain nearly `MAX_MESSAGE_SIZE` list entries, roughly 40 bytes each,
  per admitted connection. Impact: `read_until_eof` accumulates into one
  `bytearray` and materializes the payload only at EOF, so retained storage
  tracks the byte budget instead of the receive count.
- Observation: `_connect_and_request` closed its socket only on success, and a
  refused peer can reset before the client's own write completes. Evidence: the
  refused client is appended to the cleanup list only after the helper returns.
  Impact: the helper closes on a failed connect, tolerates a write failure only
  when the caller passes `expect_rejection=True`, and no longer leaks a socket
  on any failure path.
- Observation: CodeScene's Large Method marker applies at 70 lines, and the
  saturation test reached 78 once the readiness-probe waits and the refusal
  assertions were added. Evidence: the PR check at `a8b7d7f`, which failed with
  "has 78 lines, threshold = 70" while every other check passed. Impact: the
  blocking handlers moved into a `_BlockingProbe` double with `release_one`,
  `stop_blocking`, and `release_all`, and the probe wait, client burst, and
  rejection assertion became helpers, leaving a 52-line body with unchanged
  assertions.
- Observation: the DF12 pylint tier rejects a test helper whose whole body is
  `Event.set()` as `R9104 trivial-attribute-wrapper`. Evidence: `make lint` at
  `991514c`, and the same failure in CI's `Linux QA` job. Impact:
  `stop_blocking` now refuses a second call, which is the invariant the release
  accounting depends on, and `test_blocking_probe_refuses_a_second_stop` covers
  that refusal so the guard leaves no uncovered line for the 100% coverage gate.
- Observation: a constant remaining-time double cannot distinguish recomputing
  the budget before every receive from resolving it once. Evidence: hoisting
  `options.remaining_time(deadline)` above the read loop left every existing
  assertion green until the budget was made to shrink per call. Impact: the
  reader test injects `2.5`, `1.5`, `0.5`, asserts each receive receives the
  corresponding value, and asserts every call received the same deadline; the
  resolve-once mutant now fails both that test and the timeout-propagation test.
- Observation: a blocked handler with its own 10 s deadline is a flake source
  even though the deadline matches the test's own wait bound. Evidence: the
  earliest of 64 callbacks starts waiting while the remaining clients still
  connect, so a slow burst can expire it and `pytest.fail` unwinds the worker
  before the test releases the permit it is asserting on. Impact: the handler
  blocks until an explicit release with no timeout of its own; the driving test
  owns the deadline, and its `finally` releases every callback, so a failed
  wait still unwinds cleanly.

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
- D6. Decision: acquire the Unix permit in `process_request` and return it in
  `shutdown_request`'s `finally`, never in `stop()` or `server_close()`.
  Rationale: `socketserver` calls `shutdown_request` on every exit path,
  including an early worker finish and a handler failure, while a worker that
  outlives the listener still owns its permit until its own socket closes.
  `ClientSlot.release` latches, so the handler-failure path cannot release
  twice. Date/Author: 2026-11-17, implementing agent.
- D7. Decision: deliver `EP-M2` and `EP-M3` in one commit. Rationale: both
  change the same handler and inner-server surface, and the admission tests
  share `tests/test_ipc_server_limits.py` with the size-rejection test, so
  splitting them would produce a misleading history rather than independent
  reverts. Date/Author: 2026-11-17, implementing agent.
- D8. Decision: prove the permit paths with a direct inner-backend fixture and
  an inline `Thread` double, and prove real concurrency with one saturation
  test over real sockets. Rationale: deterministic stand-ins give exact release
  counts without timing assumptions, and the real-socket test covers kernel
  acceptance, refusal, and re-admission together. Date/Author: 2026-11-17,
  implementing agent.
- D9. Decision: replay the branch onto `origin/main` at `c426792` ("Measure
  `.record()` passthrough duration") without adopting any of its changes into
  this work. Rationale: the incoming commit measures recording durations in the
  passthrough coordinator, which shares this branch's `duration_ms` vocabulary
  but neither the IPC connection events nor any code path this branch touches.
  It adds `docs/recording-duration-migration-note.md` and edits two guides this
  branch also edits; those hunks land in unrelated sections and the replay
  merged them without conflict. The five branch commits replay unchanged under
  `range-diff`, and the new connection-event duration stays measured by the
  existing `_observability.elapsed_ms`, so no shared helper moved. Date/Author:
  2026-11-17, implementing agent.

## Outcomes & retrospective

The Unix transport now enforces the same resource bounds as the named-pipe
transport. A single message may carry at most 8 MiB, and at most 64 clients are
served at once. Both numbers, the `ClientSlot` latch, `acquire_client_slot`, and
`MessageTooLargeError` live in `cmd_mox.ipc._limits`, which the client, the
server, and both named-pipe modules import, so the two transports cannot drift
apart. `socket_utils.read_until_eof` replaced the client's `_read_all` and the
server's read loop, so one implementation owns the budget, the EOF probe, and
the deadline arithmetic.

A request that crosses the size limit is abandoned before dispatch. The reader
drops its buffered prefix, `_IPCHandler.handle` emits one `read_size_rejected`
connection event carrying the observed byte count and elapsed duration, logs a
fixed diagnostic, and returns without a response, so the client sees EOF rather
than a misleading error frame. A connection that arrives while 64 are in flight
is closed with no frame and recorded as `rejected` with `ClientLimitReached`.
That refusal is final for the connection: releasing a permit does not revive a
socket the server has already closed, so recovery is a later connection
attempt, which is admitted once a permit returns. Every admitted socket returns
exactly one permit, whichever of the accept loop, an early-finishing worker, a
failing handler, or a failing close ends it.

The behaviour is observable from outside: a caller that sends an oversized
request receives no response and a controlled `IPC error: ...` diagnostic; a
65th concurrent shim is closed immediately without a response frame, and only a
later connection attempt can be admitted after a permit returns. Evidence: the
nine tests in `tests/test_ipc_server_limits.py`, the size-rejection regression
in `cmd_mox/unittests/test_ipc_server_connection_events.py`, the counter-key
tests in `tests/test_ipc_observability_seam.py`, and a full suite of 2193
passed with 16 skipped.

Lessons. First, `socketserver` already routes every exit path through
`shutdown_request`, so permit ownership belongs there and in `process_request`,
never in `stop()` or `server_close()`; a worker that outlives the listener
keeps its permit until its own socket closes, and a fresh semaphore per backend
stops one server instance inheriting another's permits. Second, a real-socket
saturation test needs two concessions the deterministic tests do not: the
client must retry `connect` while the listen backlog is full, and the test must
wait for all 64 callbacks to start before expecting a refusal, because the
server's readiness probe briefly holds a permit of its own. Third,
`make check-fmt` runs `ruff format --check` only, so import order is caught by
`make lint` alone; a green format gate is not evidence for it.

Residual gaps. The Windows transport keeps its per-step cooperative timeouts,
so the POSIX deadline policy described here does not apply there; the shared
bounds only make the two transports agree on size and concurrency.
`docs/python-native-command-mocking-design.md` appears in the markdownlint
configuration's exclusion list, so that changed guide is validated by the
spelling gate and `make nixie` rather than by markdownlint.

## Context and orientation

- `cmd_mox/ipc/server.py` owns the Unix transport. `_IPCHandler` reads a
  request and dispatches it; `_inner_server_cls()` builds `_InnerServer`, which
  subclasses the platform's threaded `socketserver` class.
- `cmd_mox/ipc/client.py` owns the Unix client. `_read_all()` is its unbounded
  EOF loop.
- `cmd_mox/ipc/socket_utils.py` owns Unix socket helpers.
- `cmd_mox/ipc/_limits.py` owns `MAX_MESSAGE_SIZE`, `MAX_ACTIVE_CLIENTS`,
  `DEFAULT_READ_CHUNK_SIZE`, `ReadBounds`, `ClientSlot`, `acquire_client_slot`,
  and `MessageTooLargeError`.
- `cmd_mox/ipc/windows.py` re-exports the shared size names and keeps
  `PipeMessageTooLargeError` as a `MessageTooLargeError` subclass.
- `cmd_mox/ipc/_named_pipe_limits.py` re-exports the shared admission names,
  and owns the named-pipe-only worker vocabulary and client lifetime.
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
- `INV-PERMIT`: deterministic `_InnerServer` tests calling `process_request` and
  `shutdown_request` with fake requests. `process_request_thread` is exercised
  through the inline `Thread` double that runs the target it is handed, so the
  worker body, its `finally`, and the failure path all run synchronously. In
  addition, a real-socket saturation test covers the same invariant against the
  kernel's accept loop.
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
- Client 65 is closed with no frame while 64 are held; the closed connection
  stays refused, and the next connection attempt is admitted once one permit
  returns.

## Idempotence and recovery

Each milestone is an independent commit; revert it with `git revert` if a gate
regresses. Tests create their own sockets and clean them up in `finally`.

## Artefacts and notes

Commits on
`issue-410-bound-request-size-and-concurrency-on-the-unix-ipc-server`:

- `6794dda` — `EP-M1`: shared limits module, bounded EOF reader, and the reads
  routed through it.
- The `EP-M2`/`EP-M3` commit that follows covers the Unix size rejection, the
  admission gate, the tests, and the three guide updates.

Test artefacts, all runnable on their own:

- `tests/test_ipc_server_limits.py` — nine tests: real-socket oversize
  rejection, admission refusal, both thread-start-failure paths, early worker
  completion, handler failure, failing close, repeated cleanup, and the
  real-socket saturation test.
- `cmd_mox/unittests/test_ipc_server_connection_events.py` —
  `test_ipc_handler_rejects_an_oversized_request`.
- `tests/test_ipc_observability_seam.py` —
  `test_counter_key_preserves_fixed_unix_connection_error_categories` and
  `test_message_size_is_logged_but_never_keyed`.

Gate evidence is captured under `/tmp` as
`<gate>-2bb17d7a-1373-4f71-a079-92dcd17ca91b-issue-410-bound-request-size-and-concurrency-on-the-unix-ipc-server.out`
for `check-fmt`, `typecheck`, `test`, `lint`, `markdownlint`, and `nixie`.

## Interfaces and dependencies

- `cmd_mox.ipc._limits.MessageTooLargeError`, `MAX_MESSAGE_SIZE`,
  `MAX_ACTIVE_CLIENTS`, `ClientSlot`, `acquire_client_slot`.
- `cmd_mox.ipc.socket_utils.read_until_eof(sock, deadline, options)`, where
  `options` is a `ReadOptions(max_bytes, chunk_size=65536, remaining_time=...)`
  frozen dataclass that inherits its validated bounds from
  `cmd_mox.ipc._limits.ReadBounds`.
- `_InnerServer.process_request(request, client_address)` and
  `_InnerServer.shutdown_request(request)` in `cmd_mox/ipc/server.py`, which
  own the admission permit around the superclass calls.
- Unix connection events under `ipc.server.connection` with outcomes
  `receive_timeout`, `receive_closed`, `response_dropped`,
  `read_size_rejected`, and `rejected`, and the bounded categories
  `MessageTooLargeError`, `ClientLimitReached`, and `ThreadStartFailed`.
- No new dependencies.

## Revision note

- 2026-11-17: Initial draft derived from the supplied coding plan.
- 2026-11-17: Rebased onto `origin/main` `c426792`; recorded D9 and the
  conflict-free replay in Progress.
- 2026-11-17: Actioned the final CodeRabbit findings on deadline
  recalculation and the per-handler timeout; recorded both in Surprises and
  Progress.
