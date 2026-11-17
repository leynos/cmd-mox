# IPC timeout migration note

This note helps projects prepare for the next minor release that bounds shim
stdin and IPC waits. It does not assume a release number. For the complete
behaviour and configuration details, see the
[`CMOX_IPC_TIMEOUT` section of the Usage Guide](usage-guide.md#environment-variables).

## Check how commands receive stdin

`CMOX_IPC_TIMEOUT` bounds the capture of non-terminal stdin on every platform
and for every kind of stream. Capture starts its own deadline, and regular-file
reads run in a worker, so the shim can stop waiting at that deadline. On POSIX
the Unix IPC exchange through passthrough reporting then runs against a
separate deadline anchored where the shim starts, so a slow capture leaves the
exchange correspondingly less time. On Windows, stdin has a separate capture
deadline while named-pipe communication uses its existing cooperative timeout,
so the POSIX IPC deadline does not apply there.

Reaching the deadline ends the capture: the shim discards partial input, does
not contact the IPC server, and exits with status `1`. On Windows the cancelled
handle read is given a short bounded grace (about one second) to unwind, so the
diagnostic and exit follow that cleanup rather than occurring exactly at the
deadline. A caller that never finishes sending input therefore loses what it
had sent, so tests and callers should still send EOF or close the pipe before
waiting for an invocation to complete. If stdin is not needed, redirect it from
a source that is already complete, such as `subprocess.DEVNULL`.

## Understand failure diagnostics

- A stdin capture that reaches the deadline prints
  `IPC error: stdin read timed out after <timeout> seconds` on stderr and exits
  with status `1`.
- A non-empty request that cannot be parsed or fails validation receives a
  bounded error response on stderr and exit status 1.
- An empty read, including a readiness probe that closes without sending a
  request, is treated as a closed connection and receives no response frame.
- Connection failures and timeout expiry produce a controlled `IPC error: ...`
  diagnostic on stderr and a non-zero exit.
- If passthrough reporting fails, the shim preserves the real command's
  non-zero exit status.

## Know the request and client limits

Both transports accept at most 8 MiB per message and serve at most 64 clients
at once. On POSIX, a request that crosses the size limit is abandoned as soon
as the limit is passed, so the server never buffers the whole payload and never
dispatches the request. The connection then closes without a response frame, so
the shim reports a controlled `IPC error: ...` diagnostic and a non-zero exit
rather than a partial reply. A 65th concurrent connection is refused the same
way while 64 are in flight, and is admitted normally once one of them finishes.

The limits do not change `CMOX_IPC_TIMEOUT`. On POSIX the accepted connection
still has one non-renewing read deadline, so a client that sends slowly cannot
extend its budget by trickling bytes. Windows keeps its per-step cooperative
timeouts, so the POSIX IPC deadline does not apply there.
