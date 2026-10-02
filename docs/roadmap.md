# CmdMox roadmap

This roadmap translates the design into actionable increments without calendar
commitments. Phases are ordered and build on one another. Steps group related
workstreams. Tasks are measurable and must meet acceptance criteria to count as
done.

## 1. Project foundation and infrastructure

Focus: Establish the repository skeleton, development toolchain, and baseline
documentation.

### 1.1. Core repository structure

- [x] 1.1.1. Create project skeleton (`cmd_mox/`, `tests/`, `conftest.py`, and
  related directories).
- [x] 1.1.2. Set up packaging, Continuous Integration (CI), linting, and
  type-checking (`pyproject.toml`, `pytest`, `ruff`, and `mypy`).

### 1.2. Initial documentation

- [x] 1.2.1. Draft `README.md` with basic usage and conceptual overview.
- [x] 1.2.2. Add the design specification as
  `docs/python-native-command-mocking-design.md`.

## 2. Core components: environment and inter-process communication

Focus: Provide robust runtime environment management, shim generation, and
inter-process communication (IPC).

### 2.1. Environment manager

- [x] 2.1.1. Implement context manager to save and restore environment variables
  (including `PATH`).
- [x] 2.1.2. Implement temporary shim directory creation and cleanup.
- [x] 2.1.3. Publish environment variable(s) used for IPC endpoint discovery.

### 2.2. Shim generation engine

- [x] 2.2.1. Ship a single Python `shim.py` template.
- [x] 2.2.2. Generate per-command symlinks in the temporary shim directory.
- [x] 2.2.3. Route behaviour by command identity derived from `argv[0]`.
- [x] 2.2.4. Ensure shim executability on all supported Unix platforms.

### 2.3. IPC bus

- [x] 2.3.1. Implement lightweight IPC server in the main test process.
- [x] 2.3.2. Start and stop the IPC server in sync with lifecycle transitions.
- [x] 2.3.3. Use structured JSON communication for invocation and response
  payloads.
- [x] 2.3.4. Implement timeout handling and robust IPC cleanup.

## 3. CmdMox controller and public API

Focus: Provide a stable controller abstraction and ergonomic test integration.

### 3.1. Controller lifecycle

- [x] 3.1.1. Implement `CmdMox` controller state for expectations, stubs, spies,
  and the invocation journal.
- [x] 3.1.2. Implement lifecycle transitions (`record`, `replay`, and
  `verify`).

### 3.2. Factory methods

- [x] 3.2.1. Implement `cmd_mox.mock(cmd)`.
- [x] 3.2.2. Implement `cmd_mox.stub(cmd)`.
- [x] 3.2.3. Implement `cmd_mox.spy(cmd)`.

### 3.3. Pytest plugin

- [x] 3.3.1. Register `cmd_mox` fixture.
- [x] 3.3.2. Add pytest-xdist awareness for worker-specific temporary paths.

### 3.4. Context manager interface

- [x] 3.4.1. Support explicit `with cmd_mox.CmdMox() as mox:` usage.

## 4. Command double implementations

Focus: Provide complete stub, mock, and spy capabilities with fluent APIs.

### 4.1. Stub command

- [x] 4.1.1. Implement `.returns()` and `.runs()` for static and dynamic
  behaviour.
- [x] 4.1.2. Exclude stubs from strict verification requirements.

### 4.2. Mock command

- [x] 4.2.1. Implement `.with_args()`, `.with_matching_args()`, and
  `.with_stdin()`.
- [x] 4.2.2. Implement `.returns()` and `.runs()`.
- [x] 4.2.3. Implement `.times()`, `.in_order()`, and `.any_order()`.
- [x] 4.2.4. Implement `.with_env()` for environment matching and injection.
- [x] 4.2.5. Enforce strict record-replay-verify behaviour.

### 4.3. Spy command

- [x] 4.3.1. Implement `.returns()` for canned responses.
- [x] 4.3.2. Implement `.passthrough()` for real command execution in replay.
- [x] 4.3.3. Maintain invocation history (`invocations` and `call_count`).

### 4.4. Fluent API enhancements

- [x] 4.4.1. Implement fluent expectation DSL. See
  `python-native-command-mocking-design.md#24-the-fluent-api-for-defining-expectations`.
- [x] 4.4.2. Add spy assertion helpers mirroring `unittest.mock` semantics
  (`assert_called`, `assert_called_with`, and `assert_not_called`).

## 5. Matching and verification engine

Focus: Verify interactions deterministically with clear diagnostics.

### 5.1. Comparator classes

- [x] 5.1.1. Implement `Any`, `IsA`, `Regex`, `Contains`, `StartsWith`, and
  `Predicate` comparators.
- [x] 5.1.2. Integrate comparator plumbing into mock argument matching.

### 5.2. Invocation journal

- [x] 5.2.1. Capture command, args, stdin, env, stdout, stderr, and exit code
  for each invocation.
- [x] 5.2.2. Store journal entries in a deque to preserve order.
- [x] 5.2.3. Support configurable journal bounds via `max_journal_entries`.

### 5.3. Verification algorithm

- [x] 5.3.1. Fail early on unexpected calls.
- [x] 5.3.2. Report unfulfilled expectations.
- [x] 5.3.3. Verify call counts and strict ordering constraints.
- [x] 5.3.4. Emit clear diff-style error reports (`VerificationError` and
  related subclasses).

## 6. Shim behaviour

Focus: Ensure launcher behaviour is correct, portable, and deterministic.

### 6.1. Shim startup logic

- [x] 6.1.1. Determine mocked command identity via `argv[0]`.
- [x] 6.1.2. Connect to IPC endpoint.
- [x] 6.1.3. Capture stdin, argv, and env.
- [x] 6.1.4. Send invocation to IPC server and wait for response.
- [x] 6.1.5. Apply returned behaviour (`stdout`, `stderr`, and exit code).

### 6.2. Passthrough spies

- [x] 6.2.1. Extend IPC protocol to request real command execution.
- [x] 6.2.2. Resolve and execute real command using original `PATH`, then return
  result payload.

## 7. Advanced features and edge cases

Focus: Handle environment overlays, concurrency, and cleanup guarantees.

### 7.1. Environment variable injection

- [x] 7.1.1. Implement `.with_env()` injection before handler or canned response
  execution.

### 7.2. Concurrency support

- [x] 7.2.1. Ensure safe parallel use with unique per-test temporary paths and
  socket names.

### 7.3. Robust cleanup

- [x] 7.3.1. Always restore environment and remove temporary paths on errors and
  interrupts.

## 8. Documentation, examples, and usability

Focus: Provide complete user guidance and migration pathways.

### 8.1. API reference and tutorials

- [x] 8.1.1. Publish complete public API and matcher documentation.
- [x] 8.1.2. Add example tests for stubs, mocks, spies, pipelines, and
  passthrough mode.
- [x] 8.1.3. Publish migration guide for `shellmock` users.

## 9. Quality assurance

Focus: Expand automated confidence across unit, integration, and behavioural
layers.

### 9.1. Unit and integration testing

- [ ] 9.1.1. Reach full test coverage across core components, especially IPC and
  environment manipulation.
- [ ] 9.1.2. Add explicit pytest-xdist compatibility tests.
- [ ] 9.1.3. Add regression suite for pipelines, missing commands, and complex
  argument handling.
- [ ] 9.1.4. Add behavioural acceptance tests covering full fluent API with
  `pytest-bdd` and `cfparse`.

## 10. Release and post-MVP

Focus: Prepare and execute initial public release.

### 10.1. First public release (1.0.0)

- [ ] 10.1.1. Polish documentation, perform cleanup, and publish to PyPI.
- [ ] 10.1.2. Announce project and collect early user feedback.

## 11. Windows platform support

Focus: Deliver first-class Windows compatibility for IPC and shims.

### 11.1. Windows platform enablement

- [x] 11.1.1. Establish cross-platform IPC and shim abstractions including
  Windows implementations. Acceptance: end-to-end pytest suite passes on
  `windows-latest` with IPC and shims enabled.
- [x] 11.1.2. Implement Windows named-pipe IPC (`win32pipe` and `win32file`) and
  package `pywin32` dependency.
- [x] 11.1.3. Validate environment and filesystem helpers on Windows, including
  `PATHEXT` lookup, CRLF launchers, quoting and escaping, max-path handling,
  and case-insensitive filesystem behaviour.
- [x] 11.1.4. Extend CI to exercise Windows workflows (`windows-latest` matrix
  job) and publish IPC diagnostics artefacts.

## 12. Record mode

Record mode transforms passthrough spy recordings into reusable fixtures. This
supports deterministic replay without external dependencies. See
`python-native-command-mocking-design.md` section IX.

### 12.1. Core recording infrastructure (MVP)

- [x] 12.1.1. Implement `RecordingSession` with fixture persistence,
  session lifecycle management, fixture metadata generation, and environment
  subset filtering.
- [x] 12.1.2. Implement `FixtureFile` with JSON serialization, versioned schema
  (`1.0`), and migration support.
- [x] 12.1.3. Add `.record()` to `CommandDouble` with validation that
  passthrough mode is enabled and support for custom scrubber and allowlist
  parameters.
- [x] 12.1.4. Integrate recording into
      `PassthroughCoordinator.finalize_result()`
  with optional recording session wiring.
- [x] 12.1.5. Add unit tests for recording lifecycle, serialization roundtrips,
  and environment filtering.

### 12.2. Replay infrastructure

- [x] 12.2.1. Implement `ReplaySession` with fixture loading, schema validation,
  consumed-record tracking, and strict and fuzzy modes.
- [x] 12.2.2. Implement `InvocationMatcher` with strict matching, fuzzy
  matching, and best-fit score selection.
- [x] 12.2.3. Add `.replay()` to `CommandDouble`, including passthrough
  incompatibility validation and strict-mode option.
- [x] 12.2.4. Integrate replay into `CmdMox._make_response()` and raise
  `UnexpectedCommandError` for unmatched strict replay invocations.
- [x] 12.2.5. Extend `CmdMox.verify()` to report unconsumed recordings.
- [ ] 12.2.6. Add unit tests for fixture loading, matcher behaviour, and
  consumption tracking.

### 12.3. Scrubbing and security

- [ ] 12.3.1. Implement `Scrubber` with default secret redaction patterns,
  including GitHub PATs, AWS access keys, generic tokens, bearer headers,
  private keys, and database connection strings.
- [ ] 12.3.2. Implement `ScrubbingRule` dataclass with pattern, replacement,
  target fields, and documentation description.
- [ ] 12.3.3. Add environment filtering with default exclusions, configurable
  allowlist, and command-specific prefix support.
- [ ] 12.3.4. Implement review mode to emit companion `.review` artefacts
  showing original and scrubbed values with sensitivity warnings.
- [ ] 12.3.5. Add security-focused unit tests for default patterns,
  environment filtering, and review-file generation.

### 12.4. Pytest integration

- [ ] 12.4.1. Add `@pytest.mark.cmdmox_record` marker with automatic fixture
  directory handling and convention-based naming.
- [ ] 12.4.2. Add `@pytest.mark.cmdmox_replay` marker with automatic fixture
  loading and strict or fuzzy mode configuration.
- [ ] 12.4.3. Implement automatic fixture naming based on test module and
  function names, with collision handling and custom override support.
- [ ] 12.4.4. Ensure pytest-xdist recording compatibility with worker-isolated
  fixture paths and parallel aggregation support.

### 12.5. CLI tool

- [ ] 12.5.1. Implement `cmdmox record` with `--output`, optional command
  filters, and target command execution.
- [ ] 12.5.2. Implement `cmdmox replay` with fixture selection and strict mode.
- [ ] 12.5.3. Implement `cmdmox generate-test` to emit pytest tests from
  recorded fixtures.
- [ ] 12.5.4. Implement `cmdmox scrub` for post-hoc fixture sanitization.
- [ ] 12.5.5. Implement `cmdmox validate` for schema and corruption checks,
  including glob support.

### 12.6. Documentation and examples

- [ ] 12.6.1. Document `RecordingSession`, `ReplaySession`, `Scrubber`,
  `ScrubbingRule`, and fixture schema.
- [ ] 12.6.2. Add tutorial for recording fixtures, including end-to-end Git
  examples and fixture format explanation.
- [ ] 12.6.3. Add tutorial for Continuous Integration and Continuous Deployment
  (CI/CD) replay workflows, fixture update practices, and versioning guidance.
- [ ] 12.6.4. Add migration guide from raw passthrough tests to fixture-based
  workflows.

## 13. Rust mock command binary

This phase introduces native `cmdmox-mock` launcher support to reduce fragility
from shell and `.cmd` wrappers while preserving existing IPC protocol and
record-replay-verify semantics. See `python-native-command-mocking-design.md`
section 8.12.

### 13.1. Rust workspace and build foundation

- [ ] 13.1.1. Introduce Cuprum-style Rust structure with `rust/Cargo.toml`
  workspace root, `rust/cmdmox-mock/` binary crate, and `rust/Makefile` targets
  for native build, test, lint, and format checks.
- [ ] 13.1.2. Add Python-side backend probing utilities in
  `cmd_mox/_rust_mock_backend.py`, including resolved binary discovery.
- [ ] 13.1.3. Differentiate probe failures explicitly (missing binary versus
  present but broken binary).

### 13.2. Launcher backend selection and integration

- [ ] 13.2.1. Add `CMOX_SHIM_BACKEND=auto|python|rust` runtime selection.
- [ ] 13.2.2. Make `auto` prefer Rust when available and `rust` fail fast with
  actionable diagnostics when unavailable.
- [ ] 13.2.3. Integrate backend-aware shim generation across platforms:
  POSIX symlinks to selected launcher, Windows `.cmd` for Python backend, and
  Windows `.exe` launcher links for Rust backend.

### 13.3. Native launcher implementation

- [ ] 13.3.1. Implement `cmdmox-mock` invocation pathway with cross-platform
  command identity resolution, argument capture, stdin capture, and environment
  capture matching Python shim payload shape.
- [ ] 13.3.2. Implement IPC client compatibility for Unix domain sockets and
  Windows named pipes using existing logical socket mapping.
- [ ] 13.3.3. Preserve passthrough execution parity for PATH filtering,
  environment overlays, result reporting, and missing executable failures.

### 13.4. Packaging and distribution

- [ ] 13.4.1. Add native wheel pipeline including `cmdmox-mock` binaries for
  Linux, macOS, and Windows, and keep Python package and wheel metadata aligned.
- [ ] 13.4.2. Continue publishing pure Python wheels alongside native wheels.
- [ ] 13.4.3. Document source-build requirements and ensure source installs can
  still run via Python shim fallback.

### 13.5. Testing, CI, and performance validation

- [ ] 13.5.1. Add backend parity matrix running shim behaviour tests against
  both `python` and `rust` backends.
- [ ] 13.5.2. Add parity tests for quoting, stdin capture, env transport, and
  Windows-specific caret, percent, and space handling.
- [ ] 13.5.3. Add per-OS CI smoke workflows with backend logs and IPC transcript
  artefacts, plus non-regression checks for fallback selection behaviour.

### 13.6. Rollout and documentation

- [ ] 13.6.1. Extend design and user documentation for dual backend operation,
  including backend selection flags, troubleshooting, and limitations.
- [ ] 13.6.2. Add migration guidance for users relying on existing shell and
  `.cmd` shims.
- [ ] 13.6.3. Define rollout gates before default backend changes: parity across
  Linux, macOS, and Windows CI, no open severity-1 regressions, and release
  notes containing rollback instructions via `CMOX_SHIM_BACKEND`.

## 14. Stateful fake capabilities

Idea: if CmdMox provides durable JSON persistence and a small state-store
toolkit before higher-level fake helpers, realistic package-manager, cloud-CLI,
`git`, `docker`, and `kubectl` fakes can share one correctness boundary instead
of copying ad hoc filesystem code.

This phase turns patterns from production-like fake commands into reusable
CmdMox building blocks. Atomic persistence and locking come first because they
protect fixtures and shared state. Routing and filesystem side-effect helpers
follow as ergonomics for `.runs(...)` handlers. See
`cmd-mox-fake-capabilities-design.md`.

### 14.1. Harden JSON fixture persistence

This step answers whether CmdMox can make fixture writes durable without
changing the public record/replay API. The outcome informs every later helper
that writes JSON state. See `cmd-mox-fake-capabilities-design.md` §§Problem,
Goals, and Atomic JSON persistence.

- [ ] 14.1.1. Implement internal
  `atomic_write_json(path, payload, mode=0o600)` for fixture and state files.
  - Use same-directory temporary files, explicit UTF-8 JSON serialization,
    file flush, file `fsync`, `os.replace`, parent-directory `fsync` where
    supported, and temporary-file cleanup on failure.
  - Success: interrupted or failing writes preserve the previous complete JSON
    file, and successful writes leave owner-restricted files on POSIX.
- [ ] 14.1.2. Route `FixtureFile.save()` through `atomic_write_json(...)`
  without changing `FixtureFile.load()` or the fixture schema.
  - Requires 14.1.1.
  - See `python-native-command-mocking-design.md` §9.5.3 and
    `cmd-mox-fake-capabilities-design.md` §Atomic JSON persistence.
  - Success: existing record-mode fixture round-trips still pass, and new
    failure-injection tests prove old fixture contents survive failed writes.

### 14.2. Add a reusable lock-with-timeout utility

This step answers whether CmdMox can coordinate cooperating file-backed writers
under parallel test execution. The outcome is the concurrency contract for the
state-store helper. See `cmd-mox-fake-capabilities-design.md` §Lock with
timeout.

- [ ] 14.2.1. Implement an internal lock-file context manager with timeout
  semantics and restricted lock-file permissions.
  - Retry only contention errors, raise timeout-specific diagnostics when the
    deadline expires, and re-raise unrelated filesystem errors immediately.
  - Success: tests cover successful acquisition, contention timeout, immediate
    non-contention failure, and release after exceptions.
- [ ] 14.2.2. Provide POSIX and Windows-compatible lock backends or document a
  deliberately scoped platform limitation before exposing the helper.
  - Requires 14.2.1.
  - Success: the helper has explicit backend coverage in CI or a documented
    fallback path that keeps existing Windows support intact.

### 14.3. Provide file-backed state for dynamic command doubles

This step answers whether `.runs(...)` handlers can model state across command
invocations without each project reimplementing JSON persistence. The outcome
unlocks realistic fakes for install/list/uninstall, create/read/delete, and
login/logout command families. See `cmd-mox-fake-capabilities-design.md`
§File-backed state store.

- [ ] 14.3.1. Implement `JsonStateStore` with missing-file initialization,
  locked transactions, JSON corruption diagnostics, and atomic writeback.
  - Requires 14.1.1 and 14.2.1.
  - Keep state schema caller-owned and separate from record/replay fixture
    schema.
  - Success: concurrent transactions using the helper do not lose updates when
    mutating disjoint keys.
- [ ] 14.3.2. Add package-manager-style examples that model install, list, and
  uninstall operations through `.runs(...)` plus `JsonStateStore`.
  - Requires 14.3.1.
  - See `python-native-command-mocking-design.md` §2.4 and
    `cmd-mox-fake-capabilities-design.md` §§File-backed state store and
    Roadmap impact.
  - Success: examples demonstrate state mutation, read-only listing, and no-op
    uninstall without using private test globals.

### 14.4. Reduce argv protocol boilerplate

This step answers whether CmdMox should provide a small routing helper or rely
on examples alone. The outcome determines how much protocol-simulator
ergonomics belong in core. See `cmd-mox-fake-capabilities-design.md`
§Subcommand router helper.

- [ ] 14.4.1. Implement or explicitly reject `SubcommandRouter` after a
  prototype against the package-manager example.
  - Requires 14.3.2.
  - If accepted, support exact matches, prefix matches, deterministic missing
    route errors, deterministic ambiguous route errors, and normal `.runs(...)`
    return values.
  - Success: the chosen path removes duplicated argv dispatch from examples or
    records why examples are sufficient.
- [ ] 14.4.2. Document `.runs(...)` handler patterns for argv-driven command
  fakes even if `SubcommandRouter` remains optional or deferred.
  - Requires 14.4.1.
  - Success: users can implement `tool list`, `tool install`, and
    `tool uninstall`-style fakes from documentation alone.

### 14.5. Add filesystem side-effect helpers

This step answers whether common side effects can be expressed safely without a
side-effect framework. The outcome gives stateful fakes a supported way to
create observable files and executable shims. See
`cmd-mox-fake-capabilities-design.md` §Filesystem side-effect helpers.

- [ ] 14.5.1. Implement helper functions for atomic text-file writes and
  executable-file writes with explicit permissions.
  - Requires 14.1.1.
  - Success: helpers create parent directories, preserve file contents on
    failed writes, and apply executable bits on POSIX.
- [ ] 14.5.2. Add an executable-shim side-effect example that creates a command
  installed by a fake package manager.
  - Requires 14.5.1.
  - Success: an example fake creates an executable side effect that a later
    subprocess invocation can resolve through `PATH`.

### 14.6. Documentation and adoption

This step answers whether the helpers are understandable as a small toolkit
rather than a second mocking framework. The outcome informs whether any helper
should graduate from internal utility to documented public API.

- [ ] 14.6.1. Update usage and developer documentation with security,
  portability, and cleanup guidance for file-backed fake state.
  - Requires steps 14.1-14.5.
  - See `cmd-mox-fake-capabilities-design.md` §§Security and portability, and
    Verification strategy.
- [ ] 14.6.2. Add regression and behavioural coverage for the full stateful
  fake workflow under serial and parallel test execution.
  - Requires steps 14.1-14.5.
  - Success: the suite exercises fixture recording, state-store mutation,
    router dispatch, and executable side effects without leaking files between
    tests.

## 15. Container execution and Act integration

Status: Proposed, post-MVP. This phase implements
[RFC 001](rfc-001-act-execution-environment.md), not an already supported
execution mode. It adds controlled command dependencies to real Act workflow
tests while retaining the existing local environment and black-box validation.
Existing task numbers and completion states remain unchanged.

Steps 15.1-15.5 form the first usable Linux/Python plateau. They require only the
explicit dependencies below, including atomic JSON persistence from 14.1.1 for
failure records, not completion of phases 13 or 14. Steps 15.6-15.8 are separate
extensions, not prerequisites for that plateau. This proposed phase does not add
a first-release gate.

### 15.1. Separate local and target execution environments

Outcome: the controller can prepare a container target without redirecting host
commands. All tasks in this step implement RFC 001 §Execution environment
contract and §Matching, concurrency, and callback environment.

- [ ] 15.1.1. Introduce the prepared-environment contract through the existing
  `environment=` injection seam, separating preparation, activation, and cleanup.
  - Success: existing local context-manager and environment-restoration tests
    pass without changes to their observable expectations.
- [ ] 15.1.2. Delegate launcher and endpoint preparation to environment
  capabilities and isolate container callback environment handling.
  - Requires 15.1.1.
  - Success: container preparation and callbacks leave host `PATH`, `PATHEXT`,
    working directory, and process environment unchanged; Linux, macOS, and
    Windows local-backend regression tests remain green.
- [ ] 15.1.3. Add explicit runner working-directory and execution-scope metadata
  with backward-compatible local serialization and command-set freezing.
  - Requires 15.1.1.
  - Success: host and runner paths remain distinct, local payloads and fixtures
    still load, and late container command registration fails explicitly.

### 15.2. Deliver the local Linux bridge

Outcome: a qualified local Linux container executes relocatable Python shims
against the host controller. All tasks implement RFC 001 §Resource layout and
endpoint mapping, §Relocatable launcher bundle, §Invocation and protocol contract,
and §Failure reporting and session lifecycle.

- [ ] 15.2.1. Build a relocatable Python launcher bundle with relative references,
  a manifest, and an absolute runner-interpreter setting.
  - Requires 15.1.2.
  - Success: shims work without a host installation mount; missing dependencies,
    Python below the package minimum, and interpreter recursion fail preflight.
- [ ] 15.2.2. Implement separate bundle, socket-directory, and status mounts with
  explicit host/runner endpoint mapping and resource ownership.
  - Requires 15.1.1.
  - Success: short socket allocation handles deep pytest paths; unsafe mounts,
    incompatible permissions, and unsupported daemon topologies fail clearly.
- [ ] 15.2.3. Add authenticated capability negotiation, bounded bridge messages,
  explicit invocation identities, and safe connection-retry semantics.
  - Requires 15.1.3, 15.2.1, and 15.2.2.
  - Success: incompatible peers, malformed payloads, oversized input, duplicate
    IDs, and ambiguous submissions cannot yield success or repeat consumption.
- [ ] 15.2.4. Persist and reconcile launcher start/completion/failure records
  independently of IPC, keeping controller failure state outside bounded logs.
  - Requires 15.2.3 and 14.1.1.
  - Success: lost sockets, corrupt or incomplete records, callback errors, and
    journal eviction still fail verification when the workflow catches errors.

### 15.3. Bind prepared environments to Act

Outcome: the existing pytest harness can configure the runner without adopting a
new workflow executor. All tasks implement RFC 001 §Act adapter and activation.

- [ ] 15.3.1. Implement a provisional `ActBinding` that produces validated Act
  arguments and explicit host/environment-file configuration.
  - Requires 15.2.1, 15.2.2, and 15.2.3.
  - Success: quoting tests cover unusual paths; conflicting container options,
    implicit credential files, ambient `.actrc` settings, and unsupported job
    graphs cannot silently change the requested session.
- [ ] 15.3.2. Generate the opt-in activation script and control-plane readiness
  check, including `$GITHUB_PATH` propagation and command-resolution validation.
  - Requires 15.3.1 and 15.2.4.
  - Success: the next workflow step resolves every exported double; missing
    activation fails even with only optional stubs; path-shadowing regression
    tests demonstrate explicit reactivation rather than assumed coverage.
- [ ] 15.3.3. Expose activation/completion diagnostics and owned-resource metadata
  without moving subprocess or artefact-server ownership into CmdMox core.
  - Requires 15.3.2.
  - Success: the guide's harness accepts a binding, while ordinary CmdMox imports
    and unit tests require neither Act nor a running container daemon.

### 15.4. Establish concurrency, cancellation, and isolation guarantees

Outcome: workflow success cannot hide bridge failures and parallel scenarios do
not share state. All tasks implement RFC 001 §Matching, concurrency, and callback
environment, §Failure reporting and session lifecycle, and §Security, portability,
and isolation.

- [ ] 15.4.1. Make matching, reservation, callback dispatch, and journal updates
  safe for concurrent commands, with explicit re-entry and ordering semantics.
  - Requires 15.1.2, 15.1.3, and 15.2.3.
  - Success: concurrent requests consume each expectation at most once; callback
    re-entry fails without deadlock; completion timing does not invent ordering.
- [ ] 15.4.2. Add bounded draining and stable verification, preserving workflow
  failures and mock failures together through context and pytest teardown.
  - Requires 15.2.4, 15.3.3, and 15.4.1.
  - Success: swallowed mismatches, unfinished invocations, failed drains, and
    omitted explicit health assertions still invalidate the test.
- [ ] 15.4.3. Extend the example harness with deadline-driven, ownership-scoped
  cancellation and cleanup of exact Act/runtime resources.
  - Requires 15.3.3 and 15.4.2.
  - Success: timeout and interruption tests leave no owned containers or sockets,
    preserve unrelated containers, and retain redacted failure diagnostics.
- [ ] 15.4.4. Isolate parallel worktrees, artefact/status paths, container
  identities, writable caches, and server-port allocation.
  - Requires 15.4.3.
  - Success: at least two simultaneous pytest workers pass independent Act
    scenarios without cross-session requests, port collisions, or leaked state.

### 15.5. Publish the first usable workflow-validation plateau

Outcome: users can test a real workflow with selected command doubles and clear
support boundaries. All tasks implement RFC 001 §Illustrative pytest integration
and §Validation and adoption.

- [ ] 15.5.1. Add a real Act `gh` workflow example with shell redirection and
  positive and negative behavioural acceptance tests.
  - Requires steps 15.1-15.4.
  - Success: tests verify arguments and produced JSON; the negative scenario
    fails pytest despite Act exiting zero after a swallowed mock failure.
- [ ] 15.5.2. Add a resource-bounded integration CI lane with pinned Act/image
  profiles, cached bundles, and redacted diagnostic artefacts.
  - Requires 15.5.1.
  - Success: daemon-independent unit tests remain fast and unchanged in scope;
    the dedicated lane exercises failures, isolation, and cleanup without live
    service credentials or per-scenario dependency builds.
- [ ] 15.5.3. Document the provisional APIs, troubleshooting, trust boundaries,
  validation ladder, and explicit unsupported topologies.
  - Requires 15.5.1 and 15.5.2.
  - Success: the usage guide names the supported profile and API status; a
    companion change extends agent-helper-scripts' local-validation guide
    without replacing its black-box tests or GitHub-runner certification.

### 15.6. Reuse native launchers and runner-local recordings

Outcome: optional native launchers and passthrough spies obey the same runner
contract. These tasks implement RFC 001 §Relocatable launcher bundle and
§Runner-local passthrough and fixture reuse; they do not gate step 15.5.

- [ ] 15.6.1. Extend phase 13 asset selection and packaging for explicit Linux
  runner targets, with bundle-level backend parity tests.
  - Requires 15.5.1, 13.2.1, 13.3.1, 13.3.2, and 13.4.1.
  - Success: Python/native acceptance scenarios agree; wrong-architecture or
    broken requested binaries fail clearly; scenarios do not compile Rust.
- [ ] 15.6.2. Enable runner-local passthrough using the invocation's effective
  path, explicit shim exclusions, runner `cwd`, and result acknowledgement.
  - Requires 15.5.1 and 15.4.1.
  - Success: a runner-only executable wins over a host decoy; new tool paths are
    retained; recursive overrides and ambiguous retries cannot re-execute it.
  - Native passthrough parity additionally requires 15.6.1 and 13.3.3.
- [ ] 15.6.3. Reuse recording/replay sessions with explicit workspace
  normalization, scrubbing, and fixture-consumption verification.
  - Requires 15.6.2, 12.1.4, 12.2.5, 12.2.6, 12.3.1, 12.3.3, and 12.3.5.
  - Success: a runner recording replays without live dependencies; ephemeral
    session data does not constrain matching; persisted fixtures redact secrets
    and unused recordings fail verification without an Act-only schema.

### 15.7. Add explicit mapped workspace effects

Outcome: host callbacks can intentionally create runner-visible workspace files
without confusing host and container-private paths. All tasks implement RFC 001
§Observable filesystem and workflow effects; they do not gate step 15.5.

- [ ] 15.7.1. Implement explicit runner-to-host workspace mappings with permitted
  roots, traversal rejection, and symlink-escape protection.
  - Requires 15.5.1, 15.1.3, and 14.5.1.
  - Success: a callback writes a runner-visible fixture through the declared
    mapping; unmapped/private paths and escapes fail without host side effects.
- [ ] 15.7.2. Add stateful fake examples using mapped effects and optional phase
  14 state helpers, with correct workflow-output and environment-file semantics.
  - Requires 15.7.1, 14.3.1, and 14.5.2.
  - Success: later runner commands observe intentional files/state, while tests
    show that response environment values cannot mutate a parent shell or later
    step; no arbitrary remote evaluation API is introduced.

### 15.8. Qualify additional execution profiles

Outcome: support expands only after explicit compatibility and security evidence.
All tasks implement RFC 001 §Security, portability, and isolation and §Delivery
plan and open decisions; they do not gate the initial Linux/Docker plateau.

- [ ] 15.8.1. Qualify a same-host rootless Podman profile, including user/group
  mappings, socket permissions, and SELinux volume-label behaviour.
  - Requires 15.5.2.
  - Success: the common acceptance/cleanup suite passes on the declared profile,
    or documentation retains a precise unsupported status without insecure
    permission workarounds.
- [ ] 15.8.2. Record separate adoption decisions for Docker actions, nested
  containers, remote/VM-backed daemons, and multi-job scope routing.
  - Requires 15.5.3.
  - Success: each proposed expansion identifies a concrete use case, transport
    and trust boundaries, propagation rules, and acceptance tests; none becomes
    supported implicitly through the job-container adapter.

<!-- markdownlint-disable-next-line MD036 -->
**Legend**

- Each unchecked box represents an implementable, trackable unit suitable for
  project management tools.
- [ ] All MVP checkboxes above this point should be completed before first
  public release. Proposed phase 15 is post-MVP and adds no release prerequisite.
