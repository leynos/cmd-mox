# User Guide

For CmdMox workflows, fixtures, and API details, see the
[detailed usage guide](usage-guide.md).

For installation from PyPI, its source distribution and wheel, and the
provenance bundle included with GitHub Releases, see
[Getting started](usage-guide.md#getting-started).

## Spy call counts

A spy stays observational unless you set a count with `times(count)` or its
`times_called(count)` alias; either call makes `verify()` enforce that the spy
ran exactly that many times. An uncounted spy's total is not checked, except
that `in_order()` expands the expectation to its count (one by default), so an
ordered spy must still be invoked exactly that many times in sequence. The
[usage guide](usage-guide.md#spies-and-passthrough-mode) covers the full
behaviour.

Earlier releases accepted these count setters on spies but did not enforce the
total. A test that set a spy count and relied on it being ignored now fails
verification: remove the count to keep the spy observational, or make the
expected number of calls.

## Sensitive environment values in diagnostics

Conflict errors from `UnexpectedCommandError`, verifier failures, and spy
assertion messages mask sensitive environment values with `***` while leaving
the key name visible. The classification is the same one recording redaction
uses, so segment-style names such as `DB_PWD`, `GITHUB_KEY`, and
`DOCKER_AUTH_CONFIG` are masked along with the `*_SECRET`, `*_TOKEN`,
`*_PASSWORD`, and `*_API_KEY` families. Matching and recorded data stay raw,
and an ordinary key's value is unchanged. The [usage guide](usage-guide.md)
covers the exact vocabulary, and the
[design document](python-native-command-mocking-design.md#994-environment-values-in-error-diagnostics)
records which diagnostic paths the guarantee covers.

## Environment variables in recordings

Recordings keep only a safe subset of the process environment. Commands that
use a name prefix, such as `git` and `GIT_*`, still record every prefixed key
that is not sensitive or on the system exclusion list. `go` is different: it
records only the documented Go toolchain and runtime variables (`GOPATH`,
`GOFLAGS`, `GOOS`, `GORACE`, and so on). Other names that merely start with
`GO`, such as `GOOGLE_CLOUD_PROJECT`, and `CGO_*` names, are no longer recorded
by default.

This change affects new recordings only. Fixtures already on disk keep the keys
they stored and still replay against those keys, so an old fixture can require
a variable the current filter would drop. To keep a key that the Go rule now
rejects, pass it in `env_allowlist` when starting the recording session:

```python
mox.spy("go").passthrough().record(
    "go-fixture.json", env_allowlist=["GOOGLE_CLOUD_PROJECT"]
)
```

The [usage guide](usage-guide.md#environment-variable-filtering) lists the full
rule set, including how requested keys interact with the sensitive-key and
system-key exclusions. The
[Go environment migration note](go-env-migration-note.md) summarizes the change
for teams upgrading shared fixtures.

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
