# IPC timeout migration note

This note helps projects prepare for the next minor release that bounds shim
stdin and IPC waits. It does not assume a release number. For the complete
behaviour and configuration details, see the
[`CMOX_IPC_TIMEOUT` section of the Usage Guide](usage-guide.md#environment-variables).

## Check how commands receive stdin

`CMOX_IPC_TIMEOUT` bounds the capture of non-terminal stdin on every platform
and for every kind of stream. A shim anchors one absolute budget from it at
start-up, before the capture begins, and every later step spends that budget
instead of starting a fresh one. Regular-file reads run in a worker, so the
shim can stop waiting at the deadline. What the capture leaves covers the IPC
exchange through passthrough reporting; running the real passthrough command is
not interrupted, but its duration is charged to the same budget, so the report
only starts if time remains. The named-pipe transport now follows the same
rule: a client has one deadline for the request and the reply, and the
30-second client read limit that previously applied on Windows is gone.

On the server side, a connection's request read and its reply delivery share
one per-connection `timeout` budget, so a client that reads slowly cannot renew
it for the reply. Named-pipe reply delivery writes the reply and then waits,
within the same budget, for the client to close the pipe, because
`FlushFileBuffers` only promises the bytes left the server's buffer.

Reaching the deadline ends the capture: the shim discards partial input, does
not contact the IPC server, and exits with status `1`. On Windows the cancelled
handle read is given a short bounded grace (about one second) to unwind, so the
diagnostic and exit follow that cleanup rather than occurring exactly at the
deadline; a request cancelled mid-exchange allows the same grace. A caller that
never finishes sending input therefore loses what it had sent, so tests and
callers should still send EOF or close the pipe before waiting for an
invocation to complete. If stdin is not needed, redirect it from a source that
is already complete, such as `subprocess.DEVNULL`.

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
