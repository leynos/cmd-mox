# User Guide

For CmdMox workflows, fixtures, and API details, see the
[detailed usage guide](usage-guide.md).

## Shim stdin and IPC timeouts

`CMOX_IPC_TIMEOUT` defaults to five seconds when the IPC server starts inside
an active `EnvironmentManager`. Capture of non-terminal stdin is bounded on
every platform and for every kind of stream: once the deadline passes, the shim
discards partial input, does not contact the IPC server, prints
`IPC error: stdin read timed out after <timeout> seconds`, and exits with status
`1`. On POSIX the same deadline then covers the Unix IPC exchange through
passthrough reporting. On Windows, stdin keeps its own capture deadline while
named-pipe communication uses its existing cooperative timeout, so the POSIX
shared-deadline guarantee does not apply there.

If a shim may inherit stdin that stays open, send EOF, close the pipe, or
redirect stdin from a source that is already complete. The shim now stops
waiting at the deadline, but a caller that never finishes an interactive
exchange still loses its input. For the precise timeout scope and error
behaviour, see the
[canonical `CMOX_IPC_TIMEOUT` guidance](usage-guide.md#environment-variables).

Invalid non-empty requests receive a bounded error on stderr and exit with
status 1. An empty read, including a readiness probe that closes without a
request, is treated as a closed connection and gets no response frame. Other
connection failures and timeout expiry produce a controlled `IPC error: ...`
diagnostic and a non-zero exit. If passthrough reporting fails, the shim keeps
the real command's non-zero exit status.
