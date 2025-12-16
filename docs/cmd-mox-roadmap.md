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

## **Future/Epic XI. Windows Platform Support & Record Mode**

- [ ] **Windows Platform Enablement**

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

- [ ] **Record Mode Evolution**

  - [ ] Provide tooling that turns passthrough recordings into reusable
        fixtures or tests.

  - [ ] Support persisting recorded interactions for later reuse within
        CmdMox sessions.

**Legend:**

- Each `[ ]` is an implementable, trackable unit (suitable for tickets/epics in
  e.g. GitHub Projects, Jira, etc.)

- [ ] All MVP checkboxes above this point should be completed before first
  public release.
