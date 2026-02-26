# CmdMox Implementation Roadmap

## **I. Project Foundation & Infrastructure**

- [x] **Establish Core Repository Structure**

  - [x] Create project skeleton (`cmd_mox/`, `tests/`, `conftest.py`, etc.)

  - [x] Set up packaging, CI, linting, type-checking (e.g., `pyproject.toml`,
    `pytest`, `ruff`, `mypy`)

- [x] **Initial Documentation**

  - [x] Draft README with basic usage and conceptual overview

  - [x] Add this design spec as `docs/python-native-command-mocking-design.md`

## **II. Core Components: Environment & IPC**

- [x] **Environment Manager**

  - [x] Context manager to save/restore environment (`PATH`, etc.)

  - [x] Temporary shim directory creation/cleanup

  - [x] Environment variable for IPC socket path

- [x] **Shim Generation Engine**

  - [x] Single Python `shim.py` script (template)

  - [x] Logic to create per-command symlinks in temp directory

  - [x] Symlink invokes correct behaviour based on `argv[0]`

  - [x] Make shims executable on all supported Unix platforms

- [x] **IPC Bus (Unix Domain Sockets)**

  - [x] Lightweight IPC server in main test process

  - [x] Start/stop server in sync with test lifecycle

  - [x] Structured (JSON) communication: invocation/request, response/result

  - [x] Timeout/error handling and robust socket cleanup

## **III. CmdMox Controller & Public API**

- [x] `CmdMox` **Controller Class**

  - [x] Holds expectations, stubs, spies, and the invocation journal

  - [x] Manages lifecycle: record → replay → verify

- [x] **Factory Methods**

  - [x] `cmd_mox.mock(cmd)`, `cmd_mox.stub(cmd)`, `cmd_mox.spy(cmd)`

- [x] **Pytest Plugin**

  - [x] Register `cmd_mox` fixture

  - [x] xdist/parallelisation awareness (worker IDs, temp dirs)

- [x] **Context Manager Interface**

  - [x] Support for explicit `with cmd_mox.CmdMox() as mox:` usage

## **IV. Command Double Implementations**

- [x] **StubCommand**

  - [x] `.returns()` and `.runs()` (static/dynamic behaviour)

  - [x] No verification/required calls

- [x] **MockCommand** *(done)*

  - [x] `.with_args()`, `.with_matching_args()`, `.with_stdin()`

  - [x] `.returns()`, `.runs()`

  - [x] `.times()`, `.in_order()`, `.any_order()`

  - [x] `.with_env()` for environment matching/injection

  - [x] Strict record-replay-verify logic

- [x] **SpyCommand**

  - [x] `.returns()` for canned response

  - [x] `.passthrough()` for record/replay mode

  - [x] Maintain call history (`invocations`, `call_count` API)

### Fluent API Enhancements

- [x] Fluent expectation DSL (see
    [design section](python-native-command-mocking-design.md#24-the-fluent-api-for-defining-expectations)
    )
- [x] Assertion helpers for spy inspection mirroring `unittest.mock`
    semantics (`assert_called`, `assert_called_with`, `assert_not_called`)

## **V. Matching & Verification Engine**

- [x] **Comparator Classes**

  - [x] Implement: `Any`, `IsA`, `Regex`, `Contains`, `StartsWith`, `Predicate`

  - [x] Flexible matcher plumbing in mock argument matching

- [x] **Invocation Journal**

- [x] Capture: command, args, stdin, env, stdout, stderr, exit_code per
  invocation

  - [x] Store as a deque to preserve order during verification
  - [x] Configurable max journal size via `max_journal_entries`

- [x] **Verification Algorithm**

  - [x] Unexpected calls (fail early)

  - [x] Unfulfilled expectations

  - [x] Call counts, strict ordering checks

  - [x] Clear diff-style error reporting (`VerificationError` etc.)

## **VI. Shim Behaviour**

- [x] **Shim Startup Logic**

  - [x] Determine which command to simulate via `argv[0]`

  - [x] Connect to IPC socket

  - [x] Capture stdin, argv, env

  - [x] Send invocation to IPC server, wait for response

  - [x] Apply returned behaviour: print `stdout`, `stderr`, exit with code

- [x] **Passthrough Spies**

  - [x] IPC protocol for server to direct shim to run "real" command

  - [x] Shim locates real command in original `PATH`, executes, sends result

## **VII. Advanced Features & Edge Cases**

- [x] **Environment Variable Injection**

  - [x] `.with_env()` applies mock-specific env before executing the handler or
    canned response

- [x] **Concurrency Support**

  - [x] Safe parallel use: unique per-test temp dirs, socket names, no shared
    files

- [x] **Robust Cleanup**

  - [x] Always restore env and remove temp dirs/sockets, even on error/interrupt

## **VIII. Documentation, Examples & Usability**

- [ ] **API Reference & Tutorials**

  - [x] Document all public APIs and matchers *(done)*

  - [x] Example tests for stubs, mocks, spies, pipelines, passthrough mode

  - [x] Comparison/migration guide for `shellmock` users

## **IX. Quality Assurance**

- [ ] **Unit & Integration Testing**

  - [ ] Full test coverage for all core components (especially IPC and env
    manipulation)

  - [ ] Tests for pytest-xdist compatibility

  - [ ] Regression suite for edge cases (pipelines, missing commands, complex
    args)

  - [ ] Behavioural acceptance tests covering the full fluent API using
        `pytest-bdd` and `cfparse`

## **X. Release & Post-MVP**

- [ ] **First Public Release (1.0.0)**

  - [ ] Polish docs, clean up, push to PyPI

  - [ ] Announce project, collect early user feedback

## **XI. Windows Platform Support**

- [x] **Windows Platform Enablement**

  - [x] Establish cross-platform IPC and shim abstractions that include Windows
        implementations (acceptance: end-to-end pytest suite passes on
        `windows-latest` GH runner with IPC + shims enabled).

  - [x] Implement Windows named-pipe IPC (win32pipe/win32file) and package the
        pywin32 dependency so CmdMox can communicate with shims on Windows
        hosts without relying on Unix domain sockets.

  - [x] Validate environment management and filesystem helpers on Windows,
        addressing portability gaps discovered during testing (cover: `PATHEXT`
        lookup semantics, CRLF line endings for batch shims, argument
        quoting/escaping rules with spaces and carets, max path handling,
        case-insensitive filesystem behaviour).

  - [x] Extend CI and automated testing to exercise core workflows on Windows
        (`windows-latest` matrix job; minimal smoke: create shims, run mocked
        command, run passthrough spy; artefacts include IPC logs for debugging).

## **XII. Record Mode**

Record Mode transforms passthrough spy recordings into reusable test fixtures,
enabling developers to capture real command interactions and replay them in
subsequent test runs without external dependencies. The comprehensive design is
documented in `python-native-command-mocking-design.md` Section IX.

### **XII-A. Core Recording Infrastructure (MVP)**

- [x] Implement `RecordingSession` class with fixture persistence

  - [x] Session lifecycle management (start, record, finalize)
  - [x] Fixture metadata generation (timestamps, platform, versions)
  - [x] Environment variable subset filtering

- [x] Implement `FixtureFile` with JSON serialization and schema versioning

  - [x] Version 1.0 schema with recordings, metadata, and scrubbing rules
  - [x] `to_dict()` and `from_dict()` serialization methods
  - [ ] Schema migration support for forward compatibility

- [ ] Add `.record()` method to `CommandDouble`

  - [ ] Fluent API: `spy("git").passthrough().record("fixtures/git.json")`
  - [ ] Validation that passthrough mode is enabled
  - [ ] Support for custom scrubber and env_allowlist parameters

- [ ] Integrate recording into `PassthroughCoordinator.finalize_result()`

  - [ ] Optional `RecordingSession` parameter on coordinator
  - [ ] Automatic recording on passthrough completion

- [ ] Unit tests for recording workflow

  - [ ] Session lifecycle tests
  - [ ] Serialization roundtrip tests
  - [ ] Environment filtering tests

### **XII-B. Replay Infrastructure**

- [ ] Implement `ReplaySession` class with fixture loading

  - [ ] Load and parse fixture files with schema validation
  - [ ] Track consumed recordings during replay
  - [ ] Support both strict and fuzzy matching modes

- [ ] Implement `InvocationMatcher` for invocation matching

  - [ ] Strict matching (command, args, stdin, env)
  - [ ] Fuzzy matching (command and args only)
  - [ ] Match scoring for best-fit selection

- [ ] Add `.replay()` method to `CommandDouble`

  - [ ] Fluent API: `spy("git").replay("fixtures/git.json")`
  - [ ] Validation that passthrough mode is not enabled
  - [ ] Support for strict parameter

- [ ] Integrate replay into `CmdMox._make_response()`

  - [ ] Check replay session before other response strategies
  - [ ] Raise `UnexpectedCommandError` on unmatched strict replay

- [ ] Add replay consumption verification to `CmdMox.verify()`

  - [ ] Verify all recordings were consumed
  - [ ] Report unconsumed recordings in verification errors

- [ ] Unit tests for replay workflow

  - [ ] Fixture loading tests
  - [ ] Matching algorithm tests
  - [ ] Consumption tracking tests

### **XII-C. Scrubbing and Security**

- [ ] Implement `Scrubber` class with default rules for common secrets

  - [ ] GitHub PATs (`ghp_*`, `gho_*`, etc.)
  - [ ] AWS access keys (`AKIA*`)
  - [ ] Generic API keys and tokens
  - [ ] Bearer authorization headers
  - [ ] SSH private keys
  - [ ] Database connection strings

- [ ] Implement `ScrubbingRule` dataclass

  - [ ] Pattern (string or compiled regex)
  - [ ] Replacement string
  - [ ] Applied-to fields (env, stdout, stderr, stdin)
  - [ ] Description for documentation

- [ ] Add environment variable filtering

  - [ ] Default exclusion list (PATH, HOME, `*_KEY`, `*_SECRET`, etc.)
  - [ ] Configurable allowlist
  - [ ] Command-specific prefix matching (`GIT_*`, `AWS_*`, etc.)

- [ ] Implement review mode for manual verification

  - [ ] Generate companion `.review` file
  - [ ] Show original alongside scrubbed values
  - [ ] Include warnings for potentially sensitive data

- [ ] Security-focused unit tests

  - [ ] Pattern matching tests for all default rules
  - [ ] Environment filtering tests
  - [ ] Review file generation tests

### **XII-D. Pytest Integration**

- [ ] Add `@pytest.mark.cmdmox_record` marker

  - [ ] Automatic fixture directory configuration
  - [ ] Convention-based fixture naming (`<test_name>_<command>.json`)
  - [ ] Integration with existing `cmd_mox` fixture

- [ ] Add `@pytest.mark.cmdmox_replay` marker

  - [ ] Automatic fixture loading from configured directory
  - [ ] Strict/fuzzy mode configuration via marker parameter
  - [ ] Error reporting for missing fixtures

- [ ] Implement automatic fixture naming convention

  - [ ] Based on test module and function names
  - [ ] Support for custom naming via marker parameters
  - [ ] Collision detection and handling

- [ ] Ensure pytest-xdist compatibility for recording

  - [ ] Worker-isolated fixture paths
  - [ ] Concurrent recording safety
  - [ ] Fixture aggregation for parallel runs

### **XII-E. CLI Tool**

- [ ] Implement `cmdmox record` subcommand

  - [ ] `--output` for fixture path
  - [ ] `--commands` for selective recording
  - [ ] Execute target script with recording enabled

- [ ] Implement `cmdmox replay` subcommand

  - [ ] `--fixture` for fixture path
  - [ ] `--strict` mode flag
  - [ ] Execute target script with replay enabled

- [ ] Implement `cmdmox generate-test` for code generation

  - [ ] Generate pytest test from recorded fixture
  - [ ] Include mock definitions for all recordings
  - [ ] Support for output path configuration

- [ ] Implement `cmdmox scrub` for post-hoc sanitization

  - [ ] `--fixture` for input fixture
  - [ ] `--rules` for custom rules file (YAML)
  - [ ] In-place or output to new file

- [ ] Implement `cmdmox validate` for fixture verification

  - [ ] Schema validation against version
  - [ ] Report invalid or corrupt fixtures
  - [ ] Support for glob patterns

### **XII-F. Documentation and Examples**

- [ ] API reference documentation

  - [ ] `RecordingSession` class documentation
  - [ ] `ReplaySession` class documentation
  - [ ] `Scrubber` and `ScrubbingRule` documentation
  - [ ] `FixtureFile` schema documentation

- [ ] Tutorial: Recording a fixture

  - [ ] Step-by-step guide for passthrough recording
  - [ ] Example with git commands
  - [ ] Explanation of fixture format

- [ ] Tutorial: CI/CD replay workflows

  - [ ] Using recorded fixtures in CI
  - [ ] Managing fixture updates
  - [ ] Best practices for fixture versioning

- [ ] Migration guide from raw passthrough

  - [ ] Converting existing passthrough tests
  - [ ] Benefits of fixture-based testing
  - [ ] Common patterns and anti-patterns

**Legend:**

- Each `[ ]` is an implementable, trackable unit (suitable for tickets/epics in
  e.g. GitHub Projects, Jira, etc.)

- [ ] All MVP checkboxes above this point should be completed before first
  public release.
