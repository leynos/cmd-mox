# 🕵️‍♀️ CmdMox

[![Ask DeepWiki][dw]][deepwiki] [![PyPI Version][pypi]][package]

[dw]: https://deepwiki.com/badge.svg
[deepwiki]: https://deepwiki.com/leynos/cmd-mox
[pypi]: https://img.shields.io/pypi/v/cmd-mox "PyPI package"
[package]: https://pypi.org/project/cmd-mox/

*Python-native command mocking for pytest—so you never have to write another
shell test again.*

CmdMox swaps the external programs your code runs (`git`, `curl`, `aws`, and
friends) for small shims during a test. Each shim reports back to your pytest
process, where your mocks, stubs, and spies decide what happens and remember
what was asked of them.

______________________________________________________________________

## Why CmdMox?

Replace your flaky bats tests, your brittle log-parsing hacks, and that one
Bash script that only works on Tuesdays.

- **Plain Python tests**: Expectations are declared with a fluent API in the
  same pytest suite as everything else. No subshells, no `grep`, no `sed`.
- **Mocks, stubs, and spies**: Mocks are verified, stubs are quietly
  compliant, and spies judge everything you do (then let you assert on it).
- **Precise failures**: When your code misbehaves, you get a diff of what was
  expected against what actually ran, not a mysterious exit status.
- **Record once, replay forever**: Capture real command output to a fixture
  file and replay it in later runs, with no network or credentials required.
- **Linux, macOS, and Windows**: The same tests and fixtures run on all three.

If you've ever mocked `curl` with `cat`, this library is your penance.

______________________________________________________________________

## Quick start

### Installation

CmdMox needs Python 3.12 or newer.

<!-- shell-example: readme-install-pip -->

```shell
python -m pip install cmd-mox
```

Or add it to the development dependencies of a [uv](https://docs.astral.sh/uv/)
project:

<!-- shell-example: readme-install-uv -->

```shell
uv add --dev cmd-mox
```

Then enable the pytest plugin in your `conftest.py`:

<!-- conftest-example: readme-conftest -->

```python
pytest_plugins = ("cmd_mox.pytest_plugin",)
```

### Your first test

Say your code asks `git` for the latest tag. This test replaces `git` with a
mock, so nothing touches a real repository:

<!-- tested-example: readme-quick-start -->

```python
import shutil
import subprocess


def latest_tag() -> str:
    """The code under test: ask git for the most recent tag."""
    git = shutil.which("git")  # finds CmdMox's shim first, on every platform
    result = subprocess.run(
        [git, "describe", "--tags", "--abbrev=0"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_latest_tag(cmd_mox):
    cmd_mox.mock("git").with_args("describe", "--tags", "--abbrev=0").returns(stdout="v1.2.3\n")

    assert latest_tag() == "v1.2.3"
```

Three things happened there:

1. The `cmd_mox` fixture put a `git` shim at the front of `PATH` before the
   test body started.
2. `mock("git")` declared exactly which call to expect and what to answer.
3. When the test finished, CmdMox checked that `git` was called exactly as
   declared. A missing call, an extra call, or different arguments would fail
   the test with a diff.

______________________________________________________________________

## Features

- **Three kinds of double** – strict mocks, relaxed stubs, and inspectable
  spies ([choose a double][guide-doubles]).
- **Flexible matching** – exact arguments, comparators such as `Regex` and
  `StartsWith`, standard input, and environment variables
  ([describe expected calls][guide-expect]).
- **Dynamic responses** – canned output, or a Python handler that answers
  several different calls to one command ([respond to calls][guide-respond]).
- **Passthrough spies** – run the real command while recording every call
  ([passthrough][guide-passthrough]).
- **Record and replay** – save real interactions to versioned JSON fixtures,
  then replay them deterministically ([record and replay][guide-record]).
- **Automatic lifecycle** – the pytest fixture starts and verifies each test
  for you, and isolates parallel `pytest-xdist` workers
  ([lifecycle][guide-lifecycle]).
- **Careful diagnostics** – failure messages mask secrets such as `*_TOKEN`
  values while keeping the key visible ([troubleshooting][guide-troubleshoot]).

______________________________________________________________________

## Scope (and what's gloriously *out* of it)

CmdMox mocks *commands*—it does not re-enact `bash(1)` interpretive dance
theatre. Pipelines and redirection still work, because the shell wires them up;
CmdMox just intercepts each program. Out of scope, for now or forever:

- **Shell functions and builtins** – `cd`, `exec`, `trap`, and anything you
  would need `eval` for.
- **Running your code** – use `subprocess`, `plumbum`, or whatever black magic
  suits your taste. CmdMox doesn't care how you run commands, as long as you
  run them *like you mean it*.
- **Legacy UNIX** – AIX, Solaris, IRIX? The boat sailed, caught fire, and sank
  in 2003.

______________________________________________________________________

## Learn more

- [User's guide][guide] — from a first test to record and replay,
  troubleshooting, and the full API reference
- [0.3.0 migration guide][migration] — upgrading from CmdMox 0.2
- [Changelog][changelog] — what changed in each release
- [Shellmock migration guide][shellmock] — moving a `shellmock` suite to
  CmdMox
- [Roadmap][roadmap] — planned features and progress
- [Developers' guide][developers] — building, testing, and maintaining CmdMox

______________________________________________________________________

## Licence

ISC — see [LICENSE][licence] for details.

______________________________________________________________________

## Contributing

Contributions are welcome! Start with the [developers' guide][developers] for
how to build and test the project; automated contributors should also follow
[AGENTS.md][agents]. Run `make spelling` to check Markdown against the shared
en-GB-oxendict policy before you open a pull request.

<!-- This README is also the PyPI project description, where relative links do
not resolve, so every link to a repository file is absolute. -->

[guide]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md
[guide-doubles]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md#choose-a-double
[guide-expect]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md#describe-the-calls-you-expect
[guide-respond]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md#respond-to-calls
[guide-passthrough]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md#run-the-real-command-with-passthrough
[guide-record]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md#record-and-replay-fixtures
[guide-lifecycle]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md#control-the-lifecycle
[guide-troubleshoot]: https://github.com/leynos/cmd-mox/blob/main/docs/users-guide.md#troubleshooting
[migration]: https://github.com/leynos/cmd-mox/blob/main/docs/v0-3-0-migration-guide.md
[changelog]: https://github.com/leynos/cmd-mox/blob/main/docs/CHANGELOG.md
[shellmock]: https://github.com/leynos/cmd-mox/blob/main/docs/shellmock-migration-guide.md
[roadmap]: https://github.com/leynos/cmd-mox/blob/main/docs/roadmap.md
[developers]: https://github.com/leynos/cmd-mox/blob/main/docs/developers-guide.md
[licence]: https://github.com/leynos/cmd-mox/blob/main/LICENSE
[agents]: https://github.com/leynos/cmd-mox/blob/main/AGENTS.md
