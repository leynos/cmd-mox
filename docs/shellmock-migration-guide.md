# Shellmock migration guide

This guide helps shellmock users migrate to CmdMox. It focuses on translating
shellmock expectations into CmdMox's Python API while highlighting behavioural
differences that matter during test execution.

## Who this is for

Use this guide if you already have shellmock-based tests or CLI fixtures and
want to move to CmdMox's pytest-first workflow. If you are new to CmdMox, read
`docs/usage-guide.md` first, then return here for the mapping examples.
Shellmock CLI syntax can vary by version, so treat the shellmock snippets as
conceptual and confirm the exact flags in your local shellmock documentation.

## What changes when moving to CmdMox

- CmdMox is configured from Python tests (typically via pytest fixtures), not
  shell scripts.
- CmdMox follows a strict record -> replay -> verify lifecycle. Verification
  happens automatically in the pytest fixture unless you disable auto
  lifecycle.
- CmdMox uses Python shims and IPC to capture invocations instead of shell
  scripts and log files.
- Matchers are expressed as Python objects (for example `Regex`, `Contains`)
  rather than CLI flags.

## Feature mapping

<!-- markdownlint-disable MD013 -->
| shellmock concept or flag | CmdMox equivalent |
| --- | --- |
| Mock an executable command | `cmd_mox.mock("cmd")` |
| Define exact args (`--match`) | `.with_args("arg1", "arg2")` |
| Define stdout (`--output`) | `.returns(stdout="...")` |
| Define exit status (`--status`) | `.returns(exit_code=...)` |
| Partial arg match (`--type partial`) | `.with_matching_args(Contains("arg"))` |
| Regex arg match (`--type regex`) | `.with_matching_args(Regex(r"pattern"))` |
| Match stdin (`--match-stdin`) | `.with_stdin("payload")` |
| Custom behaviour (`--exec`) | `.runs(handler)` |
| Verify expectations (`shellmock_verify`) | `cmd_mox.verify()` |
<!-- markdownlint-enable MD013 -->

## Example 1: simple stub

Shellmock style (conceptual):

```bash
# Stub "tool --version" and return fixed output
shellmock tool --match "--version" --output "1.2.3" --status 0

# Run code under test
my_tool --version

# Verify expectations
shellmock_verify
```

CmdMox style (pytest):

```python
def test_version(cmd_mox):
    cmd_mox.stub("tool").with_args("--version").returns(
        stdout="1.2.3\n",
        exit_code=0,
    )

    assert my_tool.version() == "1.2.3"
```

Notes:

- `cmd_mox.stub(...)` does not require verification (it is non-strict), but
  verification still runs by default in the pytest fixture.
- When using the context manager API instead of pytest, call `mox.replay()`
  before running your code and `mox.verify()` after.

## Example 2: strict mock with args and stdin

Shellmock style (conceptual):

```bash
shellmock curl \
  --match "-X POST https://api.example.test" \
  --match-stdin '{"name":"demo"}' \
  --output "ok" \
  --status 0

run_uploader
shellmock_verify
```

CmdMox style (pytest):

```python
def test_upload(cmd_mox):
    cmd_mox.mock("curl") \
        .with_args("-X", "POST", "https://api.example.test") \
        .with_stdin('{"name":"demo"}') \
        .returns(stdout="ok", exit_code=0)

    run_uploader()
```

If the order or call count matters, add `.times(n)`, `.in_order()`, or
`.any_order()` to the expectation.

## Migration checklist

- Add `pytest_plugins = ("cmd_mox.pytest_plugin",)` to `conftest.py`.
- Replace each shellmock expectation with `cmd_mox.stub`, `cmd_mox.mock`, or
  `cmd_mox.spy` calls in tests.
- Translate `--match` to `with_args`, and `--type partial` or `--type regex` to
  `Contains` or `Regex` matchers.
- Replace `--output` and `--status` with `.returns(stdout=..., exit_code=...)`.
- Replace `--exec` logic with `.runs(handler)`; the handler receives an
  `Invocation` object and returns `(stdout, stderr, exit_code)`.
- Ensure verification happens (pytest fixture auto-verifies unless disabled).

## Common gotchas

- CmdMox only intercepts commands while in replay mode. If you are not using
  the pytest fixture, call `mox.replay()` before running the code under test.
- CmdMox enforces strict ordering for mocks by default. Use `.any_order()` if
  ordering is not important for a particular expectation.
- CmdMox can match stdin and environment variables, but those must be declared
  explicitly with `.with_stdin(...)` and `.with_env(...)`.

## Where to go next

- `docs/usage-guide.md` for the full API reference and matcher descriptions.
- `examples/` for runnable pytest examples covering stubs, mocks, spies, and
  passthrough mode.
