# User Guide

For CmdMox workflows, fixtures, and API details, see the
[detailed usage guide](usage-guide.md).

For installation from PyPI, its source distribution and wheel, and the
provenance bundle included with GitHub Releases, see
[Getting started](usage-guide.md#getting-started).

## Shim stdin and IPC timeouts

`CMOX_IPC_TIMEOUT` defaults to five seconds when the IPC server starts inside
an active `EnvironmentManager`. On POSIX, one deadline covers pollable piped
stdin, regular-file stdin, and the Unix IPC exchange through passthrough
reporting. A stream that cannot be polled keeps direct-read behaviour, so
reading stdin can outlast the timeout. On Windows, stdin also uses direct-read
behaviour; named-pipe communication has its existing cooperative timeout, but
the POSIX shared-deadline guarantee does not apply.

If a shim may inherit stdin that stays open, send EOF, close the pipe, or
redirect stdin from a source that is already complete. This lets the shim
finish reading before it sends the IPC request. For the precise timeout scope
and error behaviour, see the
[canonical `CMOX_IPC_TIMEOUT` guidance](usage-guide.md#environment-variables).

Invalid non-empty requests receive a bounded error on stderr and exit with
status 1. An empty read, including a readiness probe that closes without a
request, is treated as a closed connection and gets no response frame. Other
connection failures and timeout expiry produce a controlled `IPC error: ...`
diagnostic and a non-zero exit. If passthrough reporting fails, the shim keeps
the real command's non-zero exit status.
