<!-- markdownlint-disable-next-line MD013 -->
# 🕵️‍♀️ CmdMox – Python-native command mocking so you never have to write another shell test again

Replace your flaky bats tests, your brittle log-parsing hacks, and that one Bash
script that only works on Tuesdays. CmdMox intercepts external commands with
Python shims, speaks fluent IPC over Unix domain sockets, and enforces your
expectations like a disappointed parent.

- Mocks? Verified.
- Stubs? Quietly compliant.
- Spies? Judging everything you do.

Designed for pytest, built for people who’ve seen things—like `ksh93` unit test
harnesses and AIX cronjobs running `sccs`.

If you've ever mocked `curl` with `cat`, this library is your penance.

## 🧪 Example: Testing a command-line script with CmdMox

Let’s say your script under test calls `git clone` and `curl`. You want to test
it *without actually cloning anything*, because you value your bandwidth and
your sanity.

```python
# test_my_script.py
def test_clone_and_fetch(mox):
    # Record phase
    mox.mock("git") \
        .with_args("clone", "https://a.b/c.git") \
        .returns(exit_code=0)

    mox.mock("curl") \
        .with_args("-s", "https://a.b/c/info.json") \
        .returns(stdout='{"status":"ok"}')

    mox.replay()

    # Code under test runs with mocked git and curl
    result = my_tool.clone_and_fetch("https://a.b/c.git")

    # Assert your code didn’t mess it up
    assert result.status == "ok"

    # Verify phase: did the commands get called *correctly*?
    mox.verify()
```

When it passes: your mocks were used exactly as expected.

When it fails: you'll get a surgically precise diff of what was expected vs what
your misbehaving code actually did.

No subshells. No flaky greps. Just clean, high-fidelity, Pythonic command
mocking.

## 🧯 Scope (and what’s gloriously *out* of it)

**CmdMox** is for mocking *commands*—not re-enacting `bash(1)` interpretive
dance theatre.

Out of scope (for now, or forever):

- 🧞 **Shell function mocking** – you want `eval`, you wait a year. Or just
  don’t.

- 🪟 **Windows support** – maybe one day. Until then: enjoy your `.bat` files and
  pray to `CreateProcess()`.

- 🦕 **Legacy UNIX support** – AIX, Solaris, IRIX? Sorry boys, the boat sailed,
  caught fire, and sank in 2003.

- 🧩 **Builtin mocking** – `cd`, `exec`, `trap`? No. Just no.

- 🧪 **Calling commands under test** – use `subprocess`, `plumbum`, or whatever
  black magic suits your taste. CmdMox doesn't care how you run them—as long as
  you run them *like you mean it*.
