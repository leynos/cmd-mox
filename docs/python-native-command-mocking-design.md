# `CmdMox`: A Technical Design Specification for Python-Native Command Mocking

## I. Conceptual Framework: Unifying PyMox and Shell Command Mocking

This document presents the technical design for `CmdMox`, a Python library for
stubbing, mocking, and spying on external commands in Unix-like environments.
The primary objective is to provide a robust, ergonomic, and Python-native
alternative to shell-based testing frameworks like BATS and `shellmock`. The
design prioritizes a fluent API modeled on the PyMox framework, combined with a
powerful and reliable command interception mechanism. This section establishes
the core principles and architectural philosophy that underpin the library's
design.

### 1.1 The "Record-Replay-Verify" Paradigm for External Processes

The foundational testing paradigm adopted by `CmdMox` is
"Record-Replay-Verify," a disciplined workflow popularized by the EasyMock
framework for Java and its Python counterpart, PyMox. This paradigm structures
tests into three distinct, sequential phases, enforcing clarity and
explicitness about a system's interactions with its dependencies.

1. **Record Phase:** In this initial phase, the developer uses the `CmdMox` API
   to declaratively define a set of expectations. An expectation is a precise
   description of a single external command invocation, including the command
   name, its arguments, expected standard input, and the behavior it should
   exhibit (e.g., its `stdout`, `stderr`, and exit code). This is the setup
   portion of the test, where the "script" for the test doubles is written.

2. **Replay Phase:** Once all expectations are recorded, the test transitions
   the framework into the replay phase. During this phase, the system or
   component under test is executed. Any attempt to invoke an external command
   is intercepted by `CmdMox`. The framework consults the recorded expectations
   to find one that matches the actual invocation. If a match is found,
   `CmdMox` provides the specified behavior. If no match is found, it is
   treated as an unexpected interaction, which will cause the test to fail
   during the final phase.

3. **Verify Phase:** After the code under test has completed its execution, the
   test enters the final verification phase. In this phase, `CmdMox` checks
   whether the actual invocations that occurred during the replay phase
   perfectly match the expectations set during the record phase. The test
   succeeds only if every recorded expectation was met exactly as specified
   (including call counts and ordering) and no unexpected command calls
   occurred. Any deviation results in a `VerificationError` with a detailed
   report of the discrepancy.

Adopting this strict paradigm for command-line testing offers significant
advantages. It forces developers to be deliberate about the external process
dependencies of their applications. Unlike more lenient mocking styles, it
prevents "dependency creep" by immediately failing a test if an unexpected
command is called. The resulting tests are highly self-documenting; the record
phase serves as a clear specification of the application's external
interactions.

<!-- markdownlint-disable MD013 -->

### 1.2 The Core Architectural Principle: Command Interception via Dynamic `PATH` Manipulation

<!-- markdownlint-enable MD013 -->

The fundamental mechanism for intercepting command invocations will be the
dynamic manipulation of the `PATH` environment variable. This is a well-
established and reliable technique employed by a wide array of shell-based
mocking tools to redirect command calls.

The process works as follows:

- At the beginning of a test run, `CmdMox` creates a temporary, securely-named
  directory (e.g., within `/tmp`).

- For each command that needs to be mocked (e.g., `git`, `ls`, `curl`), the
  library creates a corresponding executable file, or "shim," inside this
  temporary directory.

- The absolute path of this temporary directory is then prepended to the `PATH`
  environment variable of the test process.

- When the code under test attempts to execute a command like `git`, the
  operating system's standard library functions for finding executables (such
  as `execvp`) search the directories listed in `PATH` in order. The OS will
  find the `CmdMox`-generated shim in the temporary directory before it finds
  the real system command in a standard location like `/usr/bin/git`.

- This shim then becomes the entry point for the mocked behavior.

- Upon test completion (or failure), the `PATH` variable is restored to its
  original state, and the temporary directory and all its shims are removed,
  ensuring no side effects linger beyond the test's execution.

While the `PATH` hijacking technique is common, `CmdMox` introduces a critical
architectural improvement over existing tools. Frameworks like `shellmock` and
`bats-mock` generate *shell scripts* to act as shims. These shell shims are
inherently limited. To communicate invocation details back to the main test
runner process, they must resort to writing to temporary log files (e.g.,
`shellmock.out`, `*.playback.capture.tmp`). The test runner then has the
brittle and inefficient task of parsing these flat text files to verify the
interactions. This approach struggles with structured data, concurrency, and
complex quoting or argument-passing scenarios.

`CmdMox` will instead generate lightweight *Python scripts* as its shims. This
design choice unlocks a far more powerful and robust implementation. A Python
shim can leverage the full breadth of Python's standard library for
sophisticated Inter-Process Communication (IPC). Instead of writing to fragile
log files, the shim can communicate with the main `CmdMox` test process over a
dedicated, high-performance channel like a Unix domain socket. This allows for
the bidirectional transfer of rich, structured data (e.g., JSON-serialized
objects representing the command, its arguments, environment, and `stdin`
content). This architectural decision elevates `CmdMox` beyond the capabilities
of its shell-based predecessors, enabling more complex features, better
performance, and greater reliability, as detailed in Section III.

### 1.3 Defining the Terminology: Stubs, Mocks, and Spies

To ensure clarity, `CmdMox` will adopt precise definitions for its test
doubles, based on established software testing theory and adapted for the
context of external commands.

- **Stub:** A stub is a simple, "fire-and-forget" replacement for a command. Its
  purpose is to provide a fixed, canned response to a command invocation to
  allow the code under test to proceed. For example, a test might stub the
  `git` command to always return a successful exit code. A stub does not
  perform any verification; if the stubbed command is never called, the test
  still passes. Stubs are used to satisfy a dependency, not to test an
  interaction.

- **Mock:** A mock is a "verifiable" replacement for a command. Like a stub, it
  provides a defined behavior. However, a mock also carries a set of strict
  expectations about how it must be invoked. These expectations can include the
  exact arguments, the number of times it should be called, and the order of
  invocation relative to other mocks. The `verify()` phase of the test will
  fail if these expectations are not met precisely. Mocks are used to test that
  the system under test interacts with its dependencies *in a specific, correct
  way*. This aligns with the strict philosophy of PyMox.

- **Spy:** A spy is an "observational" test double. A spy wraps a command to
  record all invocations made to it, including arguments, `stdin`, and
  environment variables. A spy can either be configured with a stubbed behavior
  or it can "passthrough" and execute the real underlying command. After the
  test run, the developer can inspect the spy's recorded history to make
  assertions about how the command was used. This provides a more flexible,
  `assert`-based verification style, similar to the functionality found in
  tools like `bash_placebo`, and is useful when the exact sequence or number of
  calls is not known beforehand or is not the primary subject of the test.

## II. The Public API: A `CmdMox` User's Guide

The design of the `CmdMox` public API is paramount, with a primary goal of
providing an ergonomic, intuitive, and "Pythonic" user experience. The API is
heavily inspired by the fluent, chainable Domain-Specific Language (DSL) of the
modern PyMox fork, particularly its "New Elegant Way" of integration with
testing frameworks. This section serves as the definitive contract for how a
developer will interact with the library.

### 2.1 The `CmdMox` Controller: The Central Orchestrator

The central class and primary user entry point for the library will be
`cmd_mox.CmdMox`. An instance of this class encapsulates the entire state for a
single test case, including all recorded expectations, the invocation journal,
lifecycle and environment management context. It is analogous to the `mox.Mox`
class in PyMox and is responsible for orchestrating the record-replay-verify
lifecycle.

### 2.2 Ergonomic Integrations: `pytest` Fixtures and Context Managers

To minimize boilerplate and promote best practices, the library will offer
seamless integration with modern Python testing workflows.

**Primary Interface:** `pytest` **Fixture**

The recommended and primary method for using `CmdMox` will be through a
`pytest` fixture. This aligns with the "New Elegant Way" promoted by PyMox and
the broader Python testing ecosystem. Users will enable the plugin, and a
`cmd_mox` fixture will be automatically available to their test functions. This
fixture provides a fresh, properly configured `cmd_mox.CmdMox` instance for
each test, with setup and teardown handled automatically. The fixture enters
replay mode before the test body executes and calls `verify()` during teardown,
removing the need for explicit lifecycle calls in most test code. Teams can opt
out globally with the ``cmd_mox_auto_lifecycle`` pytest.ini flag, override the
setting for a single run via ``--cmd-mox-auto-lifecycle`` or
``--no-cmd-mox-auto-lifecycle``, and apply per-test overrides with
``@pytest.mark.cmd_mox(auto_lifecycle=False)`` when manual lifecycle control is
required.

*Example Usage:*
```python
# In conftest.py
pytest_plugins = ("cmd_mox.pytest_plugin",)

# In test_my_cli_tool.py
def test_git_clone_functionality(cmd_mox):
    # The 'cmd_mox' fixture is a ready-to-use cmd_mox.CmdMox instance.
    # Record phase:
    cmd_mox.mock('git').with_args('clone', 'https://a.b/c.git').returns(exit_code=0)

    result = my_cli_tool.clone_repo('https://a.b/c.git')

    # Assertions on the code under test:
    assert result is True
```

#### Alternative Interface: Context Manager

For use cases outside of `pytest` or when more explicit control is desired, a
standard Python context manager will be provided. The context manager ensures
that the environment is correctly set up on entry and, critically, that it is
torn down and restored on exit, even in the case of an exception.

*Example Usage:*
```python
import cmd_mox
import subprocess

with cmd_mox.CmdMox() as mox:
    mox.stub('ls').with_args('-l').returns(stdout='total 0')
    mox.replay()

    # The PATH is now modified within this block.
    output = subprocess.check_output(['ls', '-l'], text=True)
    assert output == 'total 0'
# Exiting the block automatically calls ``mox.verify()`` and restores PATH
```

`__enter__` delegates to :class:`EnvironmentManager`, ensuring the PATH and IPC
variables are set up. By default `__exit__` invokes :meth:`verify`, stopping
any running server and restoring the original environment. This behaviour can
be disabled via `CmdMox(verify_on_exit=False)` when manual control is required.

On Windows the manager also normalises the shim directory path before
publishing it inside `PATH`. Extremely deep worktrees can exceed the historical
`MAX_PATH` limit, so the manager requests the filesystem's short (8.3) alias
whenever the expanded path approaches that threshold. Comparisons performed
during teardown normalise casing via `ntpath.normcase`, ensuring that
reassigning `environment.shim_dir` with a differently cased string still points
at the same directory and allows `__exit__` to clean up after itself. PATH
filtering and passthrough spies reuse the same normalisation routines so a
single shim directory never appears twice just because its casing changed.

### 2.3 Creating Test Doubles: `mox.mock()`, `mox.stub()`, and `mox.spy()`

The `CmdMox` controller instance provides three distinct factory methods for
creating the different types of test doubles, each returning a chainable object
for further configuration.

- `mox.mock(command_name: str) -> MockCommand`: Creates a strict `MockCommand`
  object. This is used for interactions that must be verified. The returned
  object is used to build up a complete expectation using the fluent API.

- `mox.stub(command_name: str) -> StubCommand`: Creates a `StubCommand` object.
  This is used to provide canned responses for dependencies that are not the
  focus of the test.

- `mox.spy(command_name: str) -> SpyCommand`: Creates a `SpyCommand` object.
  This is used to record invocations for later inspection without the strict
  upfront expectations of a mock.

Early iterations of the library exposed distinct `StubCommand`, `MockCommand`
and `SpyCommand` classes. These have since been unified into a single
`CommandDouble` implementation tagged with a `kind` attribute. The factories
`mox.stub()`, `mox.mock()` and `mox.spy()` still exist for ergonomics but
internally return `CommandDouble` instances. Each double tracks invocations so
verification can assert on call counts and order. Mocks and spies record calls
(`double.is_recording` is `True`), while stubs do not.

The `kind` flag determines whether a double is considered an expectation (stubs
and mocks) or merely observational (spies). It also governs how
`CmdMox.verify()` checks the journal for unexpected or missing commands.

### 2.4 The Fluent API for Defining Expectations

The core of the library's ergonomic design lies in its fluent, chainable API
for defining the behavior and expectations of test doubles. This DSL allows
developers to write clear, readable, and expressive test setups, drawing direct
inspiration from PyMox's method-chaining style.

- `.with_args(*args: str)`**:** Specifies the exact, ordered list of
  command-line arguments that are expected. The match must be precise.

- `.with_matching_args(*comparators)`**:** For more flexible matching, this
  method accepts a sequence of `CmdMox` comparator objects (see Section V).
  This allows for matching based on type, regular expressions, or custom logic.

- `.with_stdin(data: Union[str, bytes, Comparator])`**:** Defines an expectation
  for the data that is piped to the command's standard input. This can be a
  literal string or bytes, or a comparator for flexible matching.

<!-- markdownlint-disable MD013 -->

- `.returns(stdout: Union[str, bytes] = b'', stderr: Union[str, bytes] = b'',
  exit_code: int = 0)`**:**

<!-- markdownlint-enable MD013 -->

Specifies the static result of the command invocation. The mock will write the
given `stdout` and `stderr` to the corresponding streams and exit with the
provided `exit_code`.

- `.runs(handler: Callable)`**:** Provides a powerful mechanism for dynamic
  behavior. The `handler` is a Python callable that will be executed by the
  `CmdMox` framework when the mock is invoked. The callable receives a
  structured `Invocation` object containing details of the call (args, `stdin`,
  env). The handler must return a tuple of `(stdout, stderr, exit_code)` using
  UTF-8–decoded strings. Handlers may return raw bytes; these are decoded as
  UTF-8 before the values are recorded on the `Invocation` for journal
  inspection. This significantly enhances PyMox's callback feature, enabling
  stateful mocks and complex conditional logic.

- `.times(count: int)`**:** Specifies the exact number of times this specific
  expectation is expected to be met. This is inspired by PyMox's
  `MultipleTimes()` modifier.

- `.in_order()`**:** Marks this expectation as part of a default, strictly
  ordered group. Invocations must occur in the order they were recorded. An
  `.any_order()` modifier can be provided for expectations where the calling
  order is not significant, mirroring PyMox's behavior.

Implementation note: the concrete implementation stores the expected call count
in a `count` attribute but exposes a `times()` convenience method to match the
DSL described here. The more explicit `times_called()` alias remains available
for readability when desired.

To ensure `CmdMox` is a compelling replacement for `shellmock`, the following
table maps the core features of `shellmock` to their more expressive `CmdMox`
API equivalents, demonstrating complete functional parity.

**Table 1:** `shellmock` **to** `CmdMox` **Feature Mapping** <!-- markdownlint-
disable MD013 -->

| shellmock Feature (from)                    | Proposed CmdMox API Equivalent                     |
| ------------------------------------------- | -------------------------------------------------- |
| Mock an executable cmd                      | mock_cmd = mox.mock('cmd')                         |
| Define behavior for specific args (--match) | mock_cmd.with_args('arg1', 'arg2')                 |
| Define exit code (--status \<exit_code>)    | mock_cmd.returns(exit_code=\<exit_code>)           |
| Define stdout (--output \<string>)          | mock_cmd.returns(stdout=\<string>)                 |
| Partial argument matching (--type partial)  | mock_cmd.with_matching_args(Contains('arg'))       |
| Regex argument matching (--type regex)      | mock_cmd.with_matching_args(Regex(r'--file=\\S+')) |
| Match on stdin (--match-stdin)              | mock_cmd.with_stdin('some input')                  |
| Custom behavior (--exec \<command>)         | mock_cmd.runs(lambda inv: ('output', b'', 0))      |
| Verify calls (shellmock_verify)             | mox.verify()                                       |
<!-- markdownlint-enable MD013 -->

### 2.5 The Lifecycle in Practice: `replay()` and `verify()`

The `CmdMox` controller provides two methods that demarcate the phases of the
testing lifecycle. When using the pytest `cmd_mox` fixture, these are called
automatically (replay before the test body executes, verification during
teardown) so most tests only declare expectations and exercise the system under
test.

```mermaid
sequenceDiagram
  autonumber
  actor Runner as Pytest Runner
  participant Pytest as pytest
  participant Fixture as cmd_mox fixture
  participant CmdMox as CmdMox
  Note over Pytest,Fixture: resolve auto_lifecycle (marker → param → CLI → ini)
  Pytest->>Fixture: request fixture (setup)
  Fixture->>CmdMox: __enter__(verify_on_exit=False, env_prefix=worker_prefix)
  alt auto_lifecycle enabled
    Fixture->>CmdMox: replay()
    Note right of CmdMox #DDF2E9: Phase → REPLAY
  end
  Pytest-->>Runner: run test body
  Runner->>Pytest: test completes
  Pytest->>Fixture: pytest_runtest_makereport
  alt auto_lifecycle enabled and eligible
    Fixture->>CmdMox: verify()
    Note right of CmdMox #E8F5E9: Phase → VERIFY
    Fixture->>Pytest: attach verification outcome to report
  else skip/failed
    Note over Fixture: verification suppressed or recorded differently
  end
  Pytest->>Fixture: teardown
  Fixture->>CmdMox: __exit__ (cleanup)
```

The `_CmdMoxManager` consolidates the auto-lifecycle orchestration that the
fixture previously delegated to individual helper functions. The following
sequence diagram highlights how the manager collaborates with pytest and the
`CmdMox` controller while capturing verification and cleanup failures.

```mermaid
sequenceDiagram
  autonumber
  participant PyTest as PyTest
  participant Plugin as _CmdMoxManager
  participant Mox as CmdMox

  rect rgb(245,248,255)
    PyTest->>Plugin: fixture setup (enter)
    Plugin->>Mox: __enter__()
    alt auto_lifecycle
      Plugin->>Mox: replay()
    end
  end

  PyTest->>PyTest: run test body
  note over PyTest: pytest_runtest_makereport records call failure flag

  rect rgb(245,255,245)
    PyTest->>Plugin: fixture teardown (exit, body_failed?)
    alt auto_lifecycle and in REPLAY
      Plugin->>Mox: verify()
      note over Plugin: record verification section if error
    end
    Plugin->>Mox: __exit__()  %% cleanup
    alt any errors
      Plugin-->>PyTest: pytest.fail(formatted combined error)
    else
      Plugin-->>PyTest: teardown complete
    end
  end
```

- `mox.replay()`: This method must be called after all expectations have been
  recorded. It signals the end of the record phase and the beginning of the
  replay phase. Internally, this call triggers the creation of the temporary
  shim directory, the generation of the shim executables, and the modification
  of the `PATH` environment variable. It effectively "arms" the mocking
  framework.

- `mox.verify()`: This method must be called after the code under test has been
  executed. It signals the end of the replay phase. It performs the critical
  verification logic, comparing the journal of actual invocations against the
  list of recorded expectations. If any discrepancy is found—such as an
  unexpected call, an unfulfilled expectation, or an incorrect argument—it
  raises a detailed `VerificationError`. The error messages will be designed
  for maximum debuggability, providing a clear "diff" between the expected and
  actual interactions, similar to the highly-regarded error reporting of PyMox.
  Finally, it orchestrates the cleanup of all test artifacts, including
  restoring the `PATH`.

## III. Architectural Blueprint: Inside `CmdMox`

This section details the internal architecture of `CmdMox`, providing a
blueprint for implementation. The design focuses on robustness, process safety,
and performance, leveraging Python's strengths to overcome the limitations of
existing shell-based tools.

### 3.1 The Shim Generation Engine

This component is responsible for the on-the-fly creation of the executable
Python shims that intercept command calls. To maximize efficiency and minimize
disk I/O, the engine will not write a unique script for every mocked command.

Instead, the `CmdMox` library will contain a single, generic `shim.py` template
script. When `mox.replay()` is invoked, the `CmdMox` controller will execute
the following steps:

1. Create a temporary directory with a unique, process-safe name (e.g.,
   `/tmp/cmdmox-pytest-worker-1-pid-12345`).

2. For each unique command name being mocked (e.g., `git`, `curl`), it will
   create a symbolic link inside the temporary directory (e.g., `git` ->
   `.../ cmdmox/internal/shim.py`). This avoids duplicating the script's
   content on disk.

3. The master `shim.py` script itself will be made executable (`chmod +x`). The
   operating system will follow the symlink and execute the target script.

4. The shim script will determine which command it is impersonating by
   inspecting its own invocation name (`sys.argv`).

This symlink-based approach is highly efficient and ensures that any updates or
bug fixes to the shim logic only need to be applied to one central template
regardless of platform. On Windows the generator writes lightweight `.cmd`
launchers that shell out to `shim.py`. The launchers use CRLF delimiters for
native compatibility and CmdMox amends `PATHEXT` during replay so the command
processor will resolve the batch wrappers even on hosts where the extension was
removed from the user environment. See :class:`EnvironmentManager` in
:mod:`cmd_mox.environment` for the Windows-specific `PATHEXT` management logic.

The batch template also doubles percent signs and carets so Windows-specific
metacharacters survive the hand-off to Python, even when user arguments contain
spaces or escape sequences. Case-insensitive hosts are handled by rejecting
duplicate command names whose casing only differs, ensuring shim files cannot
trample each other on NTFS. When shims are regenerated from Linux or macOS the
launcher still uses CRLF delimiters so the resulting `.cmd` remains byte-for-
byte identical to the Windows-generated variant. At runtime the shared shim
script further normalises Windows argv by repeatedly collapsing doubled carets
(`^^`) into single carets. This intentionally lossy step counteracts the
multi-layer escaping performed by `cmd.exe` so the IPC payload reflects the
user's intended text instead of the intermediate batch form.
```mermaid
sequenceDiagram
    actor User
    participant App as Application
    participant FS as FileSystem
    User->>App: Call create_shim_symlinks(directory, commands)
    App->>FS: Check if directory exists
    alt Directory does not exist
        App->>User: Raise FileNotFoundError
    else Directory exists
        App->>FS: Set SHIM_PATH executable
        loop For each command
            App->>FS: Remove existing symlink (if any)
            App->>FS: Create symlink to SHIM_PATH
        end
        App->>User: Return mapping of command to symlink path
    end
```

### 3.2 State Management and Inter-Process Communication (IPC)

The communication between the main test process (hosting the `CmdMox`
controller) and the numerous, short-lived shim processes is the most critical
architectural element of `CmdMox`. The design moves away from the fragile,
file- based communication methods used by shell-based tools in favor of a
modern, robust IPC bus.

This IPC bus will be implemented using a Unix domain socket, which provides a
fast and reliable stream-based communication channel between processes on the
same host. On Windows hosts, where Unix sockets are not universally available,
`CmdMox` still exports ``CMOX_IPC_SOCKET`` with the logical socket path inside
the shim directory, but the transport hashes that path into a dedicated named
pipe (`\\.\pipe\cmdmox-<hash>`) via ``derive_pipe_name``. Shims therefore keep
working with socket semantics (PATH filtering, env setup, etc.) while the IPC
layer transparently maps that logical path to a Windows named pipe using
``win32pipe``/``win32file`` (provided by the ``pywin32`` dependency). The
workflow is as follows:

1. **Server Initialization:** When the `CmdMox` controller enters the replay
   phase, it starts a lightweight server thread. This thread creates a
   `socket.socket` listening on a unique path within the temporary shim
   directory (e.g., `/tmp/ cmd_mox.../ipc.sock`).

2. **Environment Setup:** The controller exports the path to this socket in an
   environment variable (e.g., `CMOX_IPC_SOCKET`). This variable is inherited
   by any child processes, including the code under test and, consequently, the
   shims it invokes.

3. **Shim Connection:** When a shim is executed by the OS, its first action is
   to read the `CMOX_IPC_SOCKET` environment variable and connect to the
   listening server thread in the main test process.

4. **Invocation Reporting:** The shim gathers all relevant invocation data: the
   command name, the list of arguments (`sys.argv[1:]`), the complete content
   of its standard input, and a copy of its current environment variables.
   Large payloads may require future streaming or truncation. It serializes
   this data into a structured format like JSON and sends it over the socket to
   the server.

5. **Server-Side Processing:** The server thread in the main process receives
   the JSON payload. It deserializes it into an `Invocation` object and records
   it in the in-memory Invocation Journal after the handler runs. At that point
   the invocation also captures the resulting `stdout`, `stderr`, and
   `exit_code`. The server then searches its list of `Expectation` objects to
   find a match for this invocation.

6. **Response Delivery:** Once a matching expectation is found, the server
   determines the prescribed response. If it's a static `.returns()` value, it
   serializes this data and sends it back to the shim. If it's a dynamic
   `.runs(handler)`, the server thread executes the handler function (which is
   in the same process and has access to all test state) and sends its result
   back.

7. **Shim Action:** The shim receives the response payload from the server. It
   writes the `stdout` and `stderr` data to its own standard streams and then
   terminates with the specified `exit_code`.

This socket-based IPC architecture is the key technical differentiator for
`CmdMox`. It is transactional, inherently process-safe, and allows for the
exchange of rich, complex data structures, providing a foundation for advanced
features that are infeasible with file-based logging.

#### Implementation Notes

- The shim infers the command name from ``argv[0]`` via :class:`pathlib.Path`
  so that the same script can impersonate any executable linked into the shim
  directory.
- The shim defers :func:`cmd_mox._shim_bootstrap.bootstrap_shim_path` until the
  entrypoint executes, avoiding import-time mutations of ``sys.path`` or
  ``sys.modules`` for consumers that import :mod:`cmd_mox.shim` as a helper
  module. Tests that reuse shim utilities should invoke the bootstrap
  explicitly during setup.
- On Windows the shim still exports :data:`CMOX_IPC_SOCKET` pointing at the
  temporary shim directory, but the IPC layer deterministically hashes that
  value into a named pipe (``\\.\pipe\cmdmox-<hash>``). This keeps the PATH
  filtering logic working unchanged while the transport communicates through
  `win32pipe`/`win32file` provided by `pywin32`.
- Standard input is eagerly read when the shim detects it is connected to a
  pipe. Guarding the read with ``sys.stdin.isatty()`` avoids blocking when a
  user invokes an interactive command during a test run, and the implementation
  explicitly skips calling ``read()`` on terminal-bound streams.
- The shim sends a shallow copy of ``os.environ`` to the IPC server. This
  captures environment variables at call time without mutating the caller's
  process state.
- Responses received from the server are written directly to ``stdout`` and
  ``stderr`` before the process exits using the supplied exit code. Any
  environment overrides returned by the server are merged into the shim's
  process environment, enabling later invocations in the same process to see
  those changes. Successive overrides accumulate, so commands executed within
  the same process observe the union of all previously injected variables.
- The shim reads :data:`cmd_mox.environment.CMOX_IPC_TIMEOUT_ENV` to determine
  its IPC timeout, defaulting to ``5.0`` seconds. Non-default overrides are
  validated to ensure they remain positive, finite floats before being applied.
- PATH merging uses a single `_build_search_path` helper that trims whitespace,
  removes the active shim directory, and de-duplicates entries via the shared
  ``normalize_path_string`` utility. Windows hosts therefore treat differently
  cased paths as duplicates while POSIX hosts preserve casing.

```mermaid
flowchart TD
    A["Start with env_path and lookup_path"] --> B["Collect raw entries in order (env_path then lookup_path)"]
    B --> C["Strip whitespace; skip empty entries"]
    C --> D["identity = normalize_path_string(entry)"]
    D --> E{Is the identity the shim directory or already in the seen set?}
    E -- "Yes" --> F["Skip entry"]
    E -- "No" --> G["Add entry to path_parts and identity to seen"]
    G --> H["Join path_parts with os.pathsep and return"]
    H --> Z["Resulting PATH is cross-platform, deduplicated, shim-filtered, and normalized"]
```

The initial implementation ships with a lightweight `IPCServer` class. It uses
Python's `socketserver.ThreadingUnixStreamServer` to listen on a Unix domain
socket path provided by the `EnvironmentManager`. Incoming JSON messages are
parsed into `Invocation` objects and processed in background threads with
reasonable timeouts (default: 5.0s). Callers can pass an `IPCHandlers`
dataclass to provide invocation and passthrough callbacks when constructing the
server, removing the need to subclass for custom behaviour. The
`CallbackIPCServer` compatibility wrapper forwards a `TimeoutConfig` dataclass
so callers can continue to customise startup and accept timeouts without
exceeding the four-argument limit. On Windows hosts the controller constructs a
`NamedPipeServer`, which shares the same handler plumbing but uses
`win32pipe`/`win32file` (via `pywin32`) to host a duplex named pipe that
mirrors the Unix socket behaviour. The named pipe name is derived from the
logical socket path, so shims can keep filtering the shim directory out of
``PATH`` without needing Windows-specific logic. The server attaches the
corresponding response data (`stdout`, `stderr`, `exit_code`) to the
`Invocation` before appending it to the journal. On Unix systems the server
cleans up the socket on shutdown to prevent stale sockets from interfering with
subsequent tests, while the Windows transport simply closes the pipe handles.
The timeout is configurable via
:data:`cmd_mox.environment.CMOX_IPC_TIMEOUT_ENV` (seconds).

When `IPCServer.start()` executes inside an active
:class:`~cmd_mox.environment.EnvironmentManager`, the manager exports both the
socket and timeout environment variables automatically. This keeps tests and
shim workflows from having to patch :mod:`os.environ` manually when they rely
on the higher-level context manager.

To avoid races and corrupted state, `IPCServer.start()` first checks if an
existing socket is in use before unlinking it. After launching the background
thread, the server polls for the socket path using an exponential backoff to
ensure it appears before clients connect. On the client side, `invoke_server()`
retries connection attempts with a linear backoff and small jitter to avoid
retry storms and transparently switches to a Windows named pipe when
``os.name == 'nt'``. Retry behaviour is configured via the `RetryConfig`
dataclass, which groups the retry count, backoff, and jitter parameters. The
Unix socket and named pipe clients both delegate to a shared
`retry_with_backoff` helper so jitter bounds, logging, and retry calculations
are tuned in one place rather than duplicated across transports. The client
then validates that the server's reply is valid JSON, raising a `RuntimeError`
if decoding fails. These safeguards make the IPC bus robust on slower or
heavily loaded systems.
```mermaid
classDiagram
    class IPCServer {
        - Path socket_path
        - float timeout
        - _InnerServer _server
        - Thread _thread
        + __enter__() IPCServer
        + __exit__(exc_type, exc, tb) None
        + start() None
        + stop() None
        + handle_invocation(invocation: Invocation) Response
    }
    class _InnerServer {
        - IPCServer outer
        + __init__(socket_path: Path, outer: IPCServer)
    }
    class _IPCHandler {
        + handle() None
    }
    class Invocation {
        + str command
        + list[str] args
        + str stdin
        + dict[str, str] env
        + str stdout
        + str stderr
        + int exit_code
        + dict[str, Any] to_dict()
    }
    class Response {
        + str stdout
        + str stderr
        + int exit_code
    }
    IPCServer --> _InnerServer : manages
    _InnerServer --> _IPCHandler : uses
    _IPCHandler --> Invocation : parses
    _IPCHandler --> Response : returns
    IPCServer --> Invocation : handles
    IPCServer --> Response : returns
```
```mermaid
sequenceDiagram
    actor Shim
    participant IPCServer
    participant Handler as _IPCHandler
    participant App as Application Logic

    Shim->>IPCServer: Connect via Unix socket
    Shim->>IPCServer: Send JSON Invocation
    IPCServer->>Handler: Pass connection
    Handler->>Handler: Parse Invocation
    Handler->>App: handle_invocation(Invocation)
    App-->>Handler: Response
    Handler->>Shim: Send JSON Response
    Shim->>Shim: Write stdout, stderr, exit code
```

The following diagram expands on the transport differences when the controller
boots an IPC server on Windows versus POSIX hosts.

```mermaid
sequenceDiagram
    participant Controller
    participant Server
    participant Client
    participant WindowsPipe as "Windows Named Pipe (Windows only)"
    participant UnixSocket as "Unix Domain Socket (POSIX only)"
    Controller->>Server: Start IPC server
    alt Windows
        Server->>WindowsPipe: Create named pipe (derive name from socket path)
        WindowsPipe-->>Server: Ready event set
        Client->>WindowsPipe: Connect to named pipe
        WindowsPipe->>Server: Forward request
        Server->>Client: Send response
    else POSIX
        Server->>UnixSocket: Create Unix domain socket
        UnixSocket-->>Server: Socket ready
        Client->>UnixSocket: Connect to socket
        UnixSocket->>Server: Forward request
        Server->>Client: Send response
    end
```

The relationships between the Windows transport helpers are summarised below.

```mermaid
erDiagram
    Path ||--o| NamedPipeServer : derives
    NamedPipeServer ||--|{ _NamedPipeState : manages
    Path ||--|{ derive_pipe_name : input
    derive_pipe_name }|--|| WINDOWS_PIPE_PREFIX : uses
    NamedPipeServer ||--|| derive_pipe_name : uses
```

### 3.3 The Environment Manager

This component will be implemented as a robust, exception-safe context manager
that handles all modifications to the process environment.

- On `__enter__`:

  1. It will save a copy of the original `os.environ`.

  2. Create the temporary shim directory.

  3. Prepend the shim directory's path to `os.environ`.

  4. Set any other necessary environment variables for the IPC
     mechanism, such as `CMOX_IPC_SOCKET`. Clients may additionally honour
     :data:`cmd_mox.environment.CMOX_IPC_TIMEOUT_ENV` to override the default
     connection timeout.

- On `__exit__`:

  1. Execute in a `finally` block to guarantee cleanup, even if the test fails
     with an exception.

  2. Restore the original `PATH` and unset any `CmdMox`-specific environment
     variables.

  3. Perform a recursive deletion of the temporary shim directory and all its
     contents (symlinks and the IPC socket).

The manager is not reentrant. Nested usage would overwrite the saved
environment snapshot, so attempts to use it recursively will raise
`RuntimeError`. Instead of clearing `os.environ` on exit, the manager restores
only those variables that changed and removes any that were added. This
approach avoids disrupting other threads that might rely on the environment
remaining mostly stable.

This rigorous management ensures that each test runs in a perfectly isolated
environment and leaves no artifacts behind, a critical requirement for a
reliable testing framework.

`CmdMox` enforces this guarantee even when replay aborts unexpectedly. The
controller treats any failure while starting the IPC server—including
``KeyboardInterrupt`` and ``SystemExit``—as a signal to immediately invoke the
environment manager's teardown routine before re-raising the error. Capturing
``BaseException`` in :meth:`CmdMox.replay` ensures that shim directories,
socket files, and `PATH` mutations are reversed deterministically so users
never have to remember to manually call :meth:`CmdMox.__exit__` after an
interrupt.

### 3.4 The Invocation Journal

The Invocation Journal is a simple but crucial in-memory data structure within
each `CmdMox` controller instance. A `collections.deque` backs the journal,
preserving call order and enabling efficient pruning when bounded. It stores a
chronological record of command calls during replay.

Each time the IPC server thread receives an invocation report from a shim, it
constructs an `Invocation` object containing the command name, arguments,
`stdin`, and environment. After the handler runs, the controller attaches the
resulting `stdout`, `stderr`, and `exit_code` to the `Invocation` and appends
it to the journal. `verify()` uses this enriched journal as the definitive
record, comparing it against predefined expectations to detect discrepancies.

## IV. Feature Deep Dive: Stubbing

This section details the implementation of the simplest form of test double:
the stub. Stubs are essential for satisfying dependencies of the system under
test without coupling the test to the implementation details of those
dependencies.

### 4.1 Simple, "Fire-and-Forget" Replacements

The primary use case for a stub is to provide a fixed, predictable response. An
API call like `mox.stub('grep').returns(stdout='match', exit_code=0)` initiates
the following process:

1. **Configuration:** The call creates a `Stub` configuration object within the
   `CmdMox` controller. This object stores the command name (`grep`) and the
   associated response data (stdout, stderr, exit code).

2. **Replay Phase:** During `mox.replay()`, this configuration is made available
   to the IPC server thread. It might be stored in a simple dictionary mapping
   command names to stub configurations.

3. **Invocation:** When the code under test executes `grep`, the `grep` shim is
   invoked. The shim connects to the IPC server and reports its invocation.

4. **Response:** The IPC server looks up `grep` in its stub configurations. It
   finds the defined behavior and sends a JSON response like
   `{'stdout': 'match', 'stderr': '', 'exit_code': 0}` back to the shim.

5. **Execution:** The shim receives this payload, prints "match" to its
   `stdout`, and exits with status 0.

6. **Verification:** During `mox.verify()`, stubs are not checked. If the
   `grep` command is never called, the test still passes. This
   "fire-and-forget" nature is the defining characteristic of a stub.

   Implementation-wise, the controller marks only mocks as "expected". Unused
   stubs therefore never raise `UnfulfilledExpectationError` during
   verification. Stub invocations still appear in the global journal for later
   inspection.

### 4.2 Advanced Stubs: Callable Handlers

To support dynamic or stateful behavior, `CmdMox` allows stubs to be configured
with a callable handler via the `.runs()` method, for example:
`mox.stub('date').runs(my_date_handler)`.

The implementation of this feature leverages the IPC architecture:

1. The `my_date_handler` callable itself is a Python object that exists only in
   the main test process. It is *not* serialized or sent to the shim process.

2. When the `date` shim is invoked and reports its call to the IPC server, the
   server identifies that the corresponding stub is configured with a `.runs()`
   handler.

3. The IPC server thread—which runs within the main test process and therefore
   has direct access to `my_date_handler`—executes the handler. It passes the
   structured `Invocation` object as an argument to the handler and, after the
   handler returns, records the resulting output streams and exit code on that
   invocation.

4. The handler performs its logic (which can involve accessing or modifying
   state within the test function's scope) and returns a result tuple:
   `(stdout_bytes, stderr_bytes, exit_code)`.

5. The IPC server serializes this dynamic result and sends it back to the `date`
   shim, which then acts accordingly.

This powerful feature enables the creation of sophisticated stubs that can, for
instance, return different values on subsequent calls, simulate I/O operations,
or interact with other components of the test setup, far exceeding the
capabilities of static mocks defined in shell scripts.

## V. Feature Deep Dive: Mocking

This section details the implementation of the library's core feature: strict,
verifiable mocking. Mocks are the foundation of the "Record-Replay-Verify"
paradigm and are used to assert that the system under test interacts with its
external dependencies in a precisely defined manner.

```mermaid
sequenceDiagram
    participant Test as Test Code
    participant CmdMox as CmdMox
    participant Double as CommandDouble
    participant Shim as Shim
    participant IPC as IPC Server

    Test->>CmdMox: Setup mock with expectations (args, stdin, env, order, times)
    Test->>Shim: Run command (with args, stdin)
    Shim->>IPC: Send invocation (args, stdin)
    IPC->>CmdMox: Forward invocation
    CmdMox->>Double: Match invocation (args, stdin, env)
    alt Match success
        Double->>CmdMox: Return response (stdout, stderr, env)
        CmdMox->>IPC: Send response
        IPC->>Shim: Return response
        Shim->>Shim: Inject env if present
        Shim->>Test: Return output
    else Match failure
        CmdMox->>IPC: Return error
        IPC->>Shim: Return error
        Shim->>Test: Raise error
    end
    Test->>CmdMox: verify()
    CmdMox->>Double: Check call count, order, expectations
    alt All expectations met
        CmdMox->>Test: Success
    else Expectation failed
        CmdMox->>Test: Raise verification error
    end
```

### 5.1 The Argument Matching Engine and Comparators

To provide flexibility beyond exact argument matching, `CmdMox` will include a
rich set of comparator objects, directly inspired by the comparators in PyMox.
These objects allow for defining expectations based on patterns and properties
rather than just literal values.

The library will provide a suite of built-in comparators:

- `cmd_mox.Any()`: Matches any single argument at a given position.

- `cmd_mox.IsA(type)`: Matches any argument that is an instance of the given
  Python type (after basic parsing).

- `cmd_mox.Regex(pattern: str)`: Matches any argument that conforms to the given
  regular expression.

- `cmd_mox.Contains(substring: str)`: Matches any argument that contains the
  given substring.

- `cmd_mox.StartsWith(prefix: str)`: Matches any argument that starts with the
  given prefix.

- `cmd_mox.Predicate(callable)`: The most flexible comparator. It accepts a
  callable that takes the argument as input and returns `True` for a match and
  `False` otherwise.

When a developer defines an expectation using
`with_matching_args(IsA(str), Regex(r'--file=\S+'))`, these comparator objects
are stored as part of the `Expectation` configuration. During the replay phase,
when the IPC server receives an invocation from a shim, it iterates through its
list of recorded expectations. For each expectation, it compares the incoming
arguments against the stored comparators to determine if there is a match. This
engine is the key to writing flexible yet precise tests. If a comparator
rejects an argument, the verifier reports the failing index and comparator
representation to aid debugging. If a comparator raises an exception, the
message includes the exception type and text. These diagnostics originate from
`Expectation.explain_mismatch()`. It pinpoints the failing argument index and
comparator.

### 5.2 Verification Logic: The Heart of `mox.verify()`

The `mox.verify()` method encapsulates the most complex logic in the library.
Its purpose is to algorithmically reconcile the list of predefined
`Expectation` objects with the chronological `InvocationJournal` collected
during the replay phase. A mismatch of any kind constitutes a test failure.

The verification algorithm will perform several critical checks:

1. **Unexpected Invocations:** It iterates through the `InvocationJournal`. For
   each actual invocation, it checks if it matches any of the recorded
   expectations. If an invocation occurs that does not match *any* unfulfilled
   expectation, it signifies an unexpected command call. This immediately
   raises an `UnexpectedCommandError`, analogous to PyMox's
   `UnexpectedMethodCallError`. The error message will clearly state the
   unexpected command that was called.

2. **Unfulfilled Expectations:** After checking all actual invocations, the
   algorithm checks if any `Expectation` objects remain unfulfilled. If an
   expectation was recorded but never matched by an actual invocation, it
   raises an `UnfulfilledExpectationError`. This is equivalent to PyMox's
   "Expected methods never called" error and is critical for ensuring that the
   code under test is actually exercising its dependencies as intended.

3. **Incorrect Call Counts:** For expectations defined with `.times(N)`, the
   verifier ensures that the expectation was met exactly `N` times. If it was
   met more or fewer times, a `VerificationError` is raised.

4. **Order Violations:** For expectations marked with `.in_order()`, the
   verifier ensures that they were met in the same sequence in which they were
   recorded. It maintains a pointer to the "current" expected ordered call and
   advances it only when a match is found. An out-of-order call is treated as
   an unexpected invocation.

The error messages generated by `verify()` are a key part of the user
experience. They will be meticulously crafted to provide maximum diagnostic
information, showing the expected call (including arguments and comparators)
and contrasting it with the actual call that was received, or noting its
absence entirely.

## VI. Feature Deep Dive: Spying

Spying provides a more flexible, observational approach to testing
interactions, complementing the strictness of mocks. Spies are useful for when
the exact details of an interaction are not critical to the test's success, but
the developer still wants to assert that a call was made.

### 6.1 The Spy API and Invocation History

Creating a spy is straightforward: `spy = mox.spy('curl')`. This registers
`curl` as a spied command. By default, a spy will act like a stub that does
nothing and returns a successful exit code. Its primary purpose is to record
calls.

After `mox.verify()` has been called (which for spies simply confirms no
unexpected errors occurred and performs cleanup), the test can inspect the spy
object to access its recorded history. The spy object will expose a public API
for this purpose:

- `spy.call_count`: An integer representing the total number of times the
  command was called.

- `spy.invocations`: A list of `Invocation` objects, where each object provides
  structured access to a single call's details, including captured `stdout`,
  `stderr`, and `exit_code`.

- `spy.assert_called()`, `spy.assert_not_called()`, and
  `spy.assert_called_with(*args, stdin=None, env=None)`: Helpers mirroring
  `unittest.mock` assertions for presence, absence, and arguments.

*Example Assertion-Style Verification:*
```python
def test_downloader_uses_correct_user_agent(mox):
    spy = mox.spy('curl')
    spy.returns(stdout='Success') # Spies can also be given behavior
    mox.replay()

    download_file('http://example.com/file.zip')

    mox.verify()

    assert spy.call_count == 1
    invocation = spy.invocations
    assert invocation.args == ['curl', 'http://example.com/file.zip']
    assert 'User-Agent: MyDownloader/1.0' in invocation.env
```

This style of verification is less rigid than mocking and is preferred when the
goal is simply to check that "a call happened with these properties" rather
than enforcing a strict sequence of interactions.

### 6.2 Passthrough Spies: The "Record Mode"

A powerful extension of the spy concept is the "passthrough" spy. This feature
enables a "record and replay" workflow for test generation, an incredibly
useful tool for bootstrapping tests for legacy systems or complex command-line
interactions, similar in spirit to the record mode of `bash_placebo`.

A passthrough spy is created with `spy = mox.spy('aws').passthrough()`. The
implementation leverages the IPC architecture in a unique way:

1. When the `aws` shim is invoked, it reports the call to the IPC server as
   usual.

2. The IPC server identifies the spy is in passthrough mode. Instead of
   returning a canned response, it sends a `PassthroughRequest` back to the
   shim containing the original `PATH`, any expectation-specific environment
   overrides, and a unique invocation identifier.

3. The shim resolves the real executable by searching the supplied `PATH`. If
   the lookup fails or the binary is not executable, the shim keeps the error
   as the passthrough result.

4. When the executable is found, the shim runs it with the recorded arguments
   and `stdin`, merging the expectation environment with the original
   invocation environment so nested commands continue to route through CmdMox.
   Expectation variables win on key conflicts to ensure overrides apply even
   when the caller already defines the same environment variable.

5. The shim sends a follow-up `PassthroughResult` message containing the real
   command's `stdout`, `stderr`, and `exit_code` to the server.

6. The server combines the invocation details with the passthrough result,
   records the invocation in the spy history and journal, and relays the final
   `Response` back to the shim so the calling process observes the real
   behaviour.

The core message types and their relationships are illustrated below:

```mermaid
erDiagram
    Invocation {
        string command
        list args
        string stdin
        dict env
        string stdout
        string stderr
        int exit_code
        string invocation_id
    }
    PassthroughRequest {
        string invocation_id
        string lookup_path
        dict extra_env
        float timeout
    }
    PassthroughResult {
        string invocation_id
        string stdout
        string stderr
        int exit_code
    }
    Response {
        string stdout
        string stderr
        int exit_code
        dict env
        PassthroughRequest passthrough
    }
    Invocation ||--o{ PassthroughRequest : "uses"
    PassthroughRequest ||--o{ PassthroughResult : "results in"
    Response ||--o| PassthroughRequest : "may contain"
    PassthroughResult ||--o| Response : "reported as"
```

The interaction between the shim, controller, and passthrough coordinator is
shown below. It captures both the passthrough and non-passthrough branches so
test authors can understand how control flows through the IPC pipeline:

```mermaid
sequenceDiagram
  autonumber
  actor Caller as Calling process
  participant Shim
  participant Server
  participant Controller
  participant Passthrough as PassthroughCoordinator
  participant Runner as RealCommand

  Caller->>Shim: invoke mocked command
  Shim->>Server: send Invocation (kind=invocation, invocation_id)
  Server->>Controller: handle invocation
  alt controller selects passthrough
    Controller->>Passthrough: prepare_request(double, invocation, PassthroughConfig)
    Passthrough-->>Controller: Response(passthrough=PassthroughRequest)
    Controller-->>Server: Response with passthrough
    Server-->>Shim: Response with PassthroughRequest
    Shim->>Runner: resolve real command (override or PATH) and run (env, timeout)
    Runner-->>Shim: stdout, stderr, exit_code
    Shim->>Server: kind=passthrough-result (PassthroughResult)
    Server->>Controller: handle_passthrough_result
    Controller->>Passthrough: finalize_result(PassthroughResult)
    Passthrough-->>Controller: (double, invocation, Response)
    Controller-->>Server: Final Response
    Server-->>Shim: Final Response
  else no passthrough
    Controller-->>Server: Response (normal)
    Server-->>Shim: Response (normal)
  end
  Shim-->>Caller: emit stdout/stderr and exit with code
```

To support deterministic behavioural tests, the shim honours
``CMOX_REAL_COMMAND_<NAME>`` environment variables. When present, they override
the executable path resolved in step 3, allowing tests to point a passthrough
spy at a controlled binary on disk. The override is intentionally opt-in and
namespaced so production usage continues to rely on the original `PATH`.

The immediate benefit is that a test can run against a real system while
`CmdMox` transparently records every interaction. The long-term implication is
the potential for a powerful test generation utility. A developer could run a
complex script under a `CmdMox` recorder, which would use passthrough spies to
capture all external command interactions. The recorder could then
automatically generate a complete, self-contained `pytest` file, with all the
real interactions converted into `mox.mock(...).returns(...)` definitions. This
would dramatically lower the barrier to entry for placing legacy command-line
tools under test.

## VII. Advanced Topics and Implementation Considerations

This section addresses known complexities, edge cases, and non-functional
requirements that the implementation must handle to be considered a robust and
professional-grade library.

### 7.1 Handling Complex Shell Interactions: Pipelines and Redirection

A critical aspect to define is the library's scope regarding shell syntax.
`PATH` hijacking, as a mechanism, intercepts the execution of individual
commands. It does not, and cannot, intercept or interpret the functionality of
the shell itself, such as pipelines (`|`), I/O redirection (`>`, `<`), or
process substitution (`<()`).

Therefore, the design explicitly states that `CmdMox` mocks the *tools*, not
the *shell* that glues them together. When a user needs to test a script that
contains a command like `grep foo file.txt | sort -r`, the test is not on the
pipeline itself, but on the behavior of `grep` and `sort`.

The user would test this by executing the full command line (e.g., via
`subprocess.run(..., shell=True)`) and setting up mocks for each individual
command in the pipeline:

*Example Pipeline Test:*
```python
def test_pipeline_logic(mox):
    # Mock the first command in the pipe
    mox.mock('grep').with_args('foo', 'file.txt').returns(stdout='c\na\nb\n')

    # Mock the second command, expecting the output of the first as its input
    mox.mock('sort').with_args('-r').with_stdin('c\na\nb\n').returns(stdout='c\nb\na\n')

    mox.replay()

    # Run the actual shell command
    result = subprocess.check_output('grep foo file.txt | sort -r', shell=True, text=True)

    assert result == 'c\nb\na\n'
    mox.verify()
```

This approach correctly tests that the application invokes the constituent
commands as expected. This limitation and the proper testing pattern must be
clearly documented for users.

### 7.2 Managing the Mocked Environment

Applications often depend on environment variables. `CmdMox` must provide a way
to control the environment in which the mocked commands execute. The fluent API
will include a `.with_env(vars: dict)` method on `MockCommand`, `StubCommand`,
and `SpyCommand` objects.

When this method is used, the provided dictionary of environment variables is
stored with the expectation. This data is then passed to the shim as part of
the response payload from the IPC server. Before executing its primary action
(e.g., printing `stdout` or running a handler), the shim script will update its
own environment using `os.environ |= vars`. This ensures that any further
processes spawned by a `.runs()` handler, for example, will inherit the
correct, mock-specific environment.

### 7.3 Concurrency and Parallelization (`pytest-xdist`)

Modern test suites are frequently run in parallel using tools like
`pytest- xdist` to reduce execution time. `CmdMox` must be designed from the
ground up to be fully compatible with parallel execution, where each test
worker runs in a separate process.

The proposed IPC-based architecture is inherently conducive to safe
parallelization. The file-based communication used by shell-based mockers would
require complex file locking or intricate namespacing schemes to avoid race
conditions between parallel workers. `CmdMox` avoids this entirely.

The `pytest` fixture will be designed to be "xdist-aware." It can access the
worker ID provided by `pytest-xdist` (e.g., `gw0`, `gw1`). This ID will be
incorporated into the names of the temporary directory and the IPC socket:

- Worker 0 Directory: `/tmp/cmdmox-gw0-pid12345/`

- Worker 0 Socket: `/tmp/cmdmox-gw0-pid12345/ipc.sock`

- Worker 1 Directory: `/tmp/cmdmox-gw1-pid54321/`

- Worker 1 Socket: `/tmp/cmdmox-gw1-pid54321/ipc.sock`

Because each worker process gets its own `CmdMox` instance, its own unique shim
directory, and its own private IPC socket, there is no shared state between
workers. Each test runs in a completely isolated `CmdMox` environment,
eliminating the possibility of cross-test interference and ensuring correctness
and reliability in parallel test runs.

The implementation now enforces this isolation with automated tests. Unit tests
exercise the `EnvironmentManager` to confirm every context manager invocation
produces a distinct shim directory and socket path. Behavioural tests execute a
generated suite under ``pytest -n2`` to prove that separate workers never reuse
paths and that cleanup removes the temporary directories after each test. These
checks provide confidence that parallel execution cannot leak shims or socket
files across worker boundaries.

<!-- markdownlint-disable MD013 -->

#### Table 2: Fluent API Method Reference

This table provides a quick, scannable reference for the core Domain-Specific
Language (DSL) used to build expectations.

| Method                              | Purpose                                                           | Example                                             |
| ----------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------- |
| .with_args(\*args)                  | Specifies the exact arguments the command must be called with.    | .with_args('ls', '-l', '/tmp')                      |
| .with_matching_args(\*matchers)     | Specifies flexible argument matchers (e.g., comparators).         | .with_matching_args(IsA(str), Regex(r'--foo=\\d+')) |
| .with_stdin(data)                   | Specifies expected stdin content. Can use strings or comparators. | .with_stdin(Contains('payload'))                    |
| .with_env(vars)                     | Specifies environment variables for the command's context.        | .with_env({'API_KEY': 'secret'})                    |
| .returns(stdout, stderr, exit_code) | Defines the static output and exit code of the mocked command.    | .returns(stdout=b'OK', exit_code=0)                 |
| .runs(handler)                      | Provides a callable for dynamic, stateful behavior.               | .runs(my_handler_func)                              |
| .times(count)                       | Sets the expected number of times the command will be called.     | .times(2)                                           |
| .in_order()                         | Marks this expectation as part of an ordered sequence.            | .in_order()                                         |
| .any_order()                        | Explicitly opts out of ordered verification.                      | .any_order()                                        |
| .passthrough()                      | (Spy only) Executes the real command and records the interaction. | mox.spy('ssh').passthrough()                        |

<!-- markdownlint-enable MD013 -->

## VIII. Conclusion and Future Roadmap

### 8.1 Summary of the `CmdMox` Design

This document outlines a comprehensive design for `CmdMox`, a Python library
poised to significantly improve the testing landscape for command-line tools
and scripts. By synthesizing the ergonomic, "Record-Replay-Verify" paradigm of
PyMox with a robust `PATH`-hijacking mechanism, `CmdMox` offers a powerful and
developer-friendly solution.

The key architectural innovations—the use of Python shims over shell scripts
and the implementation of a sophisticated, socket-based IPC bus—liberate the
framework from the brittleness of file-based communication. This design
provides a solid foundation for reliable, process-safe, and highly-featured
test doubles. The fluent, chainable API ensures that tests are not only
effective but also readable and maintainable. The inclusion of stubs, strict
mocks, and observational spies (including a passthrough mode) provides a
complete toolkit for tackling a wide range of testing scenarios.

### 8.2 Future Roadmap

While the design for version 1.0 is comprehensive for Linux, FreeBSD, and
Darwin environments, several avenues for future expansion exist.

- **Windows Support:** CmdMox now provides first-class Windows support. The IPC
  layer retains Unix domain sockets where available and augments the startup
  handshake so Windows clients can detect readiness even though no filesystem
  socket appears. Shim generation emits `.cmd` launchers that shell out to the
  active Python interpreter and invoke `shim.py`, preserving argument quoting
  and inheriting the test process environment. Environment management reuses
  the existing `PATH`-based interception, ensures `.CMD` lives in `PATHEXT`,
  and restores the original environment on teardown. Long shim paths are
  collapsed to their short (8.3) counterparts whenever the Windows `MAX_PATH`
  limit is at risk, PATH filtering treats casing consistently, and duplicate
  command names that differ only by case are rejected to avoid filesystem
  collisions. A dedicated Windows smoke job now runs in CI via the
  `windows-smoke` Makefile target, exercising mocked invocations and
  passthrough spies while publishing `windows-ipc.log` for diagnostics.

- **Record Mode (Phase XII):** The comprehensive design for Record Mode is
  detailed in Section IX. This feature transforms passthrough spy recordings
  into reusable test fixtures, enabling developers to record interactions once
  against real systems and replay them indefinitely in CI environments. Key
  components include fixture persistence with JSON format and schema
  versioning, scrubbing utilities for sensitive data sanitization, and a CLI
  tool for recording, replaying, and generating test code from fixtures.

- **Shell Function Mocking:** The current design explicitly excludes the mocking
  of shell functions defined within a script, a notoriously difficult problem.
  Future research could explore techniques to achieve this, such as pre-
  processing the script under test to replace target function definitions with
  calls to a `cmdmox-shim` command before sourcing it. This remains a complex
  area with many edge cases.

- **Performance Optimizations:** For test suites with extremely high-frequency
  command calls, the performance of the JSON serialization and socket
  communication could become a factor. Future versions could investigate
  higher- performance IPC strategies, such as using a binary serialization
  format like MessagePack or exploring shared memory for certain use cases.

### 8.3 Design Decisions for the Initial Controller

The first implementation of the :class:`CmdMox` controller focuses on lifecycle
management and a minimal stub facility. The controller wraps
`EnvironmentManager` and `IPCServer` to orchestrate environment setup and
inter-process communication. Invocations from shims are appended to an internal
journal. When a stub is registered for a command, the controller returns the
configured :class:`Response`; otherwise it echoes the command name.

During `verify()` the controller fails if unexpected commands were executed or
if any registered mock command was never called. Stubs are ignored during this
phase. This simplified verification establishes the record → replay → verify
workflow and lays the groundwork for upcoming expectation and spy features.
```mermaid
sequenceDiagram
    actor Tester
    participant CmdMox
    participant EnvironmentManager
    participant IPCServer
    participant Shim

    Tester->>CmdMox: Create instance
    Tester->>CmdMox: stub('hi').returns(stdout='hello')
    Tester->>CmdMox: replay()
    CmdMox->>EnvironmentManager: __enter__()
    CmdMox->>Shim: create_shim_symlinks()
    CmdMox->>IPCServer: start()
    Tester->>Shim: run 'hi' (as subprocess)
    Shim->>IPCServer: connect and send invocation
    IPCServer->>CmdMox: handle_invocation(invocation)
    CmdMox->>IPCServer: return Response(stdout='hello')
    IPCServer->>Shim: send response
    Shim->>Tester: output 'hello'
    Tester->>CmdMox: verify()
    CmdMox->>IPCServer: stop()
    CmdMox->>EnvironmentManager: __exit__()
```

Custom exception classes clarify failure modes: `LifecycleError` signals
improper use of `replay()` or `verify()`, `UnexpectedCommandError` indicates an
invocation without a matching stub, and `UnfulfilledExpectationError` reports
stubs that were never called. To aid debugging, these errors include the
controller's active phase in their messages.
```mermaid
classDiagram
    class CmdMox {
        - EnvironmentManager environment
        - IPCServer _server
        - bool _entered
        - str _phase
        - list expectations
        - dict stubs
        - list spies
        - deque journal
        - set _commands
        + __enter__()
        + __exit__()
        + register_command(name)
        + stub(command_name)
        + replay()
        + verify()
        - _handle_invocation(invocation)
    }
    class StubCommand {
        - str name
        - CmdMox controller
        - Response response
        + returns(stdout, stderr, exit_code)
    }
    class EnvironmentManager {
    }
    class IPCServer {
        + start()
        + stop()
        + handle_invocation(invocation)
    }
    class Invocation {
    }
    class Response {
    }
    CmdMox "1" -- "1" EnvironmentManager : uses
    CmdMox "1" -- "1" IPCServer : manages
    CmdMox "1" -- "*" StubCommand : has
    CmdMox "1" -- "*" Invocation : records
    StubCommand "1" -- "1" Response : configures
    IPCServer "1" -- "*" Invocation : handles
```
```mermaid
classDiagram
    class CmdMoxError {
    }
    class LifecycleError {
    }
    class MissingEnvironmentError {
    }
    class VerificationError {
    }
    class UnexpectedCommandError {
    }
    class UnfulfilledExpectationError {
    }
    CmdMoxError <|-- LifecycleError
    CmdMoxError <|-- MissingEnvironmentError
    CmdMoxError <|-- VerificationError
VerificationError <|-- UnexpectedCommandError
VerificationError <|-- UnfulfilledExpectationError
```

### 8.4 Design Decisions for the Pytest Plugin

The plugin exposes a `cmd_mox` fixture that yields a ready-to-use
:class:`CmdMox` instance.  The fixture enters the :class:`EnvironmentManager`
before yielding and always exits it afterwards to guarantee environment cleanup.

To support `pytest-xdist` each fixture instance incorporates the worker ID into
the temporary directory prefix.  The prefix takes the form
`cmdmox-{worker}-{pid}-` ensuring that socket paths and shim directories are
unique across parallel workers. When `PYTEST_XDIST_WORKER` is absent the
fixture falls back to `main` so the prefix becomes `cmdmox-main-{pid}-`.

### 8.5 Design Decisions for `MockCommand` Expectations

Expectation configuration now lives in a dedicated :class:`Expectation` object
held by each `CommandDouble`. Builder methods such as `with_args()` and
`with_stdin()` delegate to this object. During replay, the IPC handler merges
any `with_env()` variables into the recorded :class:`Invocation` before
executing the double. The overrides are applied with `temporary_env` so the
handler or canned response sees them without mutating the process environment.
Because these overrides are also copied onto `Invocation.env`, verification
matches against the same environment observed at execution time. When the
caller supplies conflicting values the handler now raises
``UnexpectedCommandError`` so mismatched expectations fail immediately instead
of silently replacing the provided values.

`Expectation.with_env()` also validates that keys and values are non-empty
strings. This catches typos and accidental `None` values during test
configuration rather than deep within the replay loop.

Verification is split into focused components. `UnexpectedCommandVerifier`
checks that every journal entry matches a registered expectation.
`OrderVerifier` ensures expectations marked `in_order()` appear in the same
relative order in the journal. It filters the journal to invocations that match
the ordered expectations so interleaved unordered mocks, even when they share a
command name, are ignored. `CountVerifier` verifies that each expectation was
met exactly the declared number of times. This modular approach simplifies the
logic within :meth:`CmdMox.verify` and clarifies how mixed ordered and
unordered calls are handled.

The verifiers now emit diff-style diagnostics that show the expected call, the
actual invocation, and any mismatching context such as stdin or environment
variables. Sensitive environment values are redacted automatically so failure
messages remain safe to copy into bug reports. `UnexpectedCommandVerifier`
tracks call counts as it scans the journal, raising immediately when a mock is
invoked more often than permitted. `OrderVerifier` prints both the expected and
observed sequences with the first divergence highlighted, while `CountVerifier`
lists the recorded invocations for expectations that were never fulfilled.
These detailed reports make it obvious what changed between record and replay,
reducing the time spent reverse-engineering verification failures.

To keep the expectation matcher readable, `Expectation.matches` now delegates
individual checks to small helper methods. These helpers verify the command
name, arguments, standard input, and environment separately, reducing
cyclomatic complexity without altering behaviour.

The controller's `replay()` and `verify()` methods were likewise broken down
into dedicated helper functions such as `_check_replay_preconditions` and
`_finalize_verification`. This keeps the high-level workflow clear while
localising error handling and environment cleanup details.

### 8.6 Design Decisions for `SpyCommand`

Spies now support a `passthrough()` mode that executes the real command instead
of a canned response. When a passthrough spy is invoked the controller
constructs a `PassthroughRequest` containing the original `PATH`, expectation
environment overrides, and a unique invocation identifier. The shim resolves
and executes the real command using helper utilities shared with the
`CommandRunner`, then reports the captured result via a `PassthroughResult`
message. The controller merges this outcome into the invocation journal and spy
history before replying to the shim. This split keeps the server thread
lightweight while ensuring passthrough invocations faithfully reproduce the
real command's behaviour.

To simplify post-replay assertions, spies expose `assert_called`,
`assert_not_called`, and `assert_called_with` methods modelled after
`unittest.mock`. `assert_called_with` accepts optional `stdin` and `env`
keyword arguments to verify standard input and environment variables. Each
helper raises `AssertionError` when expectations are unmet, offering a
lightweight alternative to inspecting the `invocations` list directly.

The runner validates that the resolved command path is absolute and executable.
It enforces a configurable timeout (30 seconds by default) to prevent hanging
processes. Any unexpected exceptions are converted into error responses instead
of bubbling up.

Both mocks and spies maintain an `invocations` list. A convenience property
`call_count` exposes `len(invocations)` for assertion-style tests.

### 8.7 Design Decisions for Comparator Matching

Comparator helpers such as :class:`Any`, :class:`IsA`, :class:`Regex`,
:class:`Contains`, :class:`StartsWith`, and :class:`Predicate` are implemented
as simple callables. Each inherits a lightweight `_ReprMixin` so failing tests
display meaningful values. `Expectation.with_matching_args` accepts callables
of the form `Callable[[str], bool]` and requires one comparator per positional
argument. The matcher result is interpreted as truthy. This keeps the matching
engine agnostic to comparator implementations while enabling user-supplied
predicates to participate alongside the built-ins. Regular expressions are
compiled once per comparator and `IsA` relies on type conversion to avoid
bespoke parsing logic. Comparator exceptions surface their class and message in
mismatch reports.

### 8.8 Design Decisions for the Invocation Journal

The controller maintains a `journal` attribute to record every invocation in
chronological order. Each entry is an :class:`Invocation` dataclass containing
the command name, argument list, captured standard input, environment at call
time, and the resulting `stdout`, `stderr`, and `exit_code`. A
`collections.deque` backs the journal to guarantee append performance and
preserve ordering for later verification. Configure bounds via
`CmdMox(max_journal_entries=n)`; when full, the oldest entries are pruned.
Verifiers consume this deque directly, ensuring that verification reflects the
exact sequence observed during replay.

### 8.9 Design Decisions for Documentation and Examples

CmdMox ships with a set of runnable example tests under `/examples`. Each file
is a small, user-facing pytest module that demonstrates a single pattern (stub,
mock, spy, pipeline, passthrough). Keeping these examples as executable tests
ensures they stay in sync with the public API and prevents documentation drift.

Pipeline examples rely on shell parsing (`shell=True`) and therefore assume a
POSIX-like shell. For portability the repository's behavioural pipeline check
only runs in Unix-socket environments, and the standalone pipeline example is
skipped on Windows where shell semantics and built-in commands differ.

### 8.10 Design Decisions for API Documentation Completeness

CmdMox treats `cmd_mox.__all__` as the source of truth for the package's
top-level public API. The usage guide therefore includes a dedicated "Public
API reference" section that mirrors `__all__` so consumers can see what is
supported without inspecting the source.

To prevent documentation drift, the test suite enforces that every symbol
listed in `cmd_mox.__all__` is present in the usage guide's API reference. This
check is implemented twice:

- A unit test asserts coverage at the string level for fast feedback.
- A `pytest-bdd` scenario verifies the same user-facing behaviour as an
  acceptance criterion ("the docs list every public symbol").

### 8.11 Design Decisions for the `shellmock` Migration Guide

The `shellmock` migration guide will be published as a standalone document at
`docs/shellmock-migration-guide.md` to keep migration content focused without
overloading the usage guide. The documentation index in `docs/contents.md` will
link to this guide, and `docs/usage-guide.md` will include a short cross-link
for discoverability.

The guide will include a concise feature mapping table (`shellmock` CLI flags
to CmdMox fluent API calls), at least two end-to-end translated examples (a
simple stub and a strict mock with verification), and a brief migration
checklist. This keeps parity with the mapping in Table 1 while providing
actionable, copy-pasteable examples for new users. `shellmock` snippets will be
labelled as conceptual to acknowledge CLI variations across `shellmock`
versions, with a note to confirm exact flags in documentation.

## IX. Record Mode: Persisting Passthrough Recordings

This section details the design for Record Mode, a feature that transforms
passthrough spy recordings into reusable test fixtures. Record Mode enables
developers to capture real command interactions during test execution and
replay them in subsequent runs, dramatically reducing test friction and
enabling deterministic, fast test execution without external dependencies.

> **Design Document Conventions:** This section contains both normative API
> contracts and illustrative implementation details. **Normative** elements
> (marked with "MUST", "SHALL", or presented in API tables) define the public
> contract that implementations must honour. **Illustrative** elements
> (class diagrams, code snippets showing internal wiring, and sequence diagrams)
> demonstrate one possible implementation approach and may evolve without
> constituting a breaking change. When in doubt, the public fluent API methods
> (`.record()`, `.replay()`) and the fixture JSON schema are normative; internal
> class structures and controller integration details are illustrative.

### 9.1 Conceptual Overview

Record Mode operates in two complementary phases:

1. **Record Phase:** Execute real commands via passthrough spies while capturing
   all interactions (command, args, stdin, env, stdout, stderr, exit_code) to
   persistent fixture files.

2. **Replay Phase:** Load previously recorded fixtures and use them to respond
   to command invocations without executing real commands.

This approach bridges the gap between realistic integration testing
(passthrough mode) and fast, deterministic unit testing (mocked responses).
Developers can record interactions once against real systems, then replay them
indefinitely in CI environments without external dependencies.

### 9.2 Architecture

The Record Mode architecture introduces several new components that integrate
with the existing passthrough infrastructure:

```mermaid
flowchart TB
    subgraph TestCode["Test Code"]
        A["spy('git').passthrough().record('fixtures/git.json')"]
    end

    subgraph CmdMox["CmdMox Controller"]
        B[PassthroughCoordinator]
        C[RecordingSession]
    end

    subgraph Execution["Command Execution"]
        D[Real Command]
    end

    subgraph Persistence["Fixture Store"]
        E["fixtures/git.json"]
    end

    A --> B
    B --> C
    B --> D
    D --> B
    C --> E
```

The recording flow extends the existing passthrough mechanism:

```mermaid
sequenceDiagram
    autonumber
    participant Test as Test Code
    participant CmdMox as CmdMox Controller
    participant Spy as PassthroughSpy
    participant Recorder as RecordingSession
    participant RealCmd as Real Command
    participant Store as FixtureStore

    Test->>CmdMox: spy("git").passthrough().record("fixtures/git")
    CmdMox->>Spy: Configure passthrough + recording
    Test->>Spy: Command invocation (via shim)
    Spy->>Recorder: Start recording invocation
    Spy->>RealCmd: Execute real command
    RealCmd-->>Spy: stdout, stderr, exit_code
    Spy->>Recorder: Complete recording with result
    Recorder->>Store: Persist to fixture file
    Spy-->>Test: Return real command output
    Test->>CmdMox: verify()
    CmdMox->>Recorder: Finalize session
```

The following diagram provides a detailed view of the recording flow, showing
the interactions between `CommandDouble`, `PassthroughCoordinator`,
`RecordingSession`, and the fixture store:

```mermaid
sequenceDiagram
    autonumber
    participant TestCode as TestCode
    participant CmdMox as CmdMoxController
    participant CommandDouble as CommandDouble
    participant Passthrough as PassthroughCoordinator
    participant RecordingSession as RecordingSession
    participant RealCommand as RealCommand
    participant FixtureStore as FixtureStore

    TestCode->>CmdMox: spy("git")
    CmdMox-->>TestCode: CommandDouble
    TestCode->>CommandDouble: passthrough()
    TestCode->>CommandDouble: record("fixtures/git.json", scrubber, env_allowlist)
    CommandDouble->>RecordingSession: __init__(fixture_path, scrubber, env_allowlist)
    CommandDouble->>Passthrough: attach RecordingSession

    TestCode->>CommandDouble: invoke via shim
    CommandDouble->>Passthrough: handle_invocation(invocation)
    Passthrough->>RealCommand: execute(invocation)
    RealCommand-->>Passthrough: stdout, stderr, exit_code
    Passthrough-->>CommandDouble: result
    Passthrough->>RecordingSession: record(invocation, response)
    RecordingSession->>RecordingSession: scrub + env_subset
    RecordingSession->>FixtureStore: append_recording()
    CommandDouble-->>TestCode: response

    TestCode->>CmdMox: verify()
    CmdMox->>RecordingSession: finalize()
    RecordingSession->>FixtureStore: save_fixture()
    CmdMox-->>TestCode: verification_result
```

The replay flow substitutes recorded responses for real command execution:

```mermaid
sequenceDiagram
    autonumber
    participant Test as Test Code
    participant CmdMox as CmdMox Controller
    participant Replay as ReplaySession
    participant Store as FixtureStore

    Test->>CmdMox: spy("git").replay("fixtures/git")
    CmdMox->>Store: Load fixture file
    Store-->>Replay: Parsed Invocation records
    Test->>Replay: Command invocation (via shim)
    Replay->>Replay: Match invocation to recording
    Replay-->>Test: Return recorded output
    Test->>CmdMox: verify()
    CmdMox->>Replay: Validate all recordings consumed
```

The following diagram provides a detailed view of the replay flow, showing the
interactions between `CommandDouble`, `ReplaySession`, `InvocationMatcher`, and
the fixture store, including the matching logic and error handling paths:

```mermaid
sequenceDiagram
    autonumber
    participant TestCode as TestCode
    participant CmdMox as CmdMoxController
    participant CommandDouble as CommandDouble
    participant ReplaySession as ReplaySession
    participant InvocationMatcher as InvocationMatcher
    participant FixtureStore as FixtureStore

    TestCode->>CmdMox: spy("git")
    CmdMox-->>TestCode: CommandDouble
    TestCode->>CommandDouble: replay("fixtures/git.json", strict=True)
    CommandDouble->>ReplaySession: __init__(fixture_path, strict_matching)
    ReplaySession->>FixtureStore: load(fixture_path)
    FixtureStore-->>ReplaySession: FixtureFile

    TestCode->>CommandDouble: invoke via shim
    CommandDouble->>ReplaySession: match(invocation)
    ReplaySession->>InvocationMatcher: find_match(invocation, recordings, consumed)
    InvocationMatcher-->>ReplaySession: index or None
    alt match found
        ReplaySession->>ReplaySession: mark_recording_consumed(index)
        ReplaySession-->>CommandDouble: recorded_response
        CommandDouble-->>TestCode: recorded_response
    else no match and strict_matching
        ReplaySession-->>CommandDouble: None
        CommandDouble->>TestCode: raise UnexpectedCommandError
    else no match and not strict_matching
        ReplaySession-->>CommandDouble: None
        CommandDouble-->>TestCode: fallback_to_other_strategies
    end

    TestCode->>CmdMox: verify()
    CmdMox->>ReplaySession: verify_all_consumed()
    ReplaySession-->>CmdMox: ok_or_error
    CmdMox-->>TestCode: verification_result
```

### 9.3 Fixture Format Specification

Fixtures use JSON format for several reasons: human-readability, native Python
support without dependencies, Git-friendliness (mergeable, diffable), and
compatibility with the existing `Invocation.to_dict()` serialization.

#### 9.3.1 Schema Definition (Version 1.0)

<!-- markdownlint-disable MD013 -->

```json
{
  "$schema": "https://cmdmox.dev/schemas/fixture-v1.json",
  "version": "1.0",
  "metadata": {
    "created_at": "2024-01-15T10:30:00Z",
    "cmdmox_version": "0.5.0",
    "platform": "linux",
    "python_version": "3.13.1",
    "test_module": "tests/test_git_operations.py",
    "test_function": "test_git_clone"
  },
  "recordings": [
    {
      "sequence": 0,
      "command": "git",
      "args": ["clone", "https://github.com/example/repo.git"],
      "stdin": "",
      "env_subset": {
        "GIT_AUTHOR_NAME": "Test User"
      },
      "stdout": "Cloning into 'repo'...\n",
      "stderr": "",
      "exit_code": 0,
      "timestamp": "2024-01-15T10:30:01Z",
      "duration_ms": 1234
    }
  ],
  "scrubbing_rules": [
    {
      "pattern": "\\b[A-Za-z0-9]{40}\\b",
      "replacement": "<GITHUB_TOKEN>",
      "applied_to": ["env", "stdout", "stderr"]
    }
  ]
}
```

<!-- markdownlint-enable MD013 -->

#### 9.3.2 Field Definitions

| Field                      | Type    | Description                              |
| -------------------------- | ------- | ---------------------------------------- |
| `version`                  | string  | Schema version for forward compatibility |
| `metadata.created_at`      | ISO8601 | Fixture creation timestamp               |
| `metadata.cmdmox_version`  | string  | CmdMox version used for recording        |
| `metadata.platform`        | string  | OS platform (linux/darwin/win32)         |
| `metadata.test_module`     | string  | Optional: originating test file          |
| `metadata.test_function`   | string  | Optional: originating test function      |
| `recordings[].sequence`    | int     | Invocation order within session          |
| `recordings[].command`     | string  | Command name                             |
| `recordings[].args`        | list    | Command arguments                        |
| `recordings[].stdin`       | string  | Standard input content                   |
| `recordings[].env_subset`  | dict    | Relevant environment variables           |
| `recordings[].stdout`      | string  | Captured standard output                 |
| `recordings[].stderr`      | string  | Captured standard error                  |
| `recordings[].exit_code`   | int     | Process exit code                        |
| `recordings[].timestamp`   | ISO8601 | When invocation occurred                 |
| `recordings[].duration_ms` | int     | Real execution time                      |
| `scrubbing_rules`          | list    | Applied sanitization rules               |

#### 9.3.3 Environment Variable Subset Strategy

Recording the full environment would bloat fixture files (100+ variables
typical), include system-specific paths that break portability, and potentially
leak sensitive data. Instead, recordings capture an `env_subset` containing:

1. Variables explicitly requested via `.with_env()`
2. Variables matching known command prefixes (e.g., `GIT_*`, `AWS_*`,
   `DOCKER_*`)
3. Variables specified in a configurable allowlist

### 9.4 Module Structure

Record Mode introduces a new subpackage within `cmd_mox`:

```
cmd_mox/
    record/
        __init__.py      # Public API exports
        session.py       # RecordingSession, ReplaySession
        fixture.py       # FixtureFile, FixtureMetadata, RecordedInvocation
        scrubber.py      # Scrubber, ScrubbingRule
        matching.py      # InvocationMatcher for replay
        cli.py           # CLI tool: cmdmox record/replay/generate-test
```

### 9.5 Core Classes

#### 9.5.1 RecordingSession

The `RecordingSession` class manages the capture of passthrough invocations to
fixture files:

```mermaid
classDiagram
    class RecordingSession {
        - Path fixture_path
        - str | list~str~ | None command_filter
        - Scrubber | None scrubber
        - list~str~ env_allowlist
        - list~RecordedInvocation~ _recordings
        - datetime | None _started_at
        - bool _finalized
        + start() None
        + record(invocation: Invocation, response: Response) None
        + finalize() FixtureFile
        + from_passthrough_spy(spy, fixture_path, **kwargs) RecordingSession
    }
```

Key responsibilities:

- Track recording session lifecycle (start, record, finalize)
- Apply scrubbing rules before persisting
- Filter environment variables to the configured subset
- Generate fixture metadata including timestamps and platform info

The following sequence diagram illustrates the complete `RecordingSession`
lifecycle, including environment filtering, optional scrubbing, and idempotent
finalization:

```mermaid
sequenceDiagram
    actor TestCode
    participant Session as RecordingSession
    participant EnvFilter as filter_env_subset
    participant Scrubber as Scrubber
    participant Fixture as FixtureFile
    participant FS as FileSystem

    TestCode->>Session: RecordingSession(fixture_path, scrubber, env_allowlist, command_filter)
    TestCode->>Session: start()
    activate Session

    TestCode->>Session: record(invocation, response, duration_ms)
    alt session not started
        Session-->>TestCode: raise LifecycleError
    else session started and not finalized
        Session->>EnvFilter: filter_env_subset(invocation.env, command, allowlist, explicit_keys=None)
        EnvFilter-->>Session: env_subset
        Session->>Session: build RecordedInvocation
        opt scrubber is not None
            Session->>Scrubber: scrub(recording)
            Scrubber-->>Session: scrubbed_recording
        end
        Session->>Session: append to _recordings
    end

    TestCode->>Session: finalize()
    alt already finalized
        Session-->>TestCode: return existing FixtureFile
    else first finalize
        Session->>Fixture: FixtureFile(version, metadata, recordings, scrubbing_rules)
        Session->>FS: save(fixture_path, FixtureFile)
        FS-->>Session: ok
        Session-->>TestCode: FixtureFile
    end
    deactivate Session
```

#### 9.5.2 ReplaySession

The `ReplaySession` class replays recorded fixtures as mock responses:

```mermaid
classDiagram
    class ReplaySession {
        - Path fixture_path
        - bool strict_matching
        - bool allow_unmatched
        - FixtureFile | None _fixture
        - set~int~ _consumed
        + load() None
        + match(invocation: Invocation) Response | None
        + verify_all_consumed() None
    }
```

Key responsibilities:

- Load and parse fixture files with schema validation
- Match incoming invocations to recorded entries
- Track which recordings have been consumed
- Verify all recordings were replayed during `verify()`

#### 9.5.3 FixtureFile

The `FixtureFile` class represents a persisted fixture with full serialization
support:

```mermaid
classDiagram
    class FixtureFile {
        + str version
        + FixtureMetadata metadata
        + list~RecordedInvocation~ recordings
        + list~ScrubbingRule~ scrubbing_rules
        + load(path: Path) FixtureFile
        + save(path: Path) None
        + to_dict() dict
        + from_dict(data: dict) FixtureFile
    }

    class FixtureMetadata {
        + str created_at
        + str cmdmox_version
        + str platform
        + str python_version
        + str | None test_module
        + str | None test_function
    }

    class RecordedInvocation {
        + int sequence
        + str command
        + list~str~ args
        + str stdin
        + dict~str, str~ env_subset
        + str stdout
        + str stderr
        + int exit_code
        + str timestamp
        + int duration_ms
    }

    FixtureFile *-- FixtureMetadata
    FixtureFile *-- RecordedInvocation
```

#### 9.5.4 Scrubber

The `Scrubber` class sanitizes sensitive data from recordings before
persistence:

```mermaid
classDiagram
    class Scrubber {
        - list~ScrubbingRule~ _rules
        + scrub(recording: RecordedInvocation) RecordedInvocation
        + add_rule(rule: ScrubbingRule) None
        - _default_rules() list~ScrubbingRule~
    }

    class ScrubbingRule {
        + str | Pattern pattern
        + str replacement
        + list~str~ applied_to
        + str description
    }

    Scrubber *-- ScrubbingRule
```

Default scrubbing rules detect and redact:

- GitHub personal access tokens (`ghp_*`, `gho_*`, `ghu_*`, `ghs_*`, `ghr_*`)
- AWS access key IDs (`AKIA*`)
- Generic API keys and tokens (`api_key=*`, `token=*`, `secret=*`)
- Bearer authorization headers
- SSH private keys
- Database connection strings with embedded credentials

#### 9.5.5 InvocationMatcher

The `InvocationMatcher` class handles matching incoming invocations to recorded
entries during replay:

```mermaid
classDiagram
    class InvocationMatcher {
        - bool strict
        - bool match_env
        - bool match_stdin
        + matches(invocation: Invocation, recording: RecordedInvocation) bool
        + find_match(invocation: Invocation, recordings: list, consumed: set) int | None
    }
```

Matching modes:

- **Strict:** Command, args, stdin, and env_subset must match exactly
- **Fuzzy:** Command and args must match; stdin and env are optional

#### 9.5.6 Record Module Class Relationships

The following class diagram shows the relationships between all implemented
record module types, including the `EnvFilter` module-level function and the
`Invocation`/`Response` IPC models consumed during recording:

```mermaid
classDiagram
    class RecordingSession {
        - Path _fixture_path
        - Scrubber | None _scrubber
        - list~str~ _env_allowlist
        - list~str~ | None _command_filter
        - list~RecordedInvocation~ _recordings
        - datetime | None _started_at
        - bool _finalized
        - FixtureFile | None _fixture_file
        + RecordingSession(fixture_path, scrubber, env_allowlist, command_filter)
        + start() void
        + record(invocation, response, duration_ms) void
        + finalize() FixtureFile
    }

    class RecordedInvocation {
        + int sequence
        + str command
        + list~str~ args
        + str stdin
        + dict~str, str~ env_subset
        + str stdout
        + str stderr
        + int exit_code
        + str timestamp
        + int duration_ms
        + to_dict() dict~str, any~
        + from_dict(data) RecordedInvocation
    }

    class FixtureMetadata {
        + str created_at
        + str cmdmox_version
        + str platform
        + str python_version
        + str | None test_module
        + str | None test_function
        + to_dict() dict~str, any~
        + from_dict(data) FixtureMetadata
        + create(test_module, test_function) FixtureMetadata
    }

    class FixtureFile {
        + str version
        + FixtureMetadata metadata
        + list~RecordedInvocation~ recordings
        + list~ScrubbingRule~ scrubbing_rules
        + to_dict() dict~str, any~
        + from_dict(data) FixtureFile
        + save(path) void
        + load(path) FixtureFile
    }

    class ScrubbingRule {
        + str pattern
        + str replacement
        + list~str~ applied_to
        + str description
        + to_dict() dict~str, any~
        + from_dict(data) ScrubbingRule
    }

    class Scrubber {
        <<protocol>>
        + scrub(recording) RecordedInvocation
    }

    class Invocation {
        + str command
        + list~str~ args
        + str stdin
        + dict~str, str~ env
    }

    class Response {
        + str stdout
        + str stderr
        + int exit_code
    }

    class EnvFilter {
        <<module>>
        + filter_env_subset(env, command, allowlist, explicit_keys) dict~str, str~
    }

    RecordingSession --> FixtureFile : creates
    RecordingSession --> RecordedInvocation : aggregates
    RecordingSession ..> Scrubber : optional
    RecordingSession ..> EnvFilter : uses
    FixtureFile o-- FixtureMetadata
    FixtureFile o-- RecordedInvocation
    FixtureFile o-- ScrubbingRule
    Scrubber ..> RecordedInvocation : scrubs
    RecordingSession ..> Invocation : consumes
    RecordingSession ..> Response : consumes
```

### 9.6 Public API Extensions

#### 9.6.1 Fluent API

The `CommandDouble` class gains two new methods for recording and replay:

```python
# Recording passthrough interactions to a fixture
spy = cmd_mox.spy("git").passthrough().record("fixtures/git_clone.json")

# Recording with custom scrubbing rules
scrubber = Scrubber()
scrubber.add_rule(ScrubbingRule(
    pattern=r"--token=\S+",
    replacement="--token=<TOKEN>",
    description="CLI token argument"
))
spy = cmd_mox.spy("aws").passthrough().record(
    "fixtures/aws_s3.json",
    scrubber=scrubber,
    env_allowlist=["AWS_REGION", "AWS_PROFILE"]
)

# Replaying from a recorded fixture
spy = cmd_mox.spy("git").replay("fixtures/git_clone.json")

# Strict replay (fail on any unmatched invocation)
spy = cmd_mox.spy("git").replay("fixtures/git_clone.json", strict=True)
```

#### 9.6.2 Table 3: Record Mode API Methods

<!-- markdownlint-disable MD013 -->

| Method                                      | Purpose                             | Example                                           |
| ------------------------------------------- | ----------------------------------- | ------------------------------------------------- |
| `.record(path, *, scrubber, env_allowlist)` | Enable recording on passthrough spy | `.record("fixtures/git.json")`                    |
| `.replay(path, *, strict)`                  | Load fixture and replay responses   | `.replay("fixtures/git.json")`                    |
| `Scrubber()`                                | Create scrubber with default rules  | `Scrubber()`                                      |
| `Scrubber.add_rule(rule)`                   | Add custom scrubbing rule           | `scrubber.add_rule(rule)`                         |
| `ScrubbingRule(pattern, replacement)`       | Define a scrubbing pattern          | `ScrubbingRule(r"token=\S+", "token=<REDACTED>")` |

<!-- markdownlint-enable MD013 -->

#### 9.6.3 Pytest Markers

Record Mode integrates with pytest through markers for automatic fixture
management:

```python
# Automatically record to fixtures/<test_name>_<command>.json
@pytest.mark.cmdmox_record(fixture_dir="fixtures/")
def test_git_operations(cmd_mox):
    spy = cmd_mox.spy("git").passthrough()
    # Recording happens automatically
    ...

# Automatically replay from fixtures/<test_name>_<command>.json
@pytest.mark.cmdmox_replay(fixture_dir="fixtures/")
def test_git_operations(cmd_mox):
    spy = cmd_mox.spy("git")
    # Replay happens automatically
    ...
```

#### 9.6.4 Context Manager Interface

For explicit control or use outside pytest:

```python
from cmd_mox.record import RecordingContext, ReplayContext

# Recording context captures all passthrough invocations
with cmd_mox.CmdMox() as mox:
    with RecordingContext(mox, "fixtures/session.json") as recording:
        mox.spy("git").passthrough()
        mox.spy("docker").passthrough()
        mox.replay()

        # Execute code under test...

    # Fixture automatically saved on context exit

# Replay context loads fixture and configures spies
with cmd_mox.CmdMox() as mox:
    with ReplayContext(mox, "fixtures/session.json") as replay:
        # Spies automatically configured from fixture
        mox.replay()

        # Execute code under test with recorded responses
```

### 9.7 CLI Tool

Record Mode includes a command-line interface for recording, replaying, and
managing fixtures:

```bash
# Record command interactions during script execution
cmdmox record --output fixtures/session.json -- ./my_script.sh

# Record specific commands only
cmdmox record --commands git,docker --output fixtures/ci.json -- make deploy

# Replay from fixture during script execution
cmdmox replay --fixture fixtures/session.json -- ./my_script.sh

# Generate pytest test code from recorded fixture
cmdmox generate-test --fixture fixtures/session.json --output tests/test_generated.py

# Scrub sensitive data from an existing fixture
cmdmox scrub --fixture fixtures/session.json --rules rules.yaml

# Validate fixture format and schema
cmdmox validate fixtures/*.json
```

### 9.8 Integration with Existing Components

#### 9.8.1 PassthroughCoordinator Extension

The `PassthroughCoordinator` gains an optional `RecordingSession` parameter:

```python
class PassthroughCoordinator:
    def __init__(
        self,
        *,
        cleanup_ttl: float = 300.0,
        recording_session: RecordingSession | None = None,
    ) -> None:
        # ... existing initialization ...
        self._recording_session = recording_session

    def finalize_result(
        self, result: PassthroughResult
    ) -> tuple[CommandDouble, Invocation, Response]:
        """Finalize passthrough and optionally record the interaction."""
        double, invocation, resp = self._finalize_result_internal(result)

        if self._recording_session is not None:
            self._recording_session.record(invocation, resp)

        return double, invocation, resp
```

#### 9.8.2 CommandDouble Extension

The `CommandDouble` class gains recording and replay session attributes:

```python
class CommandDouble:
    # ... existing attributes ...
    _recording_session: RecordingSession | None = None
    _replay_session: ReplaySession | None = None

    def record(
        self,
        fixture_path: str | Path,
        *,
        scrubber: Scrubber | None = None,
        env_allowlist: list[str] | None = None,
    ) -> Self:
        """Enable recording of passthrough invocations to a fixture file."""
        if not self.passthrough_mode:
            raise ValueError("record() requires passthrough(); call it first")
        self._recording_session = RecordingSession(
            fixture_path=Path(fixture_path),
            scrubber=scrubber,
            env_allowlist=env_allowlist or [],
        )
        return self

    def replay(
        self,
        fixture_path: str | Path,
        *,
        strict: bool = True,
    ) -> Self:
        """Load a fixture and replay recorded responses."""
        if self.passthrough_mode:
            raise ValueError("replay() cannot be combined with passthrough()")
        self._replay_session = ReplaySession(
            fixture_path=Path(fixture_path),
            strict_matching=strict,
        )
        self._replay_session.load()
        return self
```

#### 9.8.3 Controller Integration

The `CmdMox` controller integrates replay into the response generation flow:

```python
class CmdMox:
    def _make_response(self, invocation: Invocation) -> Response:
        """Build response, checking replay sessions first."""
        double = self._doubles.get(invocation.command)

        # Check for replay session before other strategies
        if double and double._replay_session:
            matched = double._replay_session.match(invocation)
            if matched:
                return matched
            if double._replay_session.strict_matching:
                raise UnexpectedCommandError(
                    f"No fixture recording matches: {invocation}"
                )

        # Fall through to existing logic
        return self._make_response_original(invocation)

    def verify(self) -> None:
        """Extended verification including replay consumption checks."""
        # ... existing verification ...

        # Verify all replay recordings were consumed
        for name, double in self._doubles.items():
            if double._replay_session:
                double._replay_session.verify_all_consumed()

        # Finalize any active recording sessions
        for name, double in self._doubles.items():
            if double._recording_session and not double._recording_session._finalized:
                double._recording_session.finalize()
```

### 9.9 Security Considerations

#### 9.9.1 Default Scrubbing Patterns

<!-- markdownlint-disable MD013 -->

| Pattern          | Example                           | Replacement                          |
| ---------------- | --------------------------------- | ------------------------------------ |
| GitHub PATs      | `ghp_1234567890abcdef...`         | `<GITHUB_TOKEN>`                     |
| AWS Access Keys  | `AKIAIOSFODNN7EXAMPLE`            | `<AWS_ACCESS_KEY>`                   |
| Generic API keys | `api_key=abc123`                  | `api_key=<REDACTED>`                 |
| Bearer tokens    | `Authorization: Bearer xyz`       | `Authorization: Bearer <REDACTED>`   |
| SSH private keys | `-----BEGIN RSA PRIVATE KEY-----` | `<SSH_PRIVATE_KEY>`                  |
| Database URLs    | `postgres://user:pass@host/db`    | `postgres://user:<REDACTED>@host/db` |

<!-- markdownlint-enable MD013 -->

#### 9.9.2 Environment Variable Filtering

**Excluded by default** (system-specific or sensitive):

- `PATH`, `HOME`, `USER`, `SHELL` (system-specific)
- `SSH_AUTH_SOCK`, `GPG_AGENT_INFO` (session-specific)
- Variables matching `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`

**Included by default**:

- Variables explicitly requested via `.with_env()`
- Variables on the user-defined allowlist
- Variables matching command-specific prefixes (e.g., `GIT_*` for git)

#### 9.9.3 Review Workflow

For sensitive fixtures, a review mode generates a companion file for manual
verification:

```python
spy = cmd_mox.spy("aws").passthrough().record(
    "fixtures/aws.json",
    review_mode=True  # Generates fixtures/aws.json.review
)
```

The review file contains original values alongside scrubbed values, warnings
for potentially sensitive data, and instructions for manual review before
committing the fixture to version control.

**Review File Structure:**

The `.review` file is a human-readable report (not JSON) containing:

1. A header warning that the file contains unscrubbed sensitive data
2. For each recording, a side-by-side comparison of original and scrubbed values
3. Flagged fields where scrubbing rules matched, with rule descriptions
4. A checklist for the reviewer to acknowledge each sensitive field
5. Instructions for deleting the review file after approval

**Storage Practices (MUST follow):**

Review files are **never** intended for version control. Projects using Record
Mode MUST add the following patterns to `.gitignore`:

```gitignore
# CmdMox review files - contain unscrubbed sensitive data
*.review
**/*.review
```

The CLI tool (`cmdmox record --review`) prints a warning reminding developers
to verify ignore patterns are in place. The `cmdmox validate` command can
optionally check for accidentally committed `.review` files.

**Acknowledgement Workflow:**

1. Run recording with `review_mode=True`
2. Inspect the generated `.review` file for unintended sensitive data leakage
3. If scrubbing is insufficient, add custom `ScrubbingRule` entries and
   re-record
4. Once satisfied, delete the `.review` file
5. Commit only the scrubbed `.json` fixture

The fixture file itself includes a `review_acknowledged` timestamp field (set
when the review file is deleted after inspection) to provide an audit trail.

### 9.10 Design Decisions and Trade-offs

#### 9.10.1 JSON vs YAML for Fixtures

**Decision:** JSON

**Rationale:**

- `Invocation.to_dict()` already produces JSON-compatible output
- No additional dependency (YAML requires `pyyaml`)
- Better tooling support (JSON Schema validation, IDE formatters)
- Slightly faster parsing than YAML

**Trade-off:** YAML would be more human-readable for manual editing, but JSON's
tooling advantages outweigh this.

#### 9.10.2 Strict vs Fuzzy Matching

**Decision:** Support both via `strict` parameter

**Rationale:**

- Strict matching ensures deterministic replay for consistent CI
- Fuzzy matching accommodates non-deterministic elements (timestamps, UUIDs)
- Users choose based on their specific requirements

#### 9.10.3 Full Environment vs Subset

**Decision:** Record environment subset only

**Rationale:**

- Full environment is 100+ variables, mostly irrelevant
- Reduces fixture size by approximately 90%
- Improves portability across machines and platforms
- Reduces secret exposure risk

#### 9.10.4 Per-Command vs Session-Level Fixtures

**Decision:** Support both

**Rationale:**

- Per-command fixtures are simpler for single-command tests
- Session fixtures capture cross-command interactions and ordering
- Different use cases require different granularity

#### 9.10.5 Duration Tracking in RecordingSession

**Decision:** Accept `duration_ms` as an optional keyword argument to
`RecordingSession.record()` defaulting to `0`

**Rationale:**

- The `RecordingSession` receives pre-built `(Invocation, Response)` pairs
- Timing is the responsibility of the caller (the passthrough coordinator
  brackets the real command execution)
- Accepting it as a parameter keeps the session stateless with respect to
  timing and avoids coupling to execution infrastructure

#### 9.10.6 Scrubber as Protocol

**Decision:** Define `Scrubber` as a `typing.Protocol` with a single `scrub()`
method

**Rationale:**

- Protocols enable structural subtyping without requiring inheritance
- Any object with a matching `scrub()` method satisfies the type constraint
- Keeps the XII-A recording MVP independent of the XII-C scrubbing
  implementation

#### 9.10.7 Version Detection via importlib.metadata

**Decision:** Retrieve the CmdMox version at runtime via
`importlib.metadata.version("cmd-mox")` with a fallback to `"unknown"`

**Rationale:**

- The project does not expose `__version__` in `cmd_mox/__init__.py`
- `importlib.metadata` is the standard mechanism for accessing installed
  package metadata in Python 3.8+

### 9.11 Versioning and Forward Compatibility

#### 9.11.1 Schema Versioning

Fixtures include a `version` field following semantic versioning:

- Breaking changes increment the major version
- New optional fields increment the minor version
- CmdMox maintains a migration table for old versions

#### 9.11.2 Upgrade Path

```python
class FixtureFile:
    @classmethod
    def load(cls, path: Path) -> FixtureFile:
        data = json.load(path.open())
        version = data.get("version", "0.0")

        if version < "1.0":
            data = migrate_v0_to_v1(data)

        return cls.from_dict(data)
```

### 9.12 Class Relationships Summary

```mermaid
classDiagram
    class CommandDouble {
        + record(path, scrubber) Self
        + replay(path, strict) Self
        - _recording_session RecordingSession
        - _replay_session ReplaySession
    }

    class RecordingSession {
        - Path fixture_path
        - Scrubber scrubber
        - list~str~ env_allowlist
        - list~RecordedInvocation~ _recordings
        + start() None
        + record(invocation, response) None
        + finalize() FixtureFile
    }

    class ReplaySession {
        - Path fixture_path
        - bool strict_matching
        - FixtureFile _fixture
        - set~int~ _consumed
        + load() None
        + match(invocation) Response
        + verify_all_consumed() None
    }

    class FixtureFile {
        + str version
        + FixtureMetadata metadata
        + list~RecordedInvocation~ recordings
        + list~ScrubbingRule~ scrubbing_rules
        + load(path) FixtureFile
        + save(path) None
    }

    class Scrubber {
        - list~ScrubbingRule~ _rules
        + scrub(recording) RecordedInvocation
        + add_rule(rule) None
    }

    class PassthroughCoordinator {
        - RecordingSession _recording_session
        + finalize_result(result) tuple
    }

    CommandDouble --> RecordingSession : _recording_session
    CommandDouble --> ReplaySession : _replay_session
    RecordingSession --> FixtureFile : creates
    RecordingSession --> Scrubber : uses
    ReplaySession --> FixtureFile : loads
    PassthroughCoordinator --> RecordingSession : delegates
```
