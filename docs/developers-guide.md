# Developer guide

This guide documents the local development checks for CmdMox. It is the source
of truth for running lint, understanding the current lint baseline, and knowing
where lint policy is configured.

## Pytest fixture lifecycle

The pytest plugin records failures at both the test-call and fixture
boundaries. Its `pytest_runtest_call` hook wrapper stores call-stage
`KeyboardInterrupt` and `SystemExit` on the test item before pytest finalizes
yield fixtures. This hook is needed because pytest can resume a fixture
generator normally while propagating those interruptions from the test body.

The `cmd_mox` fixture also marks `body_failed` when a `BaseException` escapes
`manager.enter()` or is thrown back through the fixture's `yield`, then
immediately re-raises the same exception. This records setup and fixture
failures without consuming interrupts or worker exits.

During teardown, `_CmdMoxManager.exit()` combines `body_failed` with the
call-stage state recorded by the hook. It suppresses a verification error only
when a body or call-stage failure already exists and controller cleanup
succeeds, preserving the original failure. Cleanup errors remain visible as
teardown failures.

## Coverage ownership

Pull-request CI generates coverage with the ratchet (`with-ratchet: 'true'`)
against the baseline that `coverage-main.yml` writes on pushes to `main`, and
publishes no coverage artefact (`publish-artefact: 'false'`). Nothing a pull
request runs invokes CodeScene, runs `cs-coverage`, receives `CS_ACCESS_TOKEN`,
or names the CodeScene host.

`coverage-main.yml` is the single publisher. It runs on pushes to `main`,
reports whether `CS_ACCESS_TOKEN` is set from a `codescene-token` check step
that binds nothing, passes the secret to the upload action as `access-token`
(never in any `env`, which the composite action hands to its nested steps),
guards the upload on exactly
`steps.codescene-token.outputs.available == 'true' && github.ref == 'refs/heads/main'`,
uploads with `mode: upload`, and declares a concurrency group keyed on the ref
alone that never cancels: GitHub keeps one pending run per group, so triggered
runs (push and dispatch) never overlap and the newest one's coverage lands
last. A manual re-run of an older run is an operator action that republishes
that commit's coverage and baseline until the next push supersedes it. The
uploader pins the CodeScene CLI through its own manifest, so no checksum input
or `CODESCENE_CLI_SHA256` variable is used.

Dependabot automerge merges are made with `GITHUB_TOKEN`, which fires no push
workflow, so they are a known exception: their coverage is published by the
next push to `main`. A dispatch that replaces a pending push leaves the ratchet
baseline one commit behind until the next push, because only a push saves it.
Both are tracked as issue 518 in leynos/shared-actions.

The reason is the call, not the artefact: the CLI talks to CodeScene's API,
whose answers have changed shape and failed every pull request at once, and a
fork cannot read the token anyway. The ratchet applies the same gate from this
repository's own baseline.

### Workflow contracts

`make test-workflow-contracts`, a prerequisite of `make test`, holds the
CodeScene coverage shape over this repository's workflows and local actions by
running `cv005-contracts check`, the shared contract library in
`leynos/shared-actions`, from the full commit named by `CV005_CONTRACTS_REF` in
the Makefile; a fix to the rules is a pin bump. The library reads workflows
with a loader that refuses duplicate keys, follows the pull-request surface
through local `./` and `$/` calls and composite actions, and drives every
clause against breaching fixtures in its own suite, so this repository keeps no
copy of the readers. The repository's parameters are in `.github/cv005.toml`:
its one `[[pairing]]` declares that the pull-request coverage step, which the
Windows smoke leg of the `quality` matrix skips, measures what the publisher
measures, and its `[selection]` pins the publisher's exact inputs, so a change
made to the generators and the uploader together is still a reviewed change.

## Linting

CmdMox uses a five-tier linting pipeline. Run it with:

```bash
make lint
```

The `lint` target first builds the development environment through
`make build`, and provisions the pinned duplication detector through
`make install-nose`. It then runs the five lint tiers in order:

1. `ruff check`
2. Pylint on uv-managed PyPy 3.12
3. CPython 3.14 Pylint with `df12_python_lints`, followed by `ambrleaks`
4. a blocking Skylos dead-code scan of the production package
5. the blocking nose code-duplication gate

Ruff is the fast first tier. It enforces import order, pycodestyle and Pyflakes
rules, pathlib usage, docstring rules, pytest rules, selected Ruff preview
rules, and a broad set of code-health checks imported from Episodic.

Pylint is the slower second tier. It runs after Ruff because it catches
different classes of problems, especially logging format mistakes, pattern
matching issues, selected refactoring hints, environment and subprocess
footguns, and module or function shape limits. The Pylint tier is intentionally
focused: `pyproject.toml` disables all Pylint messages by default and then
enables only the selected messages that complement Ruff.

The DF12 tier is isolated from the project environment and uses uv-managed
CPython 3.14. Its separate `pylintrc-df12.toml` enables every DF12 checker
against CmdMox's supported Python 3.12 source baseline. `ambrleaks` then scans
the test snapshot area for unredacted values.

Skylos is the fourth tier. It is separately provisioned at an exact release,
scans `cmd_mox` without treating test references as live callers, and fails the
local gate and Linux CI when it reports unexplained dead code. The scan uses
only local static analysis: uploads, provenance collection, and grep
verification are disabled.

The nose duplication gate is the final tier. It runs the pinned `nose-cli`
detector over the production package and fails when it finds a clone family no
reasoned exception covers. See [Code-duplication gate](#code-duplication-gate)
below.

## Makefile lint variables

The `Makefile` exposes the lint runner through variables so developers and
Continuous Integration (CI) jobs can override the runtime without editing
project files.

| Variable                      | Default                                                                                                                                        | Purpose                                                                           |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `RUFF_VERSION`                | `0.16.4`                                                                                                                                       | Pins the Ruff release used by lint and format targets.                            |
| `RUFF`                        | `$(UV_GATE) tool --from ruff@$(RUFF_VERSION) -- ruff`                                                                                          | Runs the pinned Ruff command via the vendored helper's tool channel.              |
| `TY_VERSION`                  | `0.0.74`                                                                                                                                       | Pins the `ty` release used by the typecheck target.                               |
| `TY`                          | `$(UV_GATE) tool --from ty@$(TY_VERSION) -- ty`                                                                                                | Runs the pinned `ty`; `--python .venv` points it at the project environment.      |
| `TYPOS_VERSION`               | `1.48.0`                                                                                                                                       | Pins the `typos` release used by the spelling target.                             |
| `PYLINT_PYTHON`               | `pypy@3.12`                                                                                                                                    | Selects the Python interpreter used by `uv tool run` for Pylint.                  |
| `PYLINT_TARGETS`              | `cmd_mox conftest.py examples tests`                                                                                                           | Lists the directories and files linted by Pylint.                                 |
| `PYLINT_VERSION`              | `4.0.9`                                                                                                                                        | Pins the Pylint release run by the second tier.                                   |
| `PYLINT_BASELINE_DISABLE`     | Existing cmd-mox baseline                                                                                                                      | Temporarily disables legacy Pylint findings while keeping the second tier active. |
| `PYLINT`                      | `$(UV_GATE) tool --managed-python --python $(PYLINT_PYTHON) --from 'pylint==$(PYLINT_VERSION)' -- pylint --disable=$(PYLINT_BASELINE_DISABLE)` | Builds the full PyPy Pylint command.                                              |
| `DF12_PYTHON`                 | `3.14`                                                                                                                                         | Selects CPython for the isolated DF12 tooling tier.                               |
| `DF12_PYTHON_LINTS_REF`       | `4cf41736cce2f7ba2778882a5c629c044568a0e5`                                                                                                     | Pins the immutable DF12 lint and `ambrleaks` revision (the `v0.3.0` tag commit).  |
| `DF12_PYTHON_LINTS`           | `git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)`                                                                 | Identifies the common source for the DF12 Pylint plugin and `ambrleaks`.          |
| `DF12_PYLINT`                 | uv-isolated Pylint under CPython 3.14                                                                                                          | Runs the enabled DF12 checker set with `pylintrc-df12.toml`.                      |
| `AMBRLEAKS`                   | uv-isolated `ambrleaks` under CPython 3.14                                                                                                     | Scans tracked test snapshot files for unredacted values.                          |
| `SKYLOS_VERSION`              | `4.33.2`                                                                                                                                       | Pins the separately provisioned dead-code analyser.                               |
| `SKYLOS_CLI`                  | `$(UV_GATE) tool --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' -- skylos`                                                                   | Command-only CLI; Python 3.14 supplies Skylos's source AST runtime.               |
| `SKYLOS`                      | `$(SKYLOS_CLI) --config-file pyproject.toml`                                                                                                   | Adds scan-only global options for the blocking lint target.                       |
| `SKYLOS_PRODUCTION_TARGETS`   | `cmd_mox`                                                                                                                                      | Limits dead-code liveness analysis to production sources.                         |
| `SKYLOS_EXCLUDE_FOLDERS`      | `tests cmd_mox/unittests`                                                                                                                      | Excludes both test paths from the production liveness graph.                      |
| `SKYLOS_EXCLUDE_ARGS`         | `$(foreach folder,$(SKYLOS_EXCLUDE_FOLDERS),--exclude $(folder))`                                                                              | Expands each excluded folder to its own repeatable `--exclude` flag.              |
| `SKYLOS_WHITELIST_LOCK`       | `.skylos-whitelist.lock`                                                                                                                       | Repository-local lock serializing documented whitelist updates.                   |
| `NOSE_VERSION`                | `0.20.0`                                                                                                                                       | Pins the duplication detector installed and run by the duplication targets.       |
| `NOSE_TOOLS_DIR`              | `.tools/nose`                                                                                                                                  | Repository-local install directory for the detector binary.                       |
| `NOSE_BIN`                    | `$(NOSE_TOOLS_DIR)/nose`                                                                                                                       | Detector binary path; a relative value resolves against the repository root.      |
| `CARGO_BINSTALL`              | `cargo-binstall`                                                                                                                               | Installer used to fetch the pinned prebuilt detector release.                     |
| `DUPLICATION_PYTHON`          | `3.14`                                                                                                                                         | Selects the gate tool environment interpreter; matches the script's Python floor. |
| `CYCLOPTS_VERSION`            | `4.25.2`                                                                                                                                       | Pins cyclopts for the gate environment; matches the script's PEP 723 block.       |
| `TOMLKIT_VERSION`             | `0.15.1`                                                                                                                                       | Pins tomlkit for the gate environment; matches the script's PEP 723 block.        |
| `DUPLICATION_GATE`            | `NOSE_BIN=$(NOSE_BIN) $(UV_GATE) tool --from 'python@$(DUPLICATION_PYTHON)' --with ... -- python scripts/duplication_gate.py`                  | Builds the gate command through the vendored helper's pinned-tool channel.        |
| `DUPLICATION_TEST_PYTHON`     | `$(DUPLICATION_PYTHON)`                                                                                                                        | Selects the interpreter for the focused duplication-gate helper tests.            |
| `DUPLICATION_TEST_PYTEST`     | `9.0.2`                                                                                                                                        | Pins pytest for the isolated helper-test environment.                             |
| `DUPLICATION_TEST_HYPOTHESIS` | `6.165.6`                                                                                                                                      | Pins hypothesis for the isolated helper-test environment.                         |

_Table 1: Makefile variables for the lint pipeline._

`tests/test_toolchain_contract.py` enforces that the Makefile's pinned
`RUFF_VERSION` and `TY_VERSION` match the versions used in Continuous
Integration (CI), so the two never drift apart silently. It also enforces that
`NOSE_VERSION`, the CI job's `NOSE_VERSION` environment variable and
`[tool.nose].version` in `pyproject.toml` all agree, since a drift there
produces a cache key that can never hit or a detector the gate rejects at run
time.

Override variables on the command line when a local investigation needs a
different target set or interpreter:

```bash
make lint PYLINT_TARGETS=cmd_mox/ipc PYLINT_PYTHON=pypy@3.12
```

Do not bypass `make lint` for normal validation. Running the target keeps the
Ruff, PyPy Pylint, DF12 Pylint, snapshot-leak, Skylos, duplication and spelling
gates ordered consistently with CI and preserves the shared `uv` cache
configuration.

## Snapshot testing

The development dependency group includes
[syrupy](https://github.com/syrupy-project/syrupy), which provides the
`snapshot` fixture used by the IPC model and server callback tests. These tests
compare serialized payloads with the tracked `.ambr` files in
`tests/__snapshots__`.

Run the snapshot tests with the normal test command:

```bash
uv run pytest tests/test_ipc_models_unit.py tests/test_ipc_server_callbacks.py
```

When an intentional payload change requires new expected output, update the
snapshots explicitly and review the resulting `.ambr` diff:

```bash
uv run pytest tests/test_ipc_models_unit.py tests/test_ipc_server_callbacks.py \
  --snapshot-update
```

Run the focused tests again without `--snapshot-update` before committing so
that the committed snapshots are verified rather than rewritten.

## Property-based testing

The development dependency group includes
[Hypothesis](https://hypothesis.readthedocs.io/) (`hypothesis>=6`), which
generates inputs for property-based tests: rather than asserting a fixed table
of examples, a property states an invariant that must hold across a broad input
space.

`cmd_mox/unittests/test_command_double_matches.py` uses it to exercise
`CommandDouble.matches`, generating command-name pairs to check that a double
never matches an invocation for a different command.

Run the affected test module with the normal test command:

```bash
uv run pytest cmd_mox/unittests/test_command_double_matches.py
```

Hypothesis complements the snapshot tests above rather than replacing them:
snapshots pin exact serialized output, while properties assert relations that
should hold for every generated input.

## IPC request dispatch

The shared IPC request pipeline keeps transport handling separate from request
dispatch:

- `_REQUEST_HANDLERS` maps each wire `kind` to its validator and the public
  handler-method name.
- `_request_pipeline` parses and validates a request before dispatching it.
- `_execute_request` resolves and calls `handle_invocation` or
  `handle_passthrough_result` on the actual server instance.
- This virtual dispatch is intentional. Do not replace it with fixed
  module-level processors that bypass subclass overrides.
- Direct `_request_pipeline` tests cover parsing, validation, and response
  encoding.
- Socket-level tests cover transport-to-hook dispatch.
- Each request emits exactly one bounded dispatch record with `operation`,
  `kind`, `outcome`, `duration_ms`, an `invocation_id` only for passthrough
  results, and an `error_category` only on failure. Payloads, arguments,
  standard streams, environments, socket paths, and exception messages must
  never be logged.
- Outcomes are `success`, `invalid_request` for validation failures,
  `rejection_frame` for parse failures, and `handler_error`. Parse failures
  return a bounded error response and use `RequestParseError` as their error
  category; validation failures keep the `invalid_request` outcome and use
  `ValidationError`. An empty read is treated as a closed connection, so
  readiness probes emit neither a dispatch record nor a response frame.
- Unix connection failures emit a bounded `ipc.server.connection` event with
  one of three outcomes: `receive_timeout`, `receive_closed`, or
  `response_dropped`. Its `error_category` is one of `ConnectionClosed`,
  `ConnectionError`, `OSError`, or `TimeoutError`; exception messages are not
  included. The event carries a nonnegative `duration_ms` and includes
  `correlation_id` only when a validated request identifier is available.
  Connection counters use only the fixed `operation`, `transport`, and
  `outcome` dimensions, so correlation identifiers and payload data never
  increase the number of counter keys.

The private deadline helpers accept an optional monotonic clock callable and use
`time.monotonic` when runtime callers omit it. The Unix request handler binds
its clock once and reuses it for each receive-budget check. Tests can pass a
fake clock directly without replacing the process-wide time function.

## IPC deadlines and stdin reads

On POSIX, the shim carries one monotonic deadline through Unix socket
connection retries, request sending, response reads, and passthrough reporting.
Stdin capture uses that deadline when the stream can be waited on. The client
stores the deadline in `_ConnectionContext` and applies only the remaining time
before each blocking socket operation. The Unix server also keeps one read
deadline per accepted connection and updates the socket timeout before each
receive, so a client that slowly streams a request cannot renew its full wait
budget with every byte.

Stdin capture is bounded by `CMOX_IPC_TIMEOUT` on every platform and for every
stream kind, and all of that logic lives in `cmd_mox._stdin_capture`. Limiting
the component to one deadline and one error type keeps strategy selection,
decoding, and reader-resource ownership out of `cmd_mox.shim`, which only turns
a failure into a diagnostic. The strategies are:

- a zero-timeout readiness probe on a POSIX descriptor, using `select.poll`
  first and `select.select` second, so an unpollable descriptor is detected
  before any bytes are read;
- chunked `os.read` against the remaining budget for pollable descriptors, and
  the same reads on a daemon worker for regular files, which cannot be waited
  on;
- bounded `ReadFile` chunks on a daemon worker for a resolved Windows handle,
  with `CancelSynchronousIo` followed by a bounded join when the deadline
  expires;
- the stream's own `read()` on a tracked daemon worker when there is no usable
  descriptor or handle.

A stream that never reaches EOF leaves its reader blocked inside the stream.
That thread cannot be joined, because only the stream's owner can end the read,
so a finished worker is pruned from the registry and a live one stays in it.
`os._exit()` must therefore stay out of `main()`: an in-process caller that can
reach `main()` shares the process with other tests and code, and terminating it
would be a hostile side effect. Instead `main()` raises `SystemExit`, and the
script entry point `_run_as_script()` inspects `outstanding_workers()` and calls
`os._exit()` with the same status only when it finds one. Normal exits re-raise
`SystemExit` and keep the ordinary interpreter shutdown, which flushes streams
and runs `atexit` handlers. The wrapper maps a non-integer status, such as the
message string `sys.exit("fatal")` carries, to `1` rather than reporting it as
success.

`tests/test_shim_stdin_timeout.py` starts the generated launcher as a child
process with an open stdin writer, so it is the only test that observes the
process-lifetime half of this contract. It is listed in `WINDOWS_SMOKE_ARGS`
because the Windows `.cmd` launcher path, the native handle strategy, and the
launcher's exit-status forwarding can only be exercised there; the Unix
capability marker is deliberately not applied so the cross-platform cases run
on both hosts.

The shim emits bounded IPC observability events when stdin reading or writing
and flushing a response stream fails. These use the existing dependency-free
observability seam and record fixed operation, outcome, and error-category
values, elapsed duration, and a correlation identifier when one is available.
They do not include exception messages or stream contents. If the deadline
expires after stdin capture but before initial dispatch, the shim emits one
`ipc.shim.ipc_request` event with outcome `deadline_expired` and does not
dispatch the invocation. Its elapsed duration measures the dispatch budget
check. If the original deadline expires while the real command runs, the shim
emits an `ipc.shim.passthrough_report` event with outcome `deadline_expired`
and does not dispatch a report. Its elapsed duration includes the real
command's runtime. Failures after either dispatch are covered by
`ipc.client.request` events, without a duplicate shim event. Unix server
response writes use `ipc.server.connection` events. The seam writes structured
logging records and keeps in-process counters; it does not export to an
external collector.

`CommandDouble.matches` must reject an `Invocation` whose `command` differs from
`CommandDouble.name`. It performs that command-name check before expectation
matching and must not invoke expectation matching for a different command. This
prevents a double from accepting an invocation owned by another command.

### Unix server-base selection

`IPCServer` defers Unix server-class selection until backend creation. Its
cached `_inner_server_cls()` factory asks the cached
`resolve_unix_server_base()` accessor for the platform base class, then creates
the inner server class. The resolver prefers
`socketserver.ThreadingUnixStreamServer`; when only `UnixStreamServer` exists,
it composes that class with `ThreadingMixIn`. On Windows the resolver returns a
placeholder whose construction raises `RuntimeError`, while a platform with
neither Unix server class raises `RuntimeError` during resolution. Both caches
retain a successful result for the process lifetime. A resolution failure is
not cached, so a later backend start retries resolution. Direct resolver calls
have no observability side effects. The server's backend-creation boundary
emits bounded `ipc.backend_selection` events for native or compatibility
selection, cache hits and misses, unsupported platforms, Windows rejection, and
backend-construction failures. Events use fixed selection and error categories;
they never include socket paths or exception messages.

### Callback server construction

`_CallbackServerBase` in `cmd_mox/ipc/_server_core.py` owns the constructor
signature shared by the two callback-driven transports: positional
`socket_path`, `handler` and `passthrough_handler`, plus a keyword-only
`timeouts`. `CallbackIPCServer` (`cmd_mox/ipc/server.py`) and
`CallbackNamedPipeServer` (`cmd_mox/ipc/named_pipe.py`) each inherit from it
and add nothing of their own; neither class defines `__init__`, so both resolve
to `_CallbackServerBase.__init__`.

`_callback_wiring`, in the same module, pairs the two callbacks into an
`IPCHandlers` and flattens a `TimeoutConfig` into the `timeout` and
`accept_timeout` arguments, defaulting to `TimeoutConfig()` when no
configuration is supplied.

The inherited constructor calls `super().__init__(...)`, so the next class in
each transport's method resolution order receives the base-server arguments. For
`CallbackIPCServer` that is `_BaseIPCServer.__init__`, because `IPCServer`
defines no `__init__` of its own. For `CallbackNamedPipeServer` it is
`NamedPipeServer.__init__`, which enforces its Windows-only guard and derives
the pipe name before delegating. This is observable: constructing
`CallbackNamedPipeServer` on Linux raises
`NamedPipeServer is only available on Windows` from the `super()` call inside
`_CallbackServerBase.__init__`.

The `_stop_backend` hook takes a non-optional backend.
`_ServerLifecycle.stop()` calls the hook only when a backend instance exists,
so a server that never started never passes `None`. Implementations may
therefore dereference the argument unconditionally: `CallbackIPCServer` calls
`server.shutdown()` and `server.server_close()`, while
`CallbackNamedPipeServer` calls `server.stop()` and
`server.join_clients(self.timeout)`.

A further callback-driven transport should subclass `_CallbackServerBase` with
its backend type rather than restating the constructor signature.

### Named-pipe backend selection

The Windows transport reaches `pywin32` through an injected backend rather than
through module globals of its own. `NamedPipeBackend`, in
`cmd_mox/ipc/windows.py`, is the port: a `typing.Protocol` covering the pipe
lifecycle, the event handles, the overlapped read path, synchronous file I/O,
and handle closure. Handles, buffers, and overlapped structures stay opaque
`object` values because they are created by the port and only ever passed back
to it.

The port also carries the Win32 constants the transport builds its calls from,
and two module views: `win32file` and `pywintypes`. Those views exist because
the shared `read_pipe_message` and `write_pipe_payload` helpers in `windows.py`
perform synchronous I/O and match `pywintypes.error` themselves. The helpers
take the views as `win32file=` and `pywintypes=` arguments and receive them from
`.win32file` and `.pywintypes` on the active backend.

Two frozen dataclasses keep long Win32 argument lists legible:
`NamedPipeCreateSpec` for `CreateNamedPipe` and `PipeFileOpenSpec` for
`CreateFile`. A port method takes the spec and unpacks it at the call, so
adding a parameter does not lengthen a signature the transport has to thread
through.

`NamedPipeServer._create_backend` constructs `Win32NamedPipeBackend` and passes
it as the keyword-only `backend` argument of `_NamedPipeState`. That is the
injection point: the state never imports a `pywin32` module. The adapter, in
`cmd_mox/ipc/_win32_named_pipe_backend.py`, binds the guarded module bindings
onto the instance in `__init__` and forwards each port method to exactly one
pywin32 call. Construction reads the bindings rather than the class body
because they are `None` off Windows, where the module must still import.

The import guard lives once for the package, in `cmd_mox/ipc/windows.py`. The
adapter does not add a second one; it imports what that guard already produced.

Tests inject `FakeNamedPipeBackend` from
`cmd_mox/unittests/_named_pipe_fakes.py`, which lets the transport run on any
platform without replacing `pywin32` module globals. The `fake_backend` fixture
creates one and publishes it for `build_state`; `patch_win32` mutates that
instance and still returns its `(file_fake, pipe_fake)` pair; and
`windows_platform` redirects the construction site to the fake so
platform-gated code can run off Windows. Add a new Win32 operation to the port,
both adapters, and the fake together — a transport call that bypasses the
backend reintroduces the module-global dependency the seam exists to remove.

The real adapter is tested too. `Win32NamedPipeBackend` would otherwise be
uncovered on POSIX, because every transport test injects the fake, and an
unexercised adapter drags the repository's line-coverage ratchet down for code
that only runs on Windows. `test_win32_named_pipe_backend.py` patches recording
stand-ins for the four modules onto the adapter's namespace and asserts that
each port method forwards to the one pywin32 call it names, with the arguments
in call order. That keeps the adapter honest on any platform and costs no
Windows runner.

## Skylos dead-code policy

Treat a Skylos report as genuine dead code until a runtime caller has been
verified. Remove confirmed dead code. For a confirmed false positive that
cannot be represented as an ordinary static reference, first add a precise
typed entry-point rule to `[[tool.skylos.dead_code.entrypoints]]`. Add a named
exception to `[tool.skylos.whitelist.documented]` in `pyproject.toml` only when
an entry-point rule cannot model the verified boundary. Every exception must
name its verified runtime caller in a caller-specific reason. Group symbols
only when the same caller or lifecycle reaches all of them; otherwise, use
separate entries. Do not add unexplained exceptions or use the allow list to
avoid a removal. The `--no-grep-verify` configuration is intentional: test
references must not keep production symbols live in the blocking scan.

For a verified false positive that cannot be modelled with an entry-point rule,
use the command-first helper:

```bash
make skylos-allow SYMBOL=handler REASON="Loaded by plugin registry"
```

The target requires both variables to contain non-whitespace values and invokes
`skylos whitelist <symbol> --reason <reason>`. `SYMBOL` avoids WSL's
caller-owned `NAME` environment variable. Each read-modify-write is serialized
with `flock` on the ignored repository-local `.skylos-whitelist.lock`; tests
may override `SKYLOS_WHITELIST_LOCK` when running in an isolated temporary
directory. Treat the helper as a reviewed write: retain the matching
`[tool.skylos.whitelist.documented]` entry in `pyproject.toml`, with a
caller-specific reason, and never use it to avoid removing genuine dead code.

When a runtime entry point is reached through a cached factory, declare the
precise function and generated class types in
`[[tool.skylos.dead_code.entrypoints]]`, and name the factory or lifecycle that
reaches them in each reason. Keep a contract test for these dynamic boundaries
so a renamed symbol cannot silently make the rule stale.

Skylos parses source with the AST implementation of its own runtime. The
command-only `SKYLOS_CLI` therefore pins Python 3.14 to prevent newer syntax
from producing phantom findings. Scan-only global options such as
`--config-file pyproject.toml` belong in `SKYLOS`, not in the command-only
macro, so the `whitelist` subcommand remains first for helper dispatch.

The blocking scan targets production modules only, excludes test paths from the
liveness graph, and enables strict gate mode. Investigate every finding; remove
genuine dead code and record only verified false positives.

The contract test parses the Makefile with the pinned Makeutil executable, and
`make test` verifies that the parser is available before running the suite. CI
installs the same revision independently in each isolated full-suite job.

For local test runs, install the same parser and toolchain before running
`make test`:

```bash
rustup toolchain install nightly-2026-05-28 --profile minimal
RUSTFLAGS="-Zpolonius=next" cargo +nightly-2026-05-28 install \
  --git https://github.com/leynos/makeutil \
  --rev 29fc5a1634ffbaa18a773eed9dff1b2838a45d9c \
  --locked --force makeutil
make test
```

## Code-duplication gate

The final lint tier runs [nose](https://github.com/corca-ai/nose), the Rust
`nose-cli` clone detector, behind `scripts/duplication_gate.py`. Nose is not
the Python test framework of the same name on PyPI. The gate, its exception
format and the decision to adopt it are recorded in
[ADR 002](./adr-002-code-duplication-gate.md).

Run it on its own with:

```bash
make duplication
```

The scan, its channels and its size floor are configured in `[tool.nose]` in
`pyproject.toml`. `roots` names the production Python surface, `exclude` skips
the packaged unit tests, and all three detection channels are pinned explicitly
so a change to nose's defaults cannot widen or narrow the gate unnoticed. The
`min-size` floor counts nose intermediate-language (IL) tokens, not Python
lines or abstract syntax tree nodes.

Exclusions are matched relative to each configured root, not to the repository
root. `unittests/*` therefore names `cmd_mox/unittests/`; the repository-root
form `cmd_mox/unittests/*` would match nothing and silently scan the test tree
instead.

`scripts/tests/test_nose_config_contract.py` pins that surface. It reads the
production table through `nose_detector.load_settings` and checks four things:
the settings match the intended scan, every root exists inside the repository,
the roots still select production modules, and every exclusion still hides
files. Changing the scan therefore fails a test rather than quietly shrinking
what the gate enforces. The module imports only the standard library, so it
also runs in the application suite, not just under `make duplication-test`.

The gate adjudicates a ranked budget of families rather than the whole report.
`top` bounds how many of the highest-ranked families are checked; duplication
below that ranking is reported nowhere and enforced nowhere. Do not raise
thresholds or lower the budget to reach a green build, and do not describe
lower-ranked duplication as enforced. Re-check saturation rather than assuming
it, since the control scan recorded in ADR 002 only shows the budget was not
saturated when it was taken.

### Recording an exception

When duplication is intentional, record it with a written reason:

```bash
make duplication-allow FIRST='cmd_mox/a.py::run' SECOND='cmd_mox/b.py::run' \
  REASON="Two independent transport implementations must not share a base."
```

An entry is a TOML `[[tool.duplication_gate.allow]]` block with either one
`unit` key or several `members` keys, never both, plus a non-blank `reason`. A
key is a repository-relative POSIX path matched as a full glob, optionally
suffixed `::name` to require the exact unit name nose reported. A `::name` key
never matches an unnamed fragment. `FIRST`, `SECOND` and `REASON` are read only
from the make command line, so an ambient environment variable of the same name
cannot create an exception. The underlying CLI accepts repeated `--second` for
families with more than two members.

An entry silences a family only when every location in that family matches one
of its keys. Partial coverage from several entries does not combine, and a new
copy in an unlisted file blocks the build. Keys name locations, not line
numbers or detector identifiers, so moving code does not invalidate an
exception and regenerating the report does not renumber one.

Prefer explicit paths and names. A path-only key is a glob that also covers
future locations in its scope; justify that scope in the reason and do not
describe the entry as protection against every new clone. Never use a
repository-wide wildcard, mass-generated reasons, or an automatic allowlisting
of the initial scan. No exceptions were copied from the reference repository;
each one here was adjudicated against this tree.

The gate prints allow entries that matched no finding. Absence from a capped
ranked report means "unmatched in this scan"; it is not proof the duplication
disappeared, and an entry whose family fell below the ranking budget is still
stale by that definition. Confirm with source inspection, or with an adequately
widened scan, before removing an entry.

### Provisioning

`make install-nose` installs the pinned release binary into `$(NOSE_TOOLS_DIR)`
through `cargo-binstall`'s Git mode, and is a no-op when the correct version is
already there. The install explicitly forbids cargo-binstall's `compile` and
`quick-install` strategies, so a platform with no prebuilt binary fails
provisioning rather than silently starting a costly source build or fetching
from an unapproved source. The installed binary must report the pinned version,
both when discovered and after installation.

The gate itself runs as isolated CPython 3.14 tooling. It neither installs nor
imports the application and needs no application service, and the application
keeps its own Python floor.

The Makefile builds that environment through the vendored `uv_gate` helper's
tool channel, naming the interpreter and both dependencies explicitly:

```make
DUPLICATION_GATE = NOSE_BIN=$(NOSE_BIN) $(UV_GATE) tool \
	--from 'python@$(DUPLICATION_PYTHON)' \
	--with cyclopts==$(CYCLOPTS_VERSION) --with tomlkit==$(TOMLKIT_VERSION) \
	-- python scripts/duplication_gate.py
```

The gate script keeps its own PEP 723 block, so
`uv run scripts/duplication_gate.py` still works when invoked directly, and
`test_duplication_gate_pins_match_the_script_metadata` fails if the Makefile
pins and that block disagree. The tool channel is used rather than
`uv_gate run` for two reasons: `run` always adds `--frozen`, which a PEP 723
script cannot satisfy without a committed script lockfile, and `run` never goes
online, so a cold cache could never provision the environment at all. The tool
channel warms once on a proven cache miss and refuses an unpinned spec, which
is why the interpreter is requested as `python@<version>` rather than through a
bare `--python`.

Because the helper uses POSIX file locking and directory operations, the
supported developer workflow is POSIX, including WSL, and native Windows
operation is not claimed; the Windows CI job does not run the gate.

Run the focused helper tests with:

```bash
make duplication-test
```

They run on the tooling interpreter with injected command runners and stub
executables, so they never download the detector.

## Spelling policy

The lint and Markdown gates run a pinned `typos` release with British English
and Oxford `-ize` conventions. Before checking maintained Markdown, the
generator refreshes the shared estate dictionary into an untracked local cache
only when the authority is newer and merges `typos.local.toml`. The generated
`typos.toml` is reviewed and committed so a clean network-restricted checkout
can still enforce the last known-good policy.

`make spelling` checks existing `*.md` files that are tracked by Git or
untracked and not ignored. Standard Git ignore rules exclude generated caches
and other ignored files, while new Markdown is checked before it is staged. Add
repository-only proper names or quoted upstream terms to `typos.local.toml`;
never edit generated entries in `typos.toml` by hand.

## Episodic lint policy

CmdMox imports its lint posture from
[Episodic](https://github.com/leynos/episodic). The imported policy has four
goals:

- keep Ruff as the fast, broad, first-pass linter;
- use focused Pylint checks for problems that Ruff does not cover as well;
- run a pinned Pylint under uv-managed PyPy 3.12 as an isolated tool; and
- detect unused production symbols with a local, blocking Skylos scan.

The DF12 extension preserves that baseline while adding an isolated CPython
3.14 pass. Its plugin is deliberately configured separately so baseline Pylint
debt cannot hide DF12 house-style and suppression-explanation checks.

The policy is adapted for CmdMox rather than copied blindly. CmdMox targets
Python 3.12 in `pyproject.toml`, while Episodic targets a newer interpreter.
Unsupported Ruff selectors are omitted, and the existing CmdMox baseline is
made explicit so the imported lint architecture can land without unrelated
behavioural refactors.

## `pyproject.toml` lint configuration

Most lint policy lives in `pyproject.toml`.

### Ruff tables

- `[tool.ruff]` sets shared Ruff behaviour, including `line-length = 88`,
  `preview = true`, and `target-version = "py312"`.
- `[tool.ruff.lint]` selects the imported rule families. The selection includes
  Pyflakes (`F`), pycodestyle (`E` and `W`), import ordering (`I`), pathlib
  checks (`PTH`), security checks (`S`), pytest checks (`PT`), documentation
  checks (`D` and `DOC`), asynchronous-code checks (`ASYNC`), annotation checks
  (`ANN`), Ruff-specific checks (`RUF`), and Pylint-compatible checks (`PLR`,
  `PLE`, and `PLW`).
- `extend-ignore` records conflicts and the current CmdMox baseline. Entries
  here should be removed when the corresponding code is cleaned up.
- `[tool.ruff.lint.per-file-ignores]` relaxes assertion and parameter-count
  rules in test and step files where pytest and behaviour-driven development
  (BDD) patterns need them.
- `[tool.ruff.lint.mccabe]` and `[tool.ruff.lint.pylint]` define complexity,
  argument-count, boolean-expression, and local-variable thresholds.
- `[tool.ruff.lint.flake8-import-conventions]` bans selected `from` imports
  and sets standard aliases such as `typing as typ` and
  `collections.abc as cabc`.
- `[tool.ruff.lint.flake8-tidy-imports.banned-api]` rejects deprecated
  `typing` collection aliases in favour of built-in collection types,
  `collections.abc`, `collections`, `contextlib`, or `re` as appropriate.
- `[tool.ruff.lint.pydocstyle]` keeps docstrings on the NumPy convention.
- `[tool.ruff.lint.pydoclint]` applies complete-docstring checks without
  requiring boilerplate for intentional one-line private-helper summaries.

### Pylint tables

- `[tool.pylint.main]` enables recursive directory linting and sets the
  module-line ceiling.
- `[tool.pylint.design]` aligns Pylint design thresholds with the Ruff policy
  while keeping a wider legacy allowance where needed.
- `[tool.pylint."messages control"]` disables all messages by default, then
  enables only the selected second-tier checks. `syntax-error` stays enabled,
  so a module the PyPy runtime cannot parse fails the lint instead of being
  skipped silently.

Pylint suppressions follow the same rule as Ruff ones: scoped to one definition
with `# pylint: disable-next=<message>` and justified on the lines above. The
one standing exception is `too-many-public-methods` on `CommandDouble` in
`cmd_mox/test_doubles.py`, whose deliberately wide fluent builder API already
carries the matching Ruff ignore; splitting that DSL across classes to satisfy
a method count would fragment it. `tests/test_pylint_tier_contract.py` holds
the tier's interpreter pin, its Pylint release pin, and the rule that
`syntax-error` stays enabled.

Inline pragmas may name df12 messages that the classic PyPy pass never
registers. For example, the `trivial-attribute-wrapper` pragma in
`cmd_mox/expectations.py` is unknown to that pass, and Pylint reports
`unknown-option-value` (W0012) for the name even under `disable = ["all"]`.
`pyproject.toml` therefore disables W0012 by name for the classic pass, while
`pylintrc-df12.toml` enables it, because that pass registers both the core and
the df12 messages. Every pragma name is still validated exactly once, by the
one pass whose vocabulary is complete, and `tests/test_pylint_tier_contract.py`
pins that split.

The `Makefile` currently supplies `PYLINT_BASELINE_DISABLE` in addition to the
`pyproject.toml` tables. That split is intentional: `pyproject.toml` documents
the desired selected Pylint policy, while the `Makefile` carries the temporary
project baseline required to keep the new tier actionable.

`pylintrc-df12.toml` is the separate configuration for the CPython 3.14 DF12
tier. It loads `df12_python_lints`, analyses the Python 3.12 source baseline,
disables the general Pylint catalogue, and enables the complete DF12 checker
list explicitly. This keeps the DF12 policy auditable and independent of the
temporary PyPy Pylint baseline.

### Skylos tables

- `[tool.skylos.gate]` enables strict failure for unexplained dead-code
  findings.
- `[tool.skylos.whitelist.documented]` stores only reasoned false positives;
  each entry must identify the verified runtime caller.

### Nose and duplication-gate tables

- `[tool.nose]` pins the detector version, the scanned roots, the detection
  channels, the size floor, the reported surface and the ranked budget. Its
  `version` must agree with the Makefile and CI pins.
- `[tool.duplication_gate]` is otherwise empty; each
  `[[tool.duplication_gate.allow]]` entry below it records one reviewed
  exception with its `unit` or `members` keys and a non-blank `reason`.

## Updating lint policy

When changing lint policy:

1. Update `pyproject.toml` or the relevant `Makefile` variable.
2. Document the behaviour change in this guide.
3. Run `make lint`.
4. If Markdown or ADR files changed, run `make markdownlint` and `make nixie`.
5. Remove baseline suppressions when the underlying code has been cleaned up.
6. When bumping `NOSE_VERSION`, update all three pins together and let
   `tests/test_toolchain_contract.py` confirm they agree.

Changes that add broad new rule families should explain whether failures are
fixed immediately or recorded as a visible baseline.

## Workflow pins and Dependabot

Dependabot owns the upgrade of GitHub Actions and reusable workflows, including
calls into `leynos/shared-actions`. Contract tests that assert a caller's exact
commit SHA create a lockstep dependency: every time Dependabot opens a bump PR,
the test fails until a human edits the pinned constant to match. That defeats
the purpose of automated dependency updates and turns a routine bump into a
manual chore.

Contract tests may still verify the _shape_ of a reusable-workflow caller. They
must not verify the specific SHA value.

- Do assert the workflow references the correct reusable workflow path.
- Do assert the ref is pinned to a full 40-character commit SHA, not a
  mutable branch such as `main` or `rolling`.
- Do assert the expected `on:` triggers, least-privilege `permissions:`, and
  the inputs the caller relies on.
- Do not hard-code the current SHA value as an expected string. Match it with
  a pattern instead.
- Do not fail a test purely because Dependabot bumped the pinned SHA.

```python
import re

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

def test_uses_pinned_full_sha(caller_step):
    ref = caller_step["uses"].split("@")[-1]
    assert SHA_RE.match(ref), f"expected a 40-hex commit SHA, got {ref!r}"
```

If a workflow's behaviour genuinely depends on a feature only present from a
particular commit onwards, express that as a comment or a changelog note, not
as a test assertion on the SHA string.

## Mutation-testing workflow contract tests

This repository runs scheduled, informational mutation testing through a thin
caller workflow,
[`.github/workflows/mutation-testing.yml`](../.github/workflows/mutation-testing.yml),
which delegates to the shared reusable workflow
`leynos/shared-actions/.github/workflows/mutation-mutmut.yml`. The heavy
lifting — running `mutmut` and summarizing survivors — lives in
`shared-actions`; this repository carries only declarative configuration. The
run is **informational only**: its results never gate pull requests. The
separate contract test does gate pull requests when the caller drifts, as
described below. Survivors are reported through the job summary and
downloadable artefacts so they can be triaged into tests, not enforced as a
blocking check. The mutation targets and test selection themselves are
configured in `[tool.mutmut]` in `pyproject.toml` (`source_paths`,
`pytest_add_cli_args_test_selection`, `runner`).

The workflow runs in two modes. A **daily schedule** fires a change-scoped run
that mutates only the source files touched within the detection window, so
quiet days are cheap no-ops. A **manual dispatch** (the Actions "Run workflow"
control) mutates the whole package; select a branch in that control to exercise
a feature branch.

The caller passes two configuration inputs:

- `paths` — set to `cmd_mox/`, the change-detection glob that decides whether
  a scheduled run has anything to mutate. CmdMox uses a flat layout, so the
  mutable source lives directly under `cmd_mox/` rather than under `src/`.
- `module-prefix-strip` — set to an empty string, because the flat layout has
  no package prefix to strip when mapping mutated files back to import paths.

The `uses:` reference pins the shared workflow to a full 40-character commit
SHA rather than a branch or tag, so a force-push upstream cannot silently
change what runs here. The contract test asserts only that the pin is a full
commit SHA, not a particular value, so Dependabot bumps it automatically
without any accompanying test edit.

Because the caller is configuration rather than code, a contract test in
`tests/test_workflow_contract.py` pins the shape it must uphold, failing the
pull request when the caller drifts — repointing the pin at a branch, widening
the token scope, or dropping a configuration input — rather than letting the
breakage surface only in a scheduled run. The test module self-skips when the
workflow file is absent (mutmut copies the sources into a sandbox that omits
`.github/`, so the contract test does not run there). Run it locally with:

```bash
uv run pytest tests/test_workflow_contract.py -v
```

The test validates:

- the `uses:` reference targets `mutation-mutmut.yml` pinned to a full commit
  SHA;
- the `with:` block carries exactly `paths: cmd_mox/` and
  `module-prefix-strip: ""`, nothing more and nothing less;
- job permissions are least-privilege (`contents: read`, `id-token: write`)
  and the workflow-level default token scope is empty;
- `concurrency` serializes runs per ref without cancelling one in progress;
  and
- the triggers keep the daily schedule and a plain `workflow_dispatch` with
  no inputs.
