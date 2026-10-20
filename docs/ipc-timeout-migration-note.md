# IPC timeout migration note

This note helps projects prepare for the next minor release that bounds shim
stdin and IPC waits. It does not assume a release number. For the complete
behaviour and configuration details, see the
[`CMOX_IPC_TIMEOUT` section of the Usage Guide](usage-guide.md#environment-variables).

## Check how commands receive stdin

On POSIX, `CMOX_IPC_TIMEOUT` applies one deadline to pollable piped stdin,
regular-file stdin, and the Unix IPC exchange through passthrough reporting.
Regular-file reads run in a worker so the shim can stop waiting at the
deadline. A stream that cannot be polled retains direct-read behaviour and may
wait beyond the timeout. On Windows, stdin remains a direct read, while
named-pipe communication uses its existing cooperative timeout; the POSIX
shared-deadline guarantee does not apply.

Tests or callers that leave stdin open should send EOF or close the pipe before
waiting for the invocation to complete. If stdin is not needed, redirect it
from a source that is already complete. An unpollable stream or a Windows
direct read may still wait for input independently of the IPC timeout.

## Understand failure diagnostics

- A non-empty request that cannot be parsed or fails validation receives a
  bounded error response on stderr and exit status 1.
- An empty read, including a readiness probe that closes without sending a
  request, is treated as a closed connection and receives no response frame.
- Connection failures and timeout expiry produce a controlled `IPC error: ...`
  diagnostic on stderr and a non-zero exit.
- If passthrough reporting fails, the shim preserves the real command's
  non-zero exit status.
