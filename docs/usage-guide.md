# CmdMox Usage Guide

CmdMox provides a fluent API for mocking, stubbing and spying on external
commands in your tests. This guide shows common patterns for everyday use.

## Getting started

Install the package and enable the pytest plugin:

```bash
pip install cmd-mox
```

In your `conftest.py`:

```python
pytest_plugins = ("cmd_mox.pytest_plugin",)
```

Each test receives a `cmd_mox` fixture that provides access to the controller
object.

## Basic workflow

CmdMox follows a strict record → replay → verify lifecycle. First declare
expectations, then run your code with the shims active, finally verify that
interactions matched what was recorded.

The three phases are defined in the design document:

1. **Record** – describe each expected command call, including its arguments
   and behaviour.
2. **Replay** – run the code under test while CmdMox intercepts command
   executions.
3. **Verify** – ensure every expectation was met and nothing unexpected
   happened.

These phases form a strict sequence for reliable command-line tests.

A typical test brings the three phases together:

```python
cmd_mox.mock("git").with_args("clone", "repo").returns(exit_code=0)

cmd_mox.replay()
my_tool.clone_repo("repo")
cmd_mox.verify()
```

## Stubs, mocks and spies

Use the controller to register doubles:

```python
cmd_mox.stub("ls")
cmd_mox.mock("git")
cmd_mox.spy("curl")
```

- **Stubs** provide canned responses without strict checking.
- **Mocks** enforce exact usage during verification.
- **Spies** record every call for later inspection and can behave like stubs.

Each call returns a `CommandDouble` that offers a fluent DSL to configure
behaviour.

## Defining expectations

Combine methods to describe how a command should be invoked:

```python
cmd_mox.mock("git") \
    .with_args("clone", "https://example.com/repo.git") \
    .returns(exit_code=0)
```

You can match arguments more flexibly using comparators:

```python
from cmd_mox import Regex, Contains

cmd_mox.mock("curl") \
    .with_matching_args(Regex(r"--header=User-Agent:.*"), Contains("example"))
```

The design document lists the available comparators:

- `Any`
- `IsA`
- `Regex`
- `Contains`
- `StartsWith`
- `Predicate`

Each comparator is a callable that returns `True` on match.
`with_matching_args` expects one comparator per argv element (excluding the program name, i.e., `argv[1:]`),
and `with_stdin` accepts either an exact string or a predicate `Callable[[str], bool]`
for flexible input checks.

## Running tests

Typical pytest usage looks like this:

```python
def test_clone(cmd_mox):
    cmd_mox.mock("git").with_args("clone", "repo").returns(exit_code=0)

    cmd_mox.replay()
    my_tool.clone_repo("repo")
    cmd_mox.verify()
```

The context manager interface is available when pytest fixtures are not in play:

```python
with CmdMox() as mox:
    mox.stub("ls").returns(stdout="")
    mox.replay()
    subprocess.run(["ls"], check=True)
```

## Spies and passthrough mode

Spies expose `invocations` (a list of `Invocation` objects) and `call_count`
during and after replay, making it easy to inspect what actually ran:

```python
def test_spy(cmd_mox):
    spy = cmd_mox.spy("curl").returns(stdout="ok")
    cmd_mox.replay()
    run_download()
    cmd_mox.verify()
    assert spy.call_count == 1
```

A spy expectation can also use `times_called(count)`—an alias of
`times(count)`—to require a specific call count during verification.

A spy can also forward to the real command while recording everything:

```python
mox.spy("aws").passthrough()
```

This "record mode" is helpful for capturing real interactions and later turning
them into mocks.

After verification, spies provide assertion helpers inspired by
`unittest.mock`:

```python
spy.assert_called()
spy.assert_called_with("--silent", stdin="payload")
# or, to ensure the spy never executed:
spy.assert_not_called()
```

These methods raise `AssertionError` when expectations are not met and are
restricted to spy doubles.

## Controller configuration and journals

`CmdMox` offers configuration hooks that surface through both the fixture and
the context-manager API:

- `verify_on_exit` (default `True`) automatically calls `verify()` when a replay
  phase ends inside a `with CmdMox()` block. Disable it when you need to manage
  verification manually. Verification still runs if the body raises; when both
  verification and the body fail, the verification error is suppressed so the
  original exception surfaces.
- `max_journal_entries` bounds the number of stored invocations (oldest entries
  are evicted FIFO when the bound is reached). The journal is exposed via
  `cmd_mox.journal`, a `collections.deque[Invocation]` recorded during replay.

The journal is especially handy when debugging:

```python
cmd_mox.replay()
exercise_system()
cmd_mox.verify()
assert [call.command for call in cmd_mox.journal] == ["git", "curl"]
```

When you want to intercept a command without configuring a double—for example to
ensure it is treated as unexpected—register it explicitly:

```python
cmd_mox.register_command("name")
```

CmdMox will create the shim so the command is routed through the IPC server even
without a stub, mock, or spy.

## Fluent API reference

The DSL methods closely mirror those described in the design specification. A
few common ones are:

- `with_args(*args)` – require exact arguments.
- `with_matching_args(*matchers)` – match arguments using comparators.
- `with_stdin(data_or_matcher)` – expect specific standard input (`str`) or
  validate it with a predicate `Callable[[str], bool]`.
- `with_env(mapping)` – set additional environment variables for the invocation
  and apply them when custom handlers run.
- `returns(stdout="", stderr="", exit_code=0)` – static response using text
  values; CmdMox operates in text mode—pass `str` (bytes are not supported).
  Note: For binary payloads, prefer `passthrough()` or encode/decode at the
  boundary (e.g., base64) so handlers exchange `str`.
- `runs(handler)` – call a function to produce dynamic output. The handler
  receives an `Invocation` and should return either a `(stdout, stderr,
  exit_code)` tuple or a `Response` instance.
- `times(count)` – expect the command exactly `count` times.
- `times_called(count)` – alias for `times` that emphasises spy call counts.
- `in_order()` – enforce strict ordering with other expectations.
- `any_order()` – allow the expectation to be satisfied in any position.
- `passthrough()` – for spies, run the real command while recording it.
- `assert_called()`, `assert_not_called()`, `assert_called_with(*args,
  stdin=None, env=None)` – spy-only helpers for post-verification assertions.

Refer to the [design document](./python-native-command-mocking-design.md) for
the full table of methods and examples.

## Environment variables

CmdMox exposes two environment variables to coordinate shims with the IPC
server.

- `CMOX_IPC_SOCKET` – path to the Unix domain socket used by shims. The
  `CmdMox` fixture sets this automatically when the server starts. Shims exit
  with an error if the variable is missing.
- `CMOX_IPC_TIMEOUT` – communication timeout in seconds. Override this to tune
  connection waits. When unset, the default is `5.0` seconds.

Most tests should rely on the fixture to manage these variables.
