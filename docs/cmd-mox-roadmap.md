# CmdMox Implementation Roadmap

## **I. Project Foundation & Infrastructure**

- [x] **Establish Core Repository Structure**

  - [x] Create project skeleton (`cmd_mox/`, `tests/`, `conftest.py`, etc.)

  - [x] Set up packaging, CI, linting, type-checking (e.g., `pyproject.toml`,
    `pytest`, `ruff`, `mypy`)

- [x] **Initial Documentation**

  - [X] Draft README with basic usage and conceptual overview

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

- [ ] **Shim Startup Logic**

  - [ ] Determine which command to simulate via `argv[0]`

  - [ ] Connect to IPC socket

  - [ ] Capture stdin, argv, env

  - [ ] Send invocation to IPC server, wait for response

  - [ ] Apply returned behaviour: print `stdout`, `stderr`, exit with code

- [ ] **Passthrough Spies**

  - [ ] IPC protocol for server to direct shim to run "real" command

  - [ ] Shim locates real command in original `PATH`, executes, sends result

## **VII. Advanced Features & Edge Cases**

- [ ] **Environment Variable Injection**

  - [ ] `.with_env()` applies mock-specific env before executing the handler or
    canned response

- [ ] **Concurrency Support**

  - [ ] Safe parallel use: unique per-test temp dirs, socket names, no shared
    files

- [ ] **Robust Cleanup**

  - [ ] Always restore env and remove temp dirs/sockets, even on error/interrupt

## **VIII. Documentation, Examples & Usability**

- [ ] **API Reference & Tutorials**

  - [ ] Document all public APIs and matchers

  - [ ] Example tests for stubs, mocks, spies, pipelines, passthrough mode

  - [ ] Comparison/migration guide for `shellmock` users

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

## **XI. Windows and Record Mode**

- [ ] **Cross-Platform IPC Abstraction**

  - [ ] **Research and select a Windows-compatible IPC mechanism.** The current
        implementation uses Unix domain sockets, which are not available on
        Windows. The design document suggests **named pipes** as a likely
        replacement.

  - [ ] **Refactor `IPCServer` and `invoke_server`** to use a platform-agnostic
        interface, with separate implementations for Unix domain sockets and the
        chosen Windows IPC mechanism. This will likely involve conditional logic
        based on `os.name`.

- [ ] **Windows-Compatible Shim Generation**

  - [ ] **Adapt the shim generation engine for Windows.** The current engine
        creates POSIX-style executable shims and uses symbolic links, which are
        not directly portable to Windows.

  - [ ] **Implement `.bat` or `.cmd` shim creation.** These batch files will
        serve as the entry point for mocked commands and will be responsible for
        invoking the Python interpreter with the `shim.py` script.

  - [ ] **Update `CommandRunner` for Windows.** The logic for finding and
        executing real commands in passthrough mode will need to account for
        Windows executable paths and file extensions (e.g., `.exe`, `.bat`,
        `.cmd`).

- [ ] **Environment and Filesystem Abstractions**

  - [ ] **Verify `EnvironmentManager` behaviour on Windows.** While it already
        uses `os.pathsep`, thorough testing is needed to ensure that `PATH`
        manipulation works as expected on Windows.

  - [ ] **Review and adapt filesystem interactions.** Any remaining
        Unix-specific filesystem operations will need to be made cross-platform,
        likely by expanding the use of `pathlib`. The existing
        `_fix_windows_permissions` function in `cmd_mox/environment.py` is a
        good starting point for this effort.

- [ ] **CI and Testing Infrastructure**

  - [ ] **Add Windows runners to the CI pipeline.** The GitHub Actions workflows
        will need to be extended to run the test suite on Windows machines to
        catch platform-specific regressions.

  - [ ] **Develop a comprehensive test suite for Windows.** This should cover
        all aspects of the library's functionality, from basic stubbing to
        advanced passthrough spying.

- [ ] **Documentation Updates**

  - [ ] **Update the usage guide and design documents** to reflect the new
        Windows support, including any platform-specific limitations or
        configuration requirements.

- [ ] **Record Mode Tooling**

  - [ ] **Prototype a test generation utility.** This tool should transform
        passthrough spy recordings into pytest test files that can be refined
        into repeatable mocks.

  - [ ] **Serialise recorded interactions for reuse.** Provide a mechanism to
        export captured real command interactions and ingest them as reusable
        mocks in future sessions.

**Legend:**

- Each `[ ]` is an implementable, trackable unit (suitable for tickets/epics in
  e.g. GitHub Projects, Jira, etc.)

- [ ] All MVP checkboxes above this point should be completed before first
  public release.
