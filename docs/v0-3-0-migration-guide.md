# CmdMox 0.3.0 migration guide

This guide covers upgrading a test suite from CmdMox 0.2 to 0.3.0. The breaking
changes come first, each with the fix; most suites need only the first one or
two. New features follow, for readers who want to know what the upgrade offers.
The [changelog](CHANGELOG.md) lists every change with links to the pull
requests behind it.

CmdMox 0.3.0 is currently in beta. Installers skip pre-releases unless asked,
so request it explicitly with `python -m pip install "cmd-mox>=0.3.0b1"` or
`uv add --dev "cmd-mox>=0.3.0b1"`; the same requirement keeps working once
0.3.0 itself is released.

| Change                                                                      | Who is affected                                    |
| --------------------------------------------------------------------------- | -------------------------------------------------- |
| [Python 3.12 or newer](#python-312-or-newer-is-required)                    | Suites still running on Python 3.11                |
| [One argument expectation per double](#one-argument-expectation-per-double) | Tests that call `with_args()` twice on one command |
| [Bounded stdin capture](#shims-stop-waiting-for-standard-input)             | Code that runs commands with an open stdin pipe    |
| [IPC conversion methods](#ipc-values-no-longer-convert-themselves)          | Tools that build IPC payloads by hand              |
| [Broader secret masking](#more-environment-values-are-masked-in-messages)   | Tests that assert on the text of CmdMox errors     |
| [Bounded Windows named pipes](#windows-named-pipes-are-bounded)             | Windows tools that hold many or slow pipe clients  |
| [Moved internal names](#internal-names-moved)                               | Code importing CmdMox private modules              |
| [Fixture versions](#recording-fixtures-from-pre-release-builds)             | Users of unreleased record-mode builds from `main` |

_Table 1: Breaking changes in CmdMox 0.3.0 and who they affect._

## Breaking changes

### Python 3.12 or newer is required

CmdMox 0.3.0 uses Python 3.12 syntax and no longer installs on Python 3.11. Pip
on Python 3.11 keeps resolving to CmdMox 0.2.0, so an unchanged 3.11 suite
keeps working, but does not receive this release. Move the test environment to
Python 3.12 or newer before upgrading.

### One argument expectation per double

In 0.2, calling `with_args()` twice on one double silently kept the last call,
and mixing `with_args()` with `with_matching_args()` applied both. Both
patterns usually hid a mistake: a second expectation for a second call that
never actually took effect. CmdMox 0.3.0 raises `ExpectationConfigurationError`
at the second call, with a message showing both expectations.

A command that is called more than one way needs one double with a handler that
dispatches on the arguments:

<!-- tested-example: migration-one-handler -->

```python
import shutil
import subprocess


def handle_git(invocation):
    match invocation.args:
        case ["fetch"]:
            return "", "", 0
        case ["rev-parse", "HEAD"]:
            return "abc123\n", "", 0
        case unexpected:
            return "", f"unexpected git arguments: {unexpected}\n", 1


def test_two_git_calls(cmd_mox):
    git = cmd_mox.spy("git").runs(handle_git)

    subprocess.run([shutil.which("git"), "fetch"], check=True)
    head = subprocess.run(
        [shutil.which("git"), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout

    assert head == "abc123\n"
    assert [call.args for call in git.invocations] == [["fetch"], ["rev-parse", "HEAD"]]
```

The user's guide section
[Several calls to one command](users-guide.md#several-calls-to-one-command)
shows a fuller version.

### Shims stop waiting for standard input

A shim reads standard input until end of file whenever it is not a terminal. In
0.2 that read had no limit, so a caller that left a pipe open hung the test. In
0.3.0 the read is bounded by `CMOX_IPC_TIMEOUT` (five seconds by default) on
every platform. When the deadline passes, the shim discards the partial input,
does not contact the test process, prints
`IPC error: stdin read timed out after <timeout> seconds`, and exits with status
`1`.

A test that used to hang now fails quickly with that message. The fix is the
same as before: make sure the command's input ends. Pass
`stdin=subprocess.DEVNULL` when no input is intended, or `input=...` when it is:

<!-- tested-example: migration-close-stdin -->

```python
import shutil
import subprocess


def test_input_is_closed(cmd_mox):
    cmd_mox.mock("tool").with_stdin("").returns(stdout="done\n")

    result = subprocess.run(
        [shutil.which("tool")], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True
    )

    assert result.stdout == "done\n"
```

On POSIX, the socket exchange that follows input capture now runs against a
deadline that starts with the shim, so a slow capture leaves it correspondingly
less time. On Windows the cancelled read is given up to about a second to
unwind, and the named-pipe exchange keeps its own timeout.
[Troubleshooting](users-guide.md#a-shim-fails-with-stdin-read-timed-out) has
the full description.

### IPC values no longer convert themselves

`Invocation`, `Response`, `PassthroughRequest`, and `PassthroughResult` are
still importable from `cmd_mox.ipc` (and `Invocation` and `Response` from
`cmd_mox`), but their JSON conversion methods have moved to functions in
`cmd_mox.ipc.wire`. The payload format itself is unchanged.

| 0.2 method                       | 0.3.0 replacement                   |
| -------------------------------- | ----------------------------------- |
| `invocation.to_dict()`           | `invocation_to_payload(invocation)` |
| `response.to_dict()`             | `response_to_payload(response)`     |
| `Response.from_payload(payload)` | `response_from_payload(payload)`    |
| `request.to_dict()`              | `passthrough_to_payload(request)`   |
| `result.to_dict()`               | `passthrough_to_payload(result)`    |

_Table 2: Replacements for the removed IPC conversion methods._

<!-- tested-example: migration-wire-functions -->

```python
from cmd_mox.ipc import Invocation, Response
from cmd_mox.ipc.wire import invocation_to_payload, response_from_payload, response_to_payload

payload = invocation_to_payload(Invocation(command="git", args=["status"], stdin="", env={}))
assert payload["command"] == "git" and payload["args"] == ["status"]

response = Response(stdout="clean\n", exit_code=0)
assert response_from_payload(response_to_payload(response)) == response
```

### More environment values are masked in messages

Error messages already hid values for keys containing `SECRET`, `TOKEN`,
`API_KEY`, or `PASSWORD`. In 0.3.0 they also mask segment-style names such as
`DB_PWD`, `GITHUB_KEY`, and `DOCKER_AUTH_CONFIG`, and the masking now covers
`UnexpectedCommandError` conflict messages and spy assertion messages as well
as verifier failures. Masked values read `***`; the key stays visible.

Only the message text changes. A test that matches the full text of a CmdMox
error containing one of these values needs its expected text updated. The
user's guide lists
[the full vocabulary](users-guide.md#decide-what-a-recording-keeps).

### Windows named pipes are bounded

The Windows named-pipe server now limits concurrent clients (64), message size,
and per-client read time (30 seconds). Ordinary tests stay well within these
limits. A tool that opens many simultaneous shim connections, or keeps one open
without writing, may now see the connection refused or closed.

### Internal names moved

These names were never part of the documented API, but were importable:

- `RobustRmtreeError` moved from `cmd_mox.environment` to `cmd_mox.fs_retry`.
- `SENSITIVE_ENV_KEY_TOKENS` moved from `cmd_mox.expectations` to
  `cmd_mox.domain`.
- `cmd_mox.ipc.models` now re-exports the domain values from
  `cmd_mox.domain`; imports through `cmd_mox.ipc` are unaffected.

### Recording fixtures from pre-release builds

Record and replay are new in 0.3.0, so suites upgrading from the 0.2.0 release
have no fixtures to migrate. Builds from `main` before the release wrote a
two-part fixture `version` such as `1.0`. CmdMox 0.3.0 writes `1.0.0`, still
reads `1.0` (normalizing it to `1.0.0`), and tolerates minor and patch
differences within a major version.

Compatibility runs one way: a build that requires two-part versions cannot read
a fixture written by 0.3.0. Upgrade every consumer of a shared fixture before
sharing fixtures written by 0.3.0, keep a copy of existing fixtures, and do not
expect a lossless downgrade. See
[Schema versions](users-guide.md#schema-versions) for the full rules.

## New in 0.3.0

None of these needs a change to existing tests:

- **Record and replay.** `.passthrough().record(path)` saves real command
  output to a versioned JSON fixture, and `.replay(path)` answers from it in
  later runs. Verification fails if a replayed fixture has unused recordings.
  Start with
  [Record and replay fixtures](users-guide.md#record-and-replay-fixtures).
- **`ExpectationConfigurationError`** is exported from `cmd_mox`.
- **`lifecycle_hint`.** `CmdMox(lifecycle_hint=...)` appends guidance to
  `LifecycleError` messages; the pytest plugin uses it to explain its own
  lifecycle switches.
- **Idempotent `replay()`.** Calling `replay()` again while already replaying
  does nothing, instead of raising.
- **Sturdier teardown.** The fixture preserves `KeyboardInterrupt` and
  `SystemExit` raised during a test, and still cleans up.
