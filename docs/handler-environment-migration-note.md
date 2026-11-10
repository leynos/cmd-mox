# Handler environment migration note

This note describes two related changes to how CmdMox exposes `with_env()`
overrides. For the complete behaviour, see the
[`with_env()` entry in the Usage Guide](usage-guide.md#fluent-api-reference).

## What changes

### Handlers read overrides from `Invocation.env`

CmdMox no longer writes `with_env()` overrides into the test process's
`os.environ` while a handler runs. The overrides are merged into
`Invocation.env` before the handler is called, and handlers must read them from
there:

```python
def handler(invocation):
    return Response(stdout=invocation.env.get("HELLO", ""))
```

Canned responses are unaffected: CmdMox still copies the overrides onto the
response environment so the shim observes them.

The previous implementation wrapped each handler call in `temporary_env()`,
which snapshotted the whole of `os.environ` and restored that snapshot on exit.
Handlers run on the threaded IPC server's request threads, so two concurrent
invocations could interleave their snapshots. One handler could then restore a
copy that predated another handler's changes, leaking expectation variables
into the test process or discarding unrelated environment changes made while a
handler was running. Removing the process-environment overlay eliminates the
race at its source.

### `temporary_env()` restores only the keys it touched

`temporary_env(mapping)` now records the entry state of each key in `mapping`
and restores only those keys on exit. Unrelated additions and deletions made
inside the context survive, and an empty mapping changes nothing. Previously it
restored a full snapshot of `os.environ`, which discarded unrelated changes and
made disjoint contexts unsafe when they exited out of order.

`temporary_env()` still provides no thread isolation. Same-key contexts that
overlap and exit out of order restore an undefined value for that key.

## Upgrade handlers

Replace any handler that reads an injected variable from `os.environ` with a
read from `invocation.env`. The host `os.environ` no longer carries the keys
named by `with_env()`, so a handler that reads them there observes no injected
value.

If a test needs the whole process environment rolled back after replay, do that
explicitly in the test thread rather than relying on CmdMox. Capture
`os.environ.copy()` before `replay()` and restore it after `verify()`. Do not
restore a whole-environment snapshot inside a handler, because handlers run
concurrently. Alternatively, use `EnvironmentManager` when the change is part
of CmdMox's own shim setup. A targeted `temporary_env()` context around the
specific keys is usually enough and avoids discarding unrelated changes.

The internal `execute_handler()` helper no longer accepts an `overrides`
argument. It is not part of the public API; callers that imported it directly
should pass overrides through `Invocation.env` instead.
