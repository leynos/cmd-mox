# User Guide

For CmdMox workflows, fixtures, and API details, see the
[detailed usage guide](usage-guide.md).

For installation from PyPI, its source distribution and wheel, and the
provenance bundle included with GitHub Releases, see
[Getting started](usage-guide.md#getting-started).

## Recording fixture schema versions

The fixture `version` uses `MAJOR.MINOR.PATCH`; legacy `1.0` values load as
`1.0.0`. CmdMox tolerates minor and patch differences within the same major
version, while a higher major version without a migration path raises
`ValueError`. This compatibility is one-way: earlier releases that require
two-component versions cannot read fixtures written with three components. See
the
[schema versioning and migration guide](usage-guide.md#schema-versioning-and-migration)
for details.

When upgrading to a release that writes three-component fixture versions,
upgrade every CmdMox consumer before sharing newly written fixtures. Earlier
releases that require two-component versions cannot read them. Existing `1.0`
fixtures continue to load in the new release and normalize to `1.0.0`; see the
[fixture schema migration note](fixture-schema-migration-note.md) before
upgrading shared fixture workflows.

## Shim stdin and IPC timeouts

`CMOX_IPC_TIMEOUT` defaults to five seconds when the IPC server starts inside
an active `EnvironmentManager`. Capture of non-terminal stdin is bounded on
every platform and for every kind of stream: once the deadline passes, the shim
discards partial input, does not contact the IPC server, prints
`IPC error: stdin read timed out after <timeout> seconds`, and exits with status
`1`. On POSIX, the Unix IPC exchange through passthrough reporting then runs
against a separate deadline anchored where the shim starts, so a slow capture
leaves it correspondingly less time. On Windows, stdin has a separate capture
deadline while named-pipe communication uses its existing cooperative timeout,
so the POSIX IPC deadline does not apply there.

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
