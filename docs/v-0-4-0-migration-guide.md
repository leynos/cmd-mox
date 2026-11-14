# v0.4.0 migration guide

This guide helps projects prepare for v0.4.0, which changes which environment
variables a recording keeps. For the complete rule order, see
[decide what a recording keeps](usage-guide.md#decide-what-a-recording-keeps).

## What changes

### Commands without a known prefix

Before v0.4.0, `filter_env_subset()` recorded every variable that was not a
system key, a CmdMox internal key or a sensitive key when the command had no
known prefix. Fixtures therefore captured host names, user paths, tool-cache
locations and agent session identifiers, and strict replay required all of them
to be present on the replay machine.

From v0.4.0, a command without a known prefix records only:

- keys named in the session's `env_allowlist`
- keys requested through `.with_env()` for that passthrough request

The eight known prefixes in `COMMAND_ENV_PREFIXES` (`git`, `aws`, `docker`,
`npm`, `pip`, `cargo`, `go`, `rustc`) keep their prefix behaviour. Command
lookup stays exact and case-insensitive, so `/usr/bin/git` and `git.exe` count
as unknown commands. CmdMox internal keys (`CMOX_*`, `CMD_MOX_*`) remain
excluded even when requested.

### `.with_env()` keys are recorded for every command

`.with_env()` keys are now captured automatically for the passthrough request
that declared them, for known-prefix commands as well as unknown ones. The keys
are snapshotted when the request is prepared, so mutating the expectation
before the request completes does not change what the recording keeps.

This is a privacy change for known-prefix commands. A key requested through
`.with_env()` is recorded even when its name matches the sensitive-name
heuristic, so a `git` recording can now persist a requested `SECRET_TOKEN`
value that the old filter excluded. Review or scrub fixtures before committing
them, and avoid passing secrets through `.with_env()` when recording.

## Who is affected

- Projects that recorded commands outside the eight known-prefix tools and
  relied on unrelated variables being present in `env_subset`.
- Projects whose fixtures currently contain host-specific values. Those
  fixtures continue to replay unchanged until they are re-recorded.
- Projects that pinned a fixture's exact `env_subset` in assertions or review
  tooling.
- Projects that pass sensitive values through `.with_env()` while recording a
  known-prefix command. Those values now reach the fixture.

Fixtures that already request their variables through `env_allowlist` or
`.with_env()` keep those keys, but review them for sensitive values.

## Re-record fixtures that carry host data

Existing fixtures keep working. Re-record them to drop the host data that the
old default captured:

```python
cmd_mox.spy("greet").passthrough().record("fixtures/greet.json")
```

Review the resulting `env_subset` before committing. If a portable variable is
missing, add it to `env_allowlist` or declare it with `.with_env()` rather than
recording the whole environment.

## Restore individual keys

Use `env_allowlist` for a key that every recording in the session should keep:

```python
cmd_mox.spy("greet").passthrough().record(
    "fixtures/greet.json",
    env_allowlist=["GREET_SETTING"],
)
```

Use `.with_env()` when the key belongs to one expectation:

```python
cmd_mox.spy("greet").with_env({"GREET_SETTING": "portable"}).passthrough().record(
    "fixtures/greet.json"
)
```

Both mechanisms record a key only when it is present in the environment, and
neither can restore a CmdMox internal key.

## Strict replay

Strict matching, the default, requires every recorded `env_subset` pair to be
present in the live invocation. Fixtures recorded under the old default fail
strict replay whenever a captured variable is absent, which is the usual
outcome on another machine or in CI. Re-recording fixes the fixture. As a
temporary workaround, replay with `strict=False`, but a narrower recording is
the durable fix. `strict=False` also stops requiring stdin to match, so replay
can return a response recorded for different input; treat it as a short-lived
local unblock rather than a way to keep a stale fixture.

## See also

- [Decide what a recording keeps](usage-guide.md#decide-what-a-recording-keeps)
- [IPC timeout migration note](ipc-timeout-migration-note.md)
- [Fixture schema migration note](fixture-schema-migration-note.md)
