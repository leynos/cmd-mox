# CmdMox Usage Guide

CmdMox provides a fluent API for mocking, stubbing and spying on external commands in your tests. This guide shows common patterns for everyday use.

## Getting started

Install the package and enable the pytest plugin:

```bash
pip install cmd-mox
```

In your `conftest.py`:

```python
pytest_plugins = ("cmd_mox.pytest_plugin",)
```

Each test receives a `cmd_mox` fixture that provides access to the controller object.

## Basic workflow

CmdMox follows a strict record → replay → verify lifecycle. First declare expectations, then run your code with the shims active, finally verify that interactions matched what was recorded.

The three phases are defined in the design document:

1. **Record** – describe each expected command call, including its arguments and behaviour.
2. **Replay** – run the code under test while CmdMox intercepts command executions.
3. **Verify** – ensure every expectation was met and nothing unexpected happened.

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

Each call returns a `CommandDouble` that offers a fluent DSL to configure behaviour.

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

which can be combined with `with_matching_args` or `with_stdin` for rich matching logic.

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

Spies expose `invocations` and `call_count` after verification for assertion-style tests:

```python
def test_spy(cmd_mox):
    spy = cmd_mox.spy("curl").returns(stdout="ok")
    cmd_mox.replay()
    run_download()
    cmd_mox.verify()
    assert spy.call_count == 1
```

A spy can also forward to the real command while recording everything:

```python
mox.spy("aws").passthrough()
```

This "record mode" is helpful for capturing real interactions and later turning them into mocks.

## Fluent API reference

The DSL methods closely mirror those described in the design specification. A few common ones are:

- `with_args(*args)` – require exact arguments.
- `with_matching_args(*matchers)` – match arguments using comparators.
- `with_stdin(data)` – expect specific standard input.
- `with_env(mapping)` – set additional environment variables for the invocation.
- `returns(stdout="", stderr="", exit_code=0)` – static response.

*Note: All examples use string values for `stdout` and `stderr`. If you need to return bytes, pass a bytes object such as `stdout=b"output"`. Mixing types is not recommended unless explicitly required and documented.*

- `runs(handler)` – call a function to produce dynamic output.
- `times(count)` – expect the command exactly `count` times.
- `in_order()` – enforce strict ordering with other expectations.
- `passthrough()` – for spies, run the real command while recording it.

Refer to the [design document](./python-native-command-mocking-design.md) for the full table of methods and examples.
