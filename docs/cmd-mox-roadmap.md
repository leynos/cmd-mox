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
- [x] 12.1.2. Implement `FixtureFile` with JSON serialisation, versioned schema
  (`1.0`), and migration support.
- [ ] 12.1.3. Add `.record()` to `CommandDouble` with validation that
  passthrough mode is enabled and support for custom scrubber and allowlist
  parameters.
- [ ] 12.1.4. Integrate recording into `PassthroughCoordinator.finalize_result()`
  with optional recording session wiring.
- [ ] 12.1.5. Add unit tests for recording lifecycle, serialisation roundtrips,
  and environment filtering.

### 12.2. Replay infrastructure

- [ ] 12.2.1. Implement `ReplaySession` with fixture loading, schema validation,
  consumed-record tracking, and strict and fuzzy modes.
- [ ] 12.2.2. Implement `InvocationMatcher` with strict matching, fuzzy
  matching, and best-fit score selection.
- [ ] 12.2.3. Add `.replay()` to `CommandDouble`, including passthrough
  incompatibility validation and strict-mode option.
- [ ] 12.2.4. Integrate replay into `CmdMox._make_response()` and raise
  `UnexpectedCommandError` for unmatched strict replay invocations.
- [ ] 12.2.5. Extend `CmdMox.verify()` to report unconsumed recordings.
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
- [ ] 12.5.4. Implement `cmdmox scrub` for post-hoc fixture sanitisation.
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
record-replay-verify semantics. See
`python-native-command-mocking-design.md` section 8.12.

### 13.1. Rust workspace and build foundation

- [ ] 13.1.1. Introduce Cuprum-style Rust structure with `rust/Cargo.toml`
  workspace root, `rust/cmdmox-mock/` binary crate, and `rust/Makefile`
  targets for native build, test, lint, and format checks.
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

## Legend

- Each unchecked box represents an implementable, trackable unit suitable for
  project management tools.
- [ ] All MVP checkboxes above this point should be completed before first
  public release.
