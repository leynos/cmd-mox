<!-- markdownlint-disable MD013 -->

# 🕵️‍♀️ CmdMox – Python-native command mocking so you never have to write another shell test again

<!-- markdownlint-enable MD013 -->

Replace your flaky bats tests, your brittle log-parsing hacks, and that one
Bash script that only works on Tuesdays. CmdMox intercepts external commands
with Python shims, speaks fluent IPC over Unix domain sockets *and* Windows
named pipes, and enforces your expectations like a disappointed parent.

- Mocks? Verified.
- Stubs? Quietly compliant.
- Spies? Judging everything you do.

Designed for pytest, built for people who’ve seen things—like `ksh93` unit test
harnesses and AIX cronjobs running `sccs`.

If you've ever mocked `curl` with `cat`, this library is your penance.

For detailed instructions, see [docs/usage-guide.md](docs/usage-guide.md).

Contributors can run `make spelling` to check maintained Markdown against the
shared en-GB-oxendict policy. The same pinned gate runs in pull-request linting.

**Platform support:** Linux, macOS, and Windows. CmdMox emits POSIX symlink
shims on Unix-like systems and `.cmd` launchers backed by named pipes on
Windows, so the same tests and fixtures run across all three platforms.

## ✅ Requirements

- Python 3.11 or newer (to leverage modern enum.StrEnum support)

## 🧪 Example: Testing a command-line script with CmdMox

Let’s say your script under test calls `git clone` and `curl`. You want to test
it *without actually cloning anything* because you value your bandwidth and
your sanity.

```python
# test_my_script.py
def test_clone_and_fetch(cmd_mox):
    # Define expectations (fixture auto-enters REPLAY before the test body)
    cmd_mox.mock("git") \
        .with_args("clone", "https://a.b/c.git") \
        .returns(exit_code=0)

    cmd_mox.mock("curl") \
        .with_args("-s", "https://a.b/c/info.json") \
        .returns(stdout='{"status":"ok"}')

    # Code under test runs with mocked git and curl
    result = my_tool.clone_and_fetch("https://a.b/c.git")

    # Assert your code didn’t mess it up
    assert result.status == "ok"
    # Verification happens automatically during pytest teardown.
```

When it passes: your mocks were used exactly as expected.

When it fails: you'll get a surgically precise diff of what was expected vs
what your misbehaving code actually did.

No subshells. No flaky greps. Just clean, high-fidelity, Pythonic command
mocking.

## 🧯 Scope (and what’s gloriously *out* of it)

**CmdMox** is for mocking *commands*—not re-enacting `bash(1)` interpretive
dance theatre.

Out of scope (for now, or forever):

- 🧞 **Shell function mocking** – you want `eval`, you wait a year. Or just
  don’t.

- 🦕 **Legacy UNIX support** – AIX, Solaris, IRIX? Sorry boys, the boat sailed,
  caught fire, and sank in 2003.

- 🧩 **Builtin mocking** – `cd`, `exec`, `trap`? No. Just no.

- 🧪 **Calling commands under test** – use `subprocess`, `plumbum`, or whatever
  black magic suits your taste. CmdMox doesn't care how you run them—as long as
  you run them *like you mean it*.
