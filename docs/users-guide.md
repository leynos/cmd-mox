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

## IPC payload migration

The domain classes no longer provide the previous IPC conversion methods.
Callers that used them must migrate to the wire functions listed in the
[IPC payload migration guide](usage-guide.md#ipc-payload-migration).

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
