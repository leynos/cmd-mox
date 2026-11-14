# Changelog

## [Unreleased]

_If you are upgrading: see the
[handler environment migration note](docs/handler-environment-migration-note.md)
and the [v0.4.0 migration guide](docs/v-0-4-0-migration-guide.md)._

### Changed

- **Breaking:** handlers no longer observe `.with_env()` overrides in
  `os.environ`; read them from `Invocation.env`. CmdMox stops mutating the test
  process's environment while a handler runs, removing a race between
  concurrent IPC worker threads
  ([#394](https://github.com/leynos/cmd-mox/issues/394); leynos)
- `temporary_env()` restores only the keys in its mapping, including after a
  partial update fails. Unrelated environment changes made inside the context
  now survive ([#394](https://github.com/leynos/cmd-mox/issues/394); leynos)
- **Breaking:** record only allowlisted and `.with_env()` keys for commands
  without a known prefix, instead of the whole environment
  ([#368](https://github.com/leynos/cmd-mox/issues/368); leynos)
- Record `.with_env()` keys for known-prefix commands, so passthrough
  recordings keep requested values that the earlier sensitive-name filter
  dropped ([#368](https://github.com/leynos/cmd-mox/issues/368); leynos)

### Added

- Add `explicit_keys` to `RecordingSession.record()` for request-scoped
  environment keys ([#368](https://github.com/leynos/cmd-mox/issues/368);
  leynos)

[Unreleased]: https://github.com/leynos/cmd-mox/compare/v0.2.0...HEAD
