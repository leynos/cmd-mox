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

- [ ] `CmdMox` **Controller Class**

  - [ ] Holds expectations, stubs, spies, and the invocation journal

  - [ ] Manages lifecycle: record → replay → verify

- [ ] **Factory Methods**

  - [ ] `mox.mock(cmd)`, `mox.stub(cmd)`, `mox.spy(cmd)`

- [ ] **Pytest Plugin**

  - [ ] Register `cmd_mox` fixture

  - [ ] xdist/parallelisation awareness (worker IDs, temp dirs)

- [ ] **Context Manager Interface**

  - [ ] Support for explicit `with cmd_mox.CmdMox() as mox:` usage

## **IV. Command Double Implementations**

- [ ] **StubCommand**

  - [ ] `.returns()` and `.runs()` (static/dynamic behaviour)

  - [ ] No verification/required calls

- [ ] **MockCommand**

  - [ ] `.with_args()`, `.with_matching_args()`, `.with_stdin()`

  - [ ] `.returns()`, `.runs()`

  - [ ] `.times()`, `.in_order()`, `.any_order()`

  - [ ] `.with_env()` for environment matching/injection

  - [ ] Strict record-replay-verify logic

- [ ] **SpyCommand**

  - [ ] `.returns()` for canned response

  - [ ] `.passthrough()` for record/replay mode

  - [ ] Maintain call history (`invocations`, `call_count` API)

### Fluent API Enhancements

- [ ] Fluent expectation DSL (see [design section](python-native-command-
    mocking-design.md#24-the-fluent-api-for-defining-expectations))
- [ ] Assertion helpers for spy inspection mirroring `unittest.mock`
    semantics (`assert_called`, `assert_called_with`)

## **V. Matching & Verification Engine**

- [ ] **Comparator Classes**

  - [ ] Implement: `Any`, `IsA`, `Regex`, `Contains`, `StartsWith`, `Predicate`

  - [ ] Flexible matcher plumbing in mock argument matching

- [ ] **Invocation Journal**

  - [ ] Capture: command, args, stdin, env per invocation

  - [ ] Store as list or deque, preserve order for verify

- [ ] **Verification Algorithm**

  - [ ] Unexpected calls (fail early)

  - [ ] Unfulfilled expectations

  - [ ] Call counts, strict ordering checks

  - [ ] Clear diff-style error reporting (`VerificationError` etc.)

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
        `behave` and `cfparse`

## **X. Release & Post-MVP**

- [ ] **First Public Release (1.0.0)**

  - [ ] Polish docs, clean up, push to PyPI

  - [ ] Announce project, collect early user feedback

## **XI. Future/Epic: Windows and Record Mode**

- [ ] **Windows Support**

  - [ ] Prototype `.bat`/`.exe` shims, named pipes IPC, Windows PATH handling

- [ ] **Test Generation Utility**

  - [ ] Prototype tool for automatic pytest test file generation from
    passthrough spy recordings

  - [ ] Export/serialise real command interactions to reusable mocks

**Legend:**

- Each `[ ]` is an implementable, trackable unit (suitable for tickets/epics in
  e.g. GitHub Projects, Jira, etc.)

- [ ] All MVP checkboxes above this point should be completed before first
  public release.
