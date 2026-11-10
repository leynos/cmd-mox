# CmdMox user's guide

CmdMox replaces the external commands that code under test runs (`git`, `curl`,
`aws`, and so on) with small shims for the duration of a test. Each shim
reports its call back to the test process, where a mock, stub, or spy decides
the answer and remembers what was asked.

This guide describes CmdMox 0.3.0. It starts with a first test and adds one
idea per section, so reading from the top is the gentlest route. Each section
also stands alone for readers who arrive with a specific question.

| Reader                             | Start here                                                                                                   |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| New to CmdMox                      | [Getting started](#getting-started), then [Choose a double](#choose-a-double)                                |
| Writing everyday tests             | [Describe the calls you expect](#describe-the-calls-you-expect) and [Respond to calls](#respond-to-calls)    |
| Testing code with awkward plumbing | [Run commands the way the code does](#run-commands-the-way-the-code-does)                                    |
| Capturing real behaviour once      | [Passthrough](#run-the-real-command-with-passthrough), then [Record and replay](#record-and-replay-fixtures) |
| Something is not working           | [Troubleshooting](#troubleshooting)                                                                          |
| Upgrading from 0.2                 | The [0.3.0 migration guide](v0-3-0-migration-guide.md)                                                       |
| Coming from `shellmock`            | The [shellmock migration guide](shellmock-migration-guide.md)                                                |

_Table 1: Where to start in this guide._

Every Python example in this guide is executed by the CmdMox test suite, so
each one can be copied into a project as it stands.

## Getting started

### Install CmdMox

CmdMox needs Python 3.12 or newer and runs on Linux, macOS, and Windows. It is
a pytest plugin, so install it alongside pytest:

<!-- shell-example: guide-install-pip -->

```shell
python -m pip install cmd-mox pytest
```

In a [uv](https://docs.astral.sh/uv/) project, add both as development
dependencies:

<!-- shell-example: guide-install-uv -->

```shell
uv add --dev cmd-mox pytest
```

PyPI carries both a wheel and a source distribution. GitHub Releases attach the
same files together with their provenance bundle; see
[ADR 003](adr-003-release-pipeline.md) for how releases are built. On Windows
the package also installs `pywin32`, which provides the named-pipe transport
the shims use there.

### Enable the pytest plugin

CmdMox does not register itself with pytest automatically. Enable it once, in
the `conftest.py` at the root of the test suite:

<!-- conftest-example: guide-conftest -->

```python
pytest_plugins = ("cmd_mox.pytest_plugin",)
```

Every test can then request the `cmd_mox` fixture.

### Write a first test

The function below is the code under test: it asks `git` for the current
branch. The test replaces `git` with a mock, so no repository is needed.

<!-- tested-example: guide-first-test -->

```python
import shutil
import subprocess


def current_branch() -> str:
    """Return the checked-out branch name."""
    result = subprocess.run(
        [shutil.which("git"), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_current_branch(cmd_mox):
    cmd_mox.mock("git").with_args("branch", "--show-current").returns(stdout="main\n")

    assert current_branch() == "main"
```

`shutil.which("git")` is how this guide's examples find a command. It honours
the `PATHEXT` variable on Windows, where shims are `.cmd` files that a plain
`subprocess.run(["git", ...])` would skip. Code that already runs commands
through a shell, or through a library that resolves names this way, needs no
change.

### What happened

CmdMox works in three phases, often summarized as record → replay → verify:

1. **Record.** The test body declares doubles: `mock("git")` and its
   `with_args(...)` and `returns(...)` calls describe what to expect and how to
   answer.
2. **Replay.** CmdMox puts a temporary directory of shims at the front of
   `PATH`. When the code under test runs `git`, it runs a shim, which sends the
   call to the test process over a Unix domain socket (or a named pipe on
   Windows) and prints whatever answer comes back.
3. **Verify.** After the test, CmdMox checks the calls it saw against the
   declared expectations, then removes the shims and restores `PATH`.

The `cmd_mox` fixture starts replay before the test body runs and verifies
during teardown, so a typical test only declares doubles and exercises code.
Doubles declared in the body are live immediately, because CmdMox creates each
shim as it is registered. [Control the lifecycle](#control-the-lifecycle)
covers the alternatives.

The repository's [`examples/`](../examples) directory holds further runnable
tests for stubs, mocks, spies, pipelines, and passthrough. They import a shared
`resolve_command()` helper from `examples/_utils.py`, which is the same
`shutil.which` lookup shown above.

## Choose a double

CmdMox offers three kinds of double. All three are configured with the same
fluent methods; they differ in what verification demands.

- **Stubs** answer calls and are never verified. Use one for a command whose
  output the code needs but whose use is not the point of the test.
- **Mocks** are strict. Each declared mock must be called, with the declared
  arguments and the declared number of times, or verification fails.
- **Spies** answer calls and record every one for inspection. Assertions on
  what happened are written in the test itself.

<!-- tested-example: guide-doubles -->

```python
import shutil
import subprocess


def run(*argv: str) -> str:
    """Run a command and return its standard output."""
    completed = subprocess.run(
        [shutil.which(argv[0]), *argv[1:]], capture_output=True, text=True, check=True
    )
    return completed.stdout


def test_three_doubles(cmd_mox):
    cmd_mox.stub("date").returns(stdout="2026-11-18\n")
    cmd_mox.mock("git").with_args("tag", "v0.3.0").returns()
    notify = cmd_mox.spy("notify-send").returns()

    today = run("date").strip()
    run("git", "tag", "v0.3.0")
    run("notify-send", f"Tagged on {today}")

    assert notify.call_count == 1
    notify.assert_called_with("Tagged on 2026-11-18")
```

Each factory returns a `CommandDouble`. `StubCommand`, `MockCommand`, and
`SpyCommand` are aliases of that class, kept for type annotations that read
well.

## Describe the calls you expect

### Exact arguments

`with_args(*args)` matches the arguments after the program name exactly, in
order. The first test above used it. Arguments are always strings, because that
is what a process receives.

### Flexible arguments with comparators

`with_matching_args(*comparators)` takes one comparator per argument, again
excluding the program name. A comparator is any callable that takes the
argument string and returns `True` on a match. CmdMox ships six:

- `Any()` matches any single argument.
- `IsA(typ)` matches when `typ(argument)` succeeds, so `IsA(int)` matches
  `"42"`. The argument itself stays a string.
- `Regex(pattern)` matches when `re.search` finds the pattern.
- `Contains(substring)` matches when the substring is present.
- `StartsWith(prefix)` matches when the argument begins with the prefix.
- `Predicate(func)` wraps any callable and uses its truthiness.

Every position needs a comparator. A plain string is not treated as a literal
and never matches, so pin a fixed argument with `Regex(r"^clone$")` or a
`Predicate`.

<!-- tested-example: guide-comparators -->

```python
import shutil
import subprocess

from cmd_mox import Any, Contains, IsA, Predicate, Regex, StartsWith


def run(*argv: str) -> str:
    """Run a command and return its standard output."""
    completed = subprocess.run(
        [shutil.which(argv[0]), *argv[1:]], capture_output=True, text=True, check=True
    )
    return completed.stdout


def test_comparators(cmd_mox):
    cmd_mox.mock("git").with_matching_args(Regex(r"^clone$"), Any()).returns()
    cmd_mox.mock("curl").with_matching_args(
        StartsWith("--header=User-Agent:"), Contains("example.com")
    ).returns(stdout="ok")
    cmd_mox.mock("head").with_matching_args(Regex(r"^-n$"), IsA(int)).returns()
    cmd_mox.mock("wc").with_matching_args(Predicate(lambda arg: arg.endswith(".txt"))).returns()

    run("git", "clone", "https://example.com/repo.git")
    assert run("curl", "--header=User-Agent: tests", "https://example.com/") == "ok"
    run("head", "-n", "5")
    run("wc", "notes.txt")
```

### Standard input

`with_stdin(data)` matches the text the command receives on standard input,
either exactly (a string) or with a predicate `Callable[[str], bool]`:

<!-- tested-example: guide-stdin -->

```python
import shutil
import subprocess


def test_sort_receives_lines(cmd_mox):
    cmd_mox.mock("sort").with_stdin("pear\napple\n").returns(stdout="apple\npear\n")
    cmd_mox.mock("wc").with_stdin(lambda text: "apple" in text).returns(stdout="2\n")

    sorted_text = subprocess.run(
        [shutil.which("sort")], input="pear\napple\n", capture_output=True, text=True, check=True
    ).stdout
    count = subprocess.run(
        [shutil.which("wc"), "-l"], input=sorted_text, capture_output=True, text=True, check=True
    ).stdout

    assert (sorted_text, count) == ("apple\npear\n", "2\n")
```

A shim reads standard input until end of file whenever it is not a terminal.
Code that does not mean to send input should close or redirect it, for example
with `stdin=subprocess.DEVNULL`; otherwise the shim waits, and then fails after
a timeout. [Troubleshooting](#a-shim-fails-with-stdin-read-timed-out) has the
details.

### Extra environment variables

`with_env(mapping)` adds variables to the environment the double sees. The
values appear in `Invocation.env` for handlers and spies, and are passed to the
real command in passthrough mode. The test process's own `os.environ` is left
alone.

<!-- tested-example: guide-env -->

```python
import os
import shutil
import subprocess


def test_with_env_reaches_the_handler(cmd_mox):
    cmd_mox.stub("deploy").with_env({"DEPLOY_TARGET": "staging"}).runs(
        lambda invocation: (invocation.env["DEPLOY_TARGET"], "", 0)
    )

    result = subprocess.run([shutil.which("deploy")], capture_output=True, text=True, check=True)

    assert result.stdout == "staging"
    assert "DEPLOY_TARGET" not in os.environ
```

If the calling process already sets one of those variables to a different
value, the invocation fails with an error naming the conflicting keys rather
than silently picking one.

### Call counts and order

Mocks expect exactly one call unless told otherwise. `times(count)` changes
that, and `in_order()` requires a mock's calls to happen in the order the
ordered mocks were declared. `any_order()` restores the default.

<!-- tested-example: guide-counts-and-order -->

```python
import shutil
import subprocess


def run(*argv: str) -> None:
    """Run a command, failing on a non-zero exit status."""
    subprocess.run([shutil.which(argv[0]), *argv[1:]], capture_output=True, check=True)


def test_build_then_upload_twice(cmd_mox):
    cmd_mox.mock("make").with_args("dist").returns().in_order()
    cmd_mox.mock("twine").with_args("upload", "dist/*").returns().times(2).in_order()

    run("make", "dist")
    run("twine", "upload", "dist/*")
    run("twine", "upload", "dist/*")

    assert [call.command for call in cmd_mox.journal] == ["make", "twine", "twine"]
```

Counts and ordering are enforced for mocks only. `times()` and its alias
`times_called()` are accepted on spies, but verification does not check them,
so assert on a spy's `call_count` instead.

### One expectation per command

Each command name maps to one double, and each double holds one argument
expectation. Calling `with_args()` or `with_matching_args()` a second time, or
mixing the two, raises `ExpectationConfigurationError` straight away instead of
silently replacing the first expectation:

<!-- tested-example: guide-one-expectation -->

```python
import pytest

from cmd_mox import ExpectationConfigurationError


def test_second_argument_expectation_is_rejected(cmd_mox):
    git = cmd_mox.stub("git").with_args("status")

    with pytest.raises(ExpectationConfigurationError) as error:
        git.with_args("log")

    assert "runs(" in str(error.value)
```

The error message shows both expectations, with sensitive option values
redacted, and points to the handler pattern in
[Several calls to one command](#several-calls-to-one-command).

## Respond to calls

### Canned output

`returns(stdout="", stderr="", exit_code=0)` gives a fixed answer. CmdMox works
in text mode, so all three output values are strings. For binary payloads, use
passthrough or encode at the boundary (base64, for example).

### Handlers

`runs(handler)` calls a Python function for each invocation. The handler
receives an `Invocation`, whose `args` attribute is a `list[str]`, and returns
either a `(stdout, stderr, exit_code)` tuple or a `Response`:

<!-- tested-example: guide-handler -->

```python
import shutil
import subprocess

from cmd_mox import Response


def handle_tool(invocation):
    if invocation.args == ["run", "--fail"]:
        return Response(stderr="boom\n", exit_code=2)
    return Response(stdout=f"ran {' '.join(invocation.args)}\n")


def test_handler_chooses_the_answer(cmd_mox):
    cmd_mox.stub("tool").runs(handle_tool)
    tool = shutil.which("tool")

    ok = subprocess.run([tool, "run"], capture_output=True, text=True)
    failed = subprocess.run([tool, "run", "--fail"], capture_output=True, text=True)

    assert (ok.returncode, ok.stdout) == (0, "ran run\n")
    assert (failed.returncode, failed.stderr) == (2, "boom\n")
```

Two details are easy to trip over:

- The handler runs in the test process, on an IPC worker thread, not in the
  shim's process. A relative path in `invocation.args` is relative to the
  command's working directory, which `Invocation` does not carry; opening it in
  the handler resolves it against the test process's directory instead. Pass
  absolute paths, or give the handler the working directory some other way.
- An exception raised in a handler becomes a failed command (exit status `1`,
  with the message on stderr), not a pytest error. To fail the test on an
  unexpected call, record it and assert afterwards, as the next example does.

### Several calls to one command

Since a command has one double, a command called several ways is best served by
one handler that dispatches on `invocation.args` with `match`. A spy keeps the
calls in order for assertions:

<!-- tested-example: guide-several-calls -->

```python
import shutil
import subprocess

unexpected_calls: list[list[str]] = []


def handle_cargo(invocation):
    match invocation.args:
        case ["nextest", "--version"]:
            return "cargo-nextest 0.9.0\n", "", 0
        case ["metadata", *_]:
            return '{"packages": []}', "", 0
        case ["nextest", "run", *_]:
            return "", "1 test failed\n", 100
        case unexpected:
            unexpected_calls.append(unexpected)
            return "", f"unexpected cargo arguments: {unexpected}\n", 1


def run_gate_script() -> int:
    """The code under test: check the tool, read metadata, run the tests."""
    cargo = shutil.which("cargo")
    subprocess.run([cargo, "nextest", "--version"], capture_output=True, check=True)
    subprocess.run([cargo, "metadata", "--format-version=1"], capture_output=True, check=True)
    return subprocess.run([cargo, "nextest", "run"], capture_output=True).returncode


def test_gate_script_reports_test_failures(cmd_mox):
    cargo = cmd_mox.spy("cargo").runs(handle_cargo)

    assert run_gate_script() == 100
    assert not unexpected_calls, f"unexpected cargo arguments: {unexpected_calls}"
    assert [invocation.args for invocation in cargo.invocations] == [
        ["nextest", "--version"],
        ["metadata", "--format-version=1"],
        ["nextest", "run"],
    ]
```

## Inspect what ran

### Spies

A spy exposes `invocations` (a list of `Invocation` objects) and `call_count`
during and after the test. Assertion helpers modelled on `unittest.mock` read
naturally in the test body:

- `assert_called()` fails when the spy never ran.
- `assert_not_called()` fails when it ran at all.
- `assert_called_with(*args, stdin=None, env=None)` checks the most recent
  call's arguments and, when given, its standard input and environment.

<!-- tested-example: guide-spy-assertions -->

```python
import shutil
import subprocess


def test_spy_assertions(cmd_mox):
    curl = cmd_mox.spy("curl").returns(stdout="ok")
    wget = cmd_mox.spy("wget")

    subprocess.run([shutil.which("curl"), "--silent"], input="payload", text=True, capture_output=True)

    curl.assert_called()
    curl.assert_called_with("--silent", stdin="payload")
    wget.assert_not_called()
    assert curl.invocations[0].stdin == "payload"
```

These helpers are available on spies only, and raise `AssertionError` with a
description of the mismatch.

### The journal

`cmd_mox.journal` is a `collections.deque` of every `Invocation` seen during
replay, across all doubles, in the order they happened. It is handy for
debugging and for checking an overall sequence:

<!-- tested-example: guide-journal -->

```python
import shutil
import subprocess


def test_journal_shows_the_sequence(cmd_mox):
    cmd_mox.stub("git").returns()
    cmd_mox.stub("curl").returns()

    subprocess.run([shutil.which("git"), "fetch"], check=True)
    subprocess.run([shutil.which("curl"), "-O", "https://example.com/a"], check=True)

    assert [call.command for call in cmd_mox.journal] == ["git", "curl"]
```

### Catch a command that must not run

`register_command(name)` installs a shim without a double. Any call to it is
reported as unexpected at verification, which makes it a safety net for
commands such as `rm` or `kubectl`:

<!-- tested-example: guide-register-command -->

```python
import shutil
import subprocess

import pytest

from cmd_mox import CmdMox, UnexpectedCommandError

with pytest.raises(UnexpectedCommandError, match="rm"):
    with CmdMox() as mox:
        mox.register_command("rm")
        mox.replay()
        subprocess.run([shutil.which("rm"), "-rf", "build"], stdin=subprocess.DEVNULL)

assert [call.args for call in mox.journal] == [["-rf", "build"]]
```

The call itself still succeeds (the shim echoes the command name), so the code
under test carries on and the problem is reported once, at verification. This
example uses the context-manager form described in
[Without pytest](#without-pytest-the-context-manager) so that it can expect the
error; under the fixture, the same failure is reported during teardown.

## Control the lifecycle

### The automatic lifecycle

By default the `cmd_mox` fixture enters replay before the test body and
verifies during teardown. If both the test body and verification fail, the
verification error is suppressed so the original failure is the one reported.
An interrupt such as `KeyboardInterrupt`, or an xdist worker's `SystemExit`, is
recorded as a test failure and re-raised once cleanup has run, so shims and
sockets never leak.

### Manual lifecycle

To call the phases explicitly, turn the automatic lifecycle off with any of
these switches:

- `@pytest.mark.cmd_mox(auto_lifecycle=False)` on a test.
- `False` or `{"auto_lifecycle": False}` as an indirect `cmd_mox` fixture
  parameter.
- `--no-cmd-mox-auto-lifecycle` on the pytest command line
  (`--cmd-mox-auto-lifecycle` turns it back on).
- `cmd_mox_auto_lifecycle = false` in the pytest configuration file.

The most specific switch wins: fixture parameters override markers, markers
override the command line, and the command line overrides the configuration
file.

<!-- tested-example: guide-manual-lifecycle -->

```python
import shutil
import subprocess

import pytest


@pytest.mark.cmd_mox(auto_lifecycle=False)
def test_manual_lifecycle(cmd_mox):
    cmd_mox.mock("git").with_args("fetch").returns()
    cmd_mox.replay()

    subprocess.run([shutil.which("git"), "fetch"], check=True)

    cmd_mox.verify()
    assert cmd_mox.mocks["git"].call_count == 1
```

Calling `replay()` again while already replaying does nothing. Calling it after
`verify()` raises `LifecycleError`, as does any other out-of-order transition.

### Without pytest: the context manager

`CmdMox` is also a context manager, for scripts and other test runners. Inside
the block, call `replay()` before running the code under test. With the default
`verify_on_exit=True`, leaving the block verifies:

<!-- tested-example: guide-context-manager -->

```python
import shutil
import subprocess

from cmd_mox import CmdMox

with CmdMox() as mox:
    mox.stub("ls").returns(stdout="a.txt\nb.txt\n")
    mox.replay()
    listing = subprocess.run(
        [shutil.which("ls")], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True
    ).stdout

assert listing.splitlines() == ["a.txt", "b.txt"]
```

If the block raises, or an interrupt arrives during replay, CmdMox stops the
IPC server, removes the shim directory, and restores `PATH` before the original
error propagates. Verification still runs on exit; if it also fails, its error
is suppressed in favour of the original one.

### Controller options

The `CmdMox` constructor takes keyword-only options:

- `verify_on_exit` (default `True`) verifies when the `with` block ends. With
  `False`, call `verify()` explicitly before leaving the block.
- `max_journal_entries` (default unbounded) caps the journal; the oldest
  entries are dropped first.
- `environment` accepts a prepared `EnvironmentManager`, which exposes the
  shim directory (see
  [Point a tool at a shim by absolute path](#point-a-tool-at-a-shim-by-absolute-path)).
- `lifecycle_hint` appends a sentence of guidance to `LifecycleError`
  messages.

The pytest fixture manages these itself: it verifies through the plugin rather
than `verify_on_exit`, and does not expose the others.

## Run commands the way the code does

### Pipelines and shell syntax

CmdMox intercepts programs, not shell syntax. Pipelines and redirection are the
shell's job, so code that runs a command line through a shell works unchanged:
each program in the pipeline is a separate double.

<!-- tested-example: guide-pipeline -->

```python
import subprocess


def test_pipeline(cmd_mox):
    cmd_mox.mock("grep").with_args("foo", "file.txt").returns(stdout="c a b")
    cmd_mox.mock("sort").with_args("-r").with_stdin("c a b").returns(stdout="c b a")

    result = subprocess.run(
        "grep foo file.txt | sort -r", shell=True, text=True, capture_output=True, check=True
    )

    assert result.stdout == "c b a"
```

### Point a tool at a shim by absolute path

Some tools take a configured executable path instead of looking a name up on
`PATH`: Git merge and diff drivers, `sudoers` rules, systemd units, and
continuous integration (CI) configuration, for example. Pass an
`EnvironmentManager` to `CmdMox`, then use the launcher in its `shim_dir`. The
directory exists once the `with` block is entered, and launchers appear when
replay starts. The launcher has a `.cmd` suffix on Windows and none elsewhere:

<!-- tested-example: guide-absolute-path -->

```python
import os
import subprocess

from cmd_mox import CmdMox, EnvironmentManager

env_mgr = EnvironmentManager()
with CmdMox(environment=env_mgr) as mox:
    driver = mox.spy("stub-merge-driver").returns()
    mox.replay()

    shim = env_mgr.shim_dir / "stub-merge-driver"
    if os.name == "nt":
        shim = shim.with_suffix(".cmd")
    path_without_shims = os.pathsep.join(
        entry for entry in os.environ["PATH"].split(os.pathsep) if entry != str(env_mgr.shim_dir)
    )
    subprocess.run(
        [str(shim), "O", "A", "B", "7", "p.py"],
        env=os.environ | {"PATH": path_without_shims},
        stdin=subprocess.DEVNULL,
        check=True,
    )
    assert driver.call_count == 1
```

The shim finds the test process through the `CMOX_IPC_SOCKET` variable, so it
works even when its directory is missing from `PATH`. Pointing the tool at this
path also means a failed interception cannot quietly fall through to a real
binary of the same name.

The process that starts the shim must still receive `CMOX_IPC_SOCKET` and be
able to reach the socket. Launchers such as `sudo` and systemd often filter the
environment; configure them to pass the variable through, or the shim exits with
`IPC socket not specified`.

### Libraries that snapshot the environment

Plumbum copies `os.environ` when it is imported. Commands it launches may then
miss the shim directory on `PATH` and the `CMOX_IPC_SOCKET` variable, which
shows up as `0 invocations recorded`. Overlay the live environment around the
Plumbum call:

<!-- illustrative-example: guide-plumbum -->

```python
import os

from plumbum import local


def test_with_plumbum(cmd_mox):
    tool = cmd_mox.spy("tool").returns(stdout="ok")
    with local.env(**os.environ):
        local["tool"]()
    assert tool.invocations
```

This example is not executed by the test suite:

- `guide-plumbum`: Plumbum is not a CmdMox dependency, so the suite has no
  copy to import.

With the automatic lifecycle turned off, call `replay()` before entering
`local.env(...)`, so the overlay copies the shim's environment, and call
`verify()` afterwards.

## Run the real command with passthrough

A spy in passthrough mode runs the real command and records the call. The
calling code sees genuine output, and the test can still assert on what ran.
The shim looks the command up on the `PATH` that was in force before CmdMox
added its shims.

For deterministic tests, `CMOX_REAL_COMMAND_<name>` names the executable to run
instead, where `<name>` is the command name exactly as registered (so
`CMOX_REAL_COMMAND_greet` for `spy("greet")`). This example uses it to make
`greet` run the Python interpreter, so it works on any machine:

<!-- tested-example: guide-passthrough -->

```python
import shutil
import subprocess
import sys


def test_passthrough_runs_the_real_command(cmd_mox, monkeypatch):
    monkeypatch.setenv("CMOX_REAL_COMMAND_greet", sys.executable)
    greet = cmd_mox.spy("greet").passthrough()

    result = subprocess.run(
        [shutil.which("greet"), "-c", "print('hello')"], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "hello"
    greet.assert_called_with("-c", "print('hello')")
```

Keep `CMOX_REAL_COMMAND_<name>` for tests. Elsewhere, letting the shim resolve
the command from the original `PATH` avoids masking a misconfigured system.

## Record and replay fixtures

Passthrough can also save what it sees. Recording runs a real command once and
writes its answers to a JSON fixture; replaying feeds those answers back in
later runs, with no network, credentials, or installed tool required.

### Record with `.record()`

Add `.record(fixture_path)` after `.passthrough()`. CmdMox writes the fixture
when verification runs, which is during teardown under the pytest fixture and
on leaving the block for the context manager:

<!-- tested-example: guide-record -->

```python
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cmd_mox import CmdMox
from cmd_mox.record import FixtureFile

fixture_path = Path(tempfile.mkdtemp()) / "greet.json"
os.environ["CMOX_REAL_COMMAND_greet"] = sys.executable

with CmdMox() as mox:
    mox.spy("greet").passthrough().record(fixture_path)
    mox.replay()
    subprocess.run(
        [shutil.which("greet"), "-c", "print('hello')"], capture_output=True, stdin=subprocess.DEVNULL
    )

fixture = FixtureFile.load(fixture_path)
(recording,) = fixture.recordings
assert (recording.command, recording.args, recording.stdout) == ("greet", ["-c", "print('hello')"], "hello\n")
```

`record()` takes two optional keyword arguments:

- `scrubber`: an object with a `scrub(recording) -> RecordedInvocation`
  method, applied to each recording before it is saved.
- `env_allowlist`: environment variable names to save even when the filter
  below would drop them.

Calling `record()` without `passthrough()` first raises `ValueError`.

### Decide what a recording keeps

Each recording stores the command, arguments, standard input, output, exit
status, a timestamp, and a filtered subset of the environment (`env_subset`).
Durations are not measured by `.record()`, so `duration_ms` is `0` in fixtures
it writes. The environment filter works like this:

- CmdMox's own variables (`CMOX_*` and `CMD_MOX_*`) are never saved.
- Names listed in `env_allowlist` are always saved.
- `PATH`, `HOME`, `USER`, `SHELL`, `SSH_AUTH_SOCK`, and `GPG_AGENT_INFO` are
  dropped.
- Sensitive names are dropped: any name containing `SECRET`, `TOKEN`,
  `API_KEY`, or `PASSWORD`, and any name with a segment such as `KEY`, `PASS`,
  `PWD`, `PASSPHRASE`, `CREDENTIAL`, or `AUTH_CONFIG` (so `GITHUB_KEY`,
  `DB_PWD`, and `DOCKER_AUTH_CONFIG` are all dropped).
- For `git`, `aws`, `docker`, `npm`, `pip`, `cargo`, `go`, and `rustc`, only
  variables with that tool's prefix (`GIT_`, `AWS_`, and so on) are kept.
- **For any other command, every remaining variable is kept.**

That last rule matters. A fixture recorded for an unfamiliar command can hold
much of the recording machine's environment: host names, user paths, session
identifiers. That is both a privacy risk and a portability problem, because
strict replay (below) expects those values to be present again. Review fixtures
before committing them, and prefer a scrubber that keeps only what the test
needs:

<!-- tested-example: guide-scrubber -->

```python
import dataclasses as dc
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cmd_mox import CmdMox
from cmd_mox.record import FixtureFile, RecordedInvocation


class KeepOnlyLocale:
    """Drop every recorded variable except LANG, and mask an access key."""

    def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
        return dc.replace(
            recording,
            env_subset={key: value for key, value in recording.env_subset.items() if key == "LANG"},
            stdout=recording.stdout.replace("AKIA", "****"),
        )


fixture_path = Path(tempfile.mkdtemp()) / "greet.json"
os.environ["CMOX_REAL_COMMAND_greet"] = sys.executable
os.environ["LANG"] = "C.UTF-8"

with CmdMox() as mox:
    mox.spy("greet").passthrough().record(fixture_path, scrubber=KeepOnlyLocale())
    mox.replay()
    subprocess.run(
        [shutil.which("greet"), "-c", "print('AKIA1234')"], capture_output=True, stdin=subprocess.DEVNULL
    )

(recording,) = FixtureFile.load(fixture_path).recordings
assert recording.env_subset == {"LANG": "C.UTF-8"}
assert recording.stdout == "****1234\n"
```

### The fixture file format

Fixtures are versioned JSON. This is a complete, minimal fixture:

<!-- fixture-example: guide-fixture-format -->

```json
{
  "version": "1.0.0",
  "metadata": {
    "created_at": "2026-11-18T10:30:00+00:00",
    "cmdmox_version": "0.3.0",
    "platform": "linux",
    "python_version": "3.12.7 (main, Oct  1 2026, 08:00:00) [GCC 14.2.0]"
  },
  "recordings": [
    {
      "sequence": 0,
      "command": "git",
      "args": ["status", "--short"],
      "stdin": "",
      "env_subset": {"GIT_AUTHOR_NAME": "Test User"},
      "stdout": " M file.py\n",
      "stderr": "",
      "exit_code": 0,
      "timestamp": "2026-11-18T10:30:01+00:00",
      "duration_ms": 0
    }
  ],
  "scrubbing_rules": []
}
```

The metadata may also carry `test_module` and `test_function` when they are
known. `version` follows `MAJOR.MINOR.PATCH`; see
[Schema versions](#schema-versions).

### Replay with `.replay()`

`.replay(fixture_path)` attaches a fixture to a spy. The file is loaded at
once, so a missing file, malformed JSON, or an unsupported schema version fails
the test during set-up rather than halfway through. During the test, CmdMox
answers each call from the best-matching unused recording before falling back
to the spy's own `returns()` or `runs()` configuration. Replayed calls still
appear in `spy.invocations` and in the journal.

<!-- tested-example: guide-replay -->

```python
import json
import shutil
import subprocess

RECORDING = {
    "sequence": 0,
    "command": "git",
    "args": ["status", "--short"],
    "stdin": "",
    "env_subset": {},
    "stdout": " M file.py\n",
    "stderr": "",
    "exit_code": 0,
    "timestamp": "2026-11-18T10:30:01+00:00",
    "duration_ms": 0,
}
METADATA = {
    "created_at": "2026-11-18T10:30:00+00:00",
    "cmdmox_version": "0.3.0",
    "platform": "linux",
    "python_version": "3.12.7",
}


def test_replay_answers_from_the_fixture(cmd_mox, tmp_path):
    fixture_path = tmp_path / "git.json"
    fixture_path.write_text(json.dumps({"version": "1.0.0", "metadata": METADATA, "recordings": [RECORDING]}))
    git = cmd_mox.spy("git").replay(fixture_path)

    result = subprocess.run([shutil.which("git"), "status", "--short"], capture_output=True, text=True)

    assert result.stdout == " M file.py\n"
    assert git.call_count == 1
```

`.replay()` takes one option, `strict` (default `True`); pass `strict=False`
for fuzzy matching. It is valid on spies only, cannot be combined with
`passthrough()`, and can be called once per double; the attached session is
available as `replay_session`, and `has_replay_session` says whether there is
one.

### Strict and fuzzy matching

**Strict matching** (the default) requires the command name, the argument list,
and standard input to be equal, and every recorded `env_subset` pair to be
present in the live call's environment. Extra live variables are fine.

**Fuzzy matching** requires only the command name and arguments. Use it when
standard input or the environment carries values that change between runs, such
as timestamps or random identifiers.

When a call matches no recording, strict mode fails the command (exit status
`1`, with `No fixture recording matches: <command> <args>` on stderr); fuzzy
mode falls back to the spy's own configuration.

When several unused recordings match, CmdMox picks one deterministically. In
fuzzy mode it prefers an exact standard-input match first. In both modes it
then prefers more matching environment pairs, then a larger `env_subset`, then
the lowest `sequence`. Each recording answers at most one call, so two
identical recordings answer two identical calls in order.

### Every recording must be used

Verification fails with `VerificationError` if any recording was not consumed,
listing each one by index, command, and arguments. This keeps fixtures honest:
a recording the code no longer triggers is a sign that the code or the fixture
has changed. The lower-level `ReplaySession` below can opt out with
`allow_unmatched=True`.

### Lower-level sessions

`RecordingSession` and `ReplaySession`, both in `cmd_mox.record`, are the
building blocks behind `.record()` and `.replay()`. They are useful for tools
that manage fixtures outside a test, or for replay that tolerates unused
recordings:

<!-- tested-example: guide-sessions -->

```python
import tempfile
from pathlib import Path

from cmd_mox import Invocation, Response
from cmd_mox.record import RecordingSession, ReplaySession

fixture_path = Path(tempfile.mkdtemp()) / "git.json"

recorder = RecordingSession(fixture_path, env_allowlist=["GIT_AUTHOR_NAME"])
recorder.start()
recorder.record(
    Invocation(command="git", args=["status"], stdin="", env={"GIT_AUTHOR_NAME": "Test User"}),
    Response(stdout="clean\n"),
    duration_ms=42,
)
recorder.record(Invocation(command="git", args=["log"], stdin="", env={}), Response(stdout="abc123\n"))
recorder.finalize()

replayer = ReplaySession(fixture_path, strict_matching=False, allow_unmatched=True)
replayer.load()
response = replayer.match(Invocation(command="git", args=["status"], stdin="", env={}))

assert response is not None and response.stdout == "clean\n"
replayer.verify_all_consumed()  # passes: allow_unmatched tolerates the unused "git log"
```

- `RecordingSession(fixture_path, *, scrubber=None, env_allowlist=None)`
  follows `start()`, then any number of
  `record(invocation, response, *, duration_ms=0)` calls, then `finalize()`,
  which writes the file and returns the `FixtureFile`. Calling `finalize()`
  again returns the same object; recording before `start()` or after
  `finalize()` raises `LifecycleError`.
- `ReplaySession(fixture_path, *, strict_matching=True, allow_unmatched=False)`
  needs `load()` before `match()` or `verify_all_consumed()`, and `load()` may
  be called once; both misuses raise `LifecycleError`. `match()` returns a
  `Response`, or `None` when nothing matches.

### Schema versions

`version` uses the numeric `MAJOR.MINOR.PATCH` form, with ASCII digits and no
leading zeroes. The legacy two-part form `1.0`, written by pre-release builds,
still loads and is normalized to `1.0.0`. `FixtureFile.load()` and
`FixtureFile.from_dict()` migrate older fixtures automatically and tolerate
minor and patch differences within a major version, ignoring unknown fields. A
fixture with a higher major version and no migration path raises `ValueError`.

<!-- tested-example: guide-schema-version -->

```python
from cmd_mox.record import FixtureFile

legacy = {
    "version": "1.0",
    "metadata": {
        "created_at": "2026-02-01T00:00:00+00:00",
        "cmdmox_version": "0.3.0",
        "platform": "linux",
        "python_version": "3.12.7",
    },
    "recordings": [],
}

assert FixtureFile.from_dict(legacy).version == "1.0.0"
```

Compatibility runs one way: a release that only understands two-part versions
cannot read a three-part fixture. Upgrade every consumer of a shared fixture
before sharing fixtures written by CmdMox 0.3.0.

## Parallel runs and isolation

Every controller creates its own temporary shim directory, with the IPC socket
inside it, and removes both afterwards. Under pytest the directory name also
includes the xdist worker and process identifiers, so parallel runs never
collide, even on shared filesystems:

<!-- shell-example: guide-xdist -->

```shell
pytest -n auto
```

Each test gets its own `cmd_mox` fixture, and outside pytest each test case
should create its own `CmdMox`. Shims and sockets are never shared.

## Platform support

CmdMox supports Linux, macOS, and Windows. Shims are symbolic links to one
shared Python script on Unix-like systems, and `.cmd` launchers on Windows.

On Windows:

- Resolve commands with `shutil.which`, or run them through a shell, as
  explained in [Write a first test](#write-a-first-test). `CreateProcess` looks
  only for `.exe` files when given a bare name, so a plain
  `subprocess.run(["git", ...])` misses the shim and may run a real `git`.
- CmdMox makes sure `.CMD` is in the effective `PATHEXT` during replay, even if
  a developer's shell removed it.
- Launchers use CRLF line endings and escape carets and percent signs, so
  arguments and paths with spaces or metacharacters survive. Doubled carets
  (`^^`) in arguments are collapsed to one, so an argument that genuinely needs
  repeated carets arrives with a single caret.
- Two command names that differ only in case are rejected with `ValueError`,
  because their launchers would overwrite each other.
- When the shim directory's path would exceed the classic `MAX_PATH` limit,
  CmdMox uses its short (8.3) alias.
- The named-pipe transport bounds concurrent clients, message size, and
  per-client read time.

The package exports `is_supported()`, `unsupported_reason()`, and
`skip_if_unsupported()` for code that wants to gate on platform support. All
three current platforms are supported, so they report support everywhere today:

<!-- tested-example: guide-platform -->

```python
import cmd_mox

assert cmd_mox.is_supported()
assert cmd_mox.unsupported_reason() is None
```

Setting `CMD_MOX_PLATFORM_OVERRIDE` (exported as `PLATFORM_OVERRIDE_ENV`)
changes the platform these helpers evaluate. It does not change how shims or
the IPC transport behave.

## Troubleshooting

### Reading a verification failure

Verification failures show what was expected beside what happened. A mock that
was never called produces:

<!-- tested-example: guide-failure-message -->

```python
import pytest

from cmd_mox import CmdMox, UnfulfilledExpectationError

with pytest.raises(UnfulfilledExpectationError) as failure:
    with CmdMox() as mox:
        mox.mock("git").with_args("status").returns(stdout="clean")
        mox.replay()

message = str(failure.value)
assert "Expected:\n  git('status')" in message
assert "Observed calls:\n  0 (expected 1)" in message
```

A call with the wrong arguments is reported as an `UnexpectedCommandError`, with
`Expected`, `Actual`, and a `Reason` such as
`arguments ['push'] != ['status']`. Note that a mismatched call still gets the
mock's configured answer while the test runs, so the code under test may carry
on happily until verification reports the mismatch.

### A double records no invocations

When a test reports `0 invocations recorded` but the code clearly ran the
command:

- On Windows, check that the command is resolved with `shutil.which` or a
  shell (see [Platform support](#platform-support)).
- Check that the code under test inherits the live environment. A library that
  copies `os.environ` early (see
  [Libraries that snapshot the environment](#libraries-that-snapshot-the-environment))
  or a launcher that filters it, such as `sudo`, hides the shim.
- Check that the code does not call the command by an absolute path such as
  `/usr/bin/git`. Shims only take over names looked up on `PATH`.

### A shim fails with "stdin read timed out"

A shim reads standard input until end of file whenever input is not a terminal.
If the caller leaves a pipe open and never closes it, the shim gives up after
`CMOX_IPC_TIMEOUT` seconds (five by default), discards the partial input, writes
`IPC error: stdin read timed out after <timeout> seconds` to stderr, and exits
with status `1` without reaching the test process.

This most often happens when a script, rather than pytest, runs the code under
test and the child inherits the script's own standard input. Pass
`stdin=subprocess.DEVNULL`, supply the input with `input=...`, or otherwise
close the pipe. On Windows the cancelled read is given up to about a second to
unwind, so the exit follows the deadline closely rather than exactly.

### Other shim errors

- `IPC socket not specified`: the shim ran without `CMOX_IPC_SOCKET`, usually
  because a launcher filtered the environment or replay had not started.
- `IPC error: ...` with another reason: the shim could not connect, or the
  exchange timed out.
- A non-empty request that the test process cannot parse gets a short error
  on stderr and exit status `1`. An empty connection, such as a readiness
  probe, is treated as closed and gets no reply.
- If reporting a passthrough result fails, the shim keeps the real command's
  non-zero exit status.

### Sensitive values in diagnostics

Messages from `UnexpectedCommandError`, verification failures, and spy
assertions replace sensitive environment values with `***`, while keeping the
variable name visible. The names treated as sensitive are the same as in
[Decide what a recording keeps](#decide-what-a-recording-keeps), so
`GITHUB_KEY`, `DB_PWD`, and `DOCKER_AUTH_CONFIG` are masked along with
`*_SECRET`, `*_TOKEN`, `*_PASSWORD`, and `*_API_KEY`. Matching and recorded
data are unchanged; only the message is masked. The `repr` of an `Invocation`
uses `<redacted>` for the same keys. Formatting a `Response` or `Expectation`
directly is not covered by this masking.

## Use the IPC server directly

Most projects never touch the IPC layer, but tools built on CmdMox can run a
server of their own. `IPCServer` (Unix domain sockets) and `NamedPipeServer`
(Windows) take an optional `IPCHandlers` object whose `handler` answers
invocations and whose `passthrough_handler` handles completed passthrough runs.
`invoke_server` is the client side that the shim uses:

<!-- tested-example: guide-ipc-server -->

```python
import os

from cmd_mox import EnvironmentManager
from cmd_mox.ipc import (
    IPCHandlers,
    IPCServer,
    Invocation,
    NamedPipeServer,
    Response,
    invoke_server,
)


def handle(invocation: Invocation) -> Response:
    return Response(stdout=f"handled {invocation.command}")


Server = NamedPipeServer if os.name == "nt" else IPCServer

with EnvironmentManager() as env, Server(env.socket_path, handlers=IPCHandlers(handler=handle)):
    response = invoke_server(Invocation(command="git", args=["status"], stdin="", env={}), timeout=5.0)

assert response.stdout == "handled git"
```

Without a `handler`, the server echoes the command name. Without a
`passthrough_handler`, a passthrough result is answered with an error.

For behaviour that callbacks cannot express, subclass the server and override
`handle_invocation()` and `handle_passthrough_result()`; both transports
dispatch through these hooks. `CallbackIPCServer` and `CallbackNamedPipeServer`
take the two callbacks as constructor arguments, and all servers accept
`timeouts=TimeoutConfig(timeout=5.0, accept_timeout=None)` to tune request and
accept timeouts.

On Unix the server picks `socketserver.ThreadingUnixStreamServer` when it first
starts, falling back to `UnixStreamServer` with `ThreadingMixIn`, and raises
`RuntimeError` if neither exists. Both transports emit bounded, structured
observability events with correlation identifiers; the
[design document](python-native-command-mocking-design.md) lists the event
vocabulary and the Windows limits.

The domain values `Invocation`, `Response`, `PassthroughRequest`, and
`PassthroughResult` no longer convert themselves to JSON. Use the functions in
`cmd_mox.ipc.wire` (`invocation_to_payload`, `response_to_payload`,
`response_from_payload`, and `passthrough_to_payload`) when building payloads
by hand.

## Environment variables

CmdMox sets these variables during replay. Most tests never need to touch them.

- `CMOX_IPC_SOCKET`: where the shim finds the test process. Entering an
  `EnvironmentManager` sets it. On Windows it is a logical path that the IPC
  layer maps to a named pipe. A shim without it exits with an error.
- `CMOX_IPC_TIMEOUT`: the timeout in seconds, exported when the server starts
  (default `5.0`). It bounds standard-input capture on every platform, as
  described in [Troubleshooting](#a-shim-fails-with-stdin-read-timed-out). On
  POSIX, the socket exchange that follows (connecting, sending, receiving, and
  reporting passthrough results) runs against a separate deadline that started
  when the shim did, so slow input capture leaves it correspondingly less time.
  On Windows the named-pipe exchange keeps its own cooperative timeout.
- `CMOX_REAL_COMMAND_<name>`: for passthrough spies, the executable to run
  instead of searching `PATH`. Intended for tests only.
- `CMD_MOX_PLATFORM_OVERRIDE`: the platform the support helpers evaluate.

## Fluent API reference

Every double offers these methods. Each returns the double, so calls chain.

- `with_args(*args)`: require exact arguments.
- `with_matching_args(*comparators)`: match arguments with one comparator each.
- `with_stdin(data_or_predicate)`: match standard input exactly or with a
  predicate.
- `with_env(mapping)`: add variables to the invocation's environment.
- `returns(stdout="", stderr="", exit_code=0)`: answer with fixed text.
- `runs(handler)`: answer with `handler(invocation)`, returning a tuple or a
  `Response`.
- `times(count)` and `times_called(count)`: expect exactly `count` calls
  (verified for mocks).
- `in_order()` and `any_order()`: opt into or out of ordered verification.
- `passthrough()`: for spies, run the real command.
- `record(fixture_path, *, scrubber=None, env_allowlist=None)`: for
  passthrough spies, save calls to a fixture.
- `replay(fixture_path, *, strict=True)`: for spies, answer from a fixture.

Spies also offer `invocations`, `call_count`, `assert_called()`,
`assert_not_called()`, and `assert_called_with(*args, stdin=None, env=None)`.
The [design document](python-native-command-mocking-design.md) has the full
rationale.

## Public API reference

<!-- api-reference:start -->

These symbols are exported from the `cmd_mox` package itself, mirroring
`cmd_mox.__all__`.

### Controller and test doubles

- `CmdMox` – controller implementing record → replay → verify.
- `CommandDouble` – the fluent test double returned by the factory methods.
- `StubCommand`, `MockCommand`, `SpyCommand` – aliases of `CommandDouble` for
  annotations.
- `Expectation` – the argument, stdin, and environment expectation behind a
  double.
- `Invocation` – a captured call: command, args, stdin, env, and results.
- `Response` – an answer: stdout, stderr, exit code, and environment.

### Matchers (comparators)

- `Any` – match any argument.
- `IsA` – match values convertible to a type.
- `Regex` – regular-expression search.
- `Contains` – substring match.
- `StartsWith` – prefix match.
- `Predicate` – wrap a custom predicate.

### IPC servers

- `IPCServer` – Unix-domain-socket server.
- `NamedPipeServer` – Windows named-pipe server.

### Shims and environment helpers

- `EnvironmentManager` – manages the shim directory, `PATH`, and IPC
  variables.
- `temporary_env` – context manager that temporarily sets `os.environ`
  entries.
- `create_shim_symlinks` – low-level helper that generates shims.
- `SHIM_PATH` – path to the shared `shim.py` entry point.

### Platform helpers

- `is_supported` – whether the platform is supported.
- `skip_if_unsupported` – `pytest.skip()` on an unsupported platform.
- `unsupported_reason` – why the platform is unsupported, or `None`.
- `PLATFORM_OVERRIDE_ENV` – the override variable's name
  (`CMD_MOX_PLATFORM_OVERRIDE`).

### Exceptions

- `CmdMoxError` – base class for CmdMox errors.
- `ExpectationConfigurationError` – an argument expectation was set twice on
  one double.
- `LifecycleError` – an invalid record, replay, or verify transition.
- `MissingEnvironmentError` – replay cannot start because its environment is
  missing.
- `UnexpectedCommandError` – a command ran without a matching double.
- `UnfulfilledExpectationError` – expected calls did not happen.
- `VerificationError` – base class for verification failures.

### Pytest helper

- `cmd_mox_fixture` – the pytest `cmd_mox` fixture function (loaded lazily).

<!-- api-reference:end -->

The record-and-replay API lives in `cmd_mox.record` (`RecordingSession`,
`ReplaySession`, `FixtureFile`, `RecordedInvocation`, `Scrubber`, and
`filter_env_subset`), and the IPC layer in `cmd_mox.ipc` and `cmd_mox.ipc.wire`.
