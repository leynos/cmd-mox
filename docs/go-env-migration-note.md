# Go environment migration note

This note describes the change to how recordings filter Go environment
variables. For the complete rule set, see the
[environment variable filtering section of the Usage Guide](usage-guide.md#environment-variable-filtering).

## What changes

Recordings of `go` invocations previously kept every variable whose name began
with `GO`. That prefix also matched unrelated names, such as
`GOOGLE_CLOUD_PROJECT`, so fixtures captured values that had no bearing on the
recorded command.

New recordings keep only the Go toolchain and runtime variables documented by
`go help environment` and `go doc runtime`, such as `GOPATH`, `GOFLAGS`, `GOOS`,
`GORACE`, and `GOTOOLCHAIN`. Other `GO*` names, and `CGO_*` names, are no
longer recorded by default. The list changes between Go releases, so a variable
added by a newer toolchain is not recorded until CmdMox lists it.

## Upgrade shared fixture workflows

Existing fixtures are unaffected: a fixture keeps the `env_subset` it stored,
and replay matches against those keys. An old fixture can therefore require a
variable that the current filter would drop.

New recordings may omit a variable that an older fixture required. To keep such
a key, name it in `env_allowlist` when starting the recording:

```python
mox.spy("go").passthrough().record(
    "go-fixture.json", env_allowlist=["GOOGLE_CLOUD_PROJECT"]
)
```

Requested keys take precedence over the sensitive-key and system-key rules, so
an allowlisted key is persisted with its raw value. CmdMox internal keys
(`CMOX_*`, `CMD_MOX_*`) remain excluded even when requested. Review an
allowlist for secrets before sharing the resulting fixture.
