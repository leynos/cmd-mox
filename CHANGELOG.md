# Changelog

## [Unreleased]

_If you are upgrading: see the
[handler environment migration note](docs/handler-environment-migration-note.md)._

### Changed

- **Breaking:** handlers no longer observe `.with_env()` overrides in
  `os.environ`; read them from `Invocation.env`. CmdMox stops mutating the test
  process's environment while a handler runs, removing a race between
  concurrent IPC worker threads
  ([#394](https://github.com/leynos/cmd-mox/issues/394); leynos)
- `temporary_env()` restores only the keys in its mapping, including after a
  partial update fails. Unrelated environment changes made inside the context
  now survive ([#394](https://github.com/leynos/cmd-mox/issues/394); leynos)

[Unreleased]: https://github.com/leynos/cmd-mox/compare/v0.2.0...HEAD
