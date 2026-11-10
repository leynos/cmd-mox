# Changelog

## [0.3.0] - 2026-11-18

_If you are upgrading: please see the
[0.3.0 migration guide](v0-3-0-migration-guide.md)._

### Changed

- **Breaking:** require Python 3.12 or newer ([#149])
- **Breaking:** raise `ExpectationConfigurationError` when a double's argument
  expectation is set twice, or `with_args()` and `with_matching_args()` are
  mixed, instead of silently replacing or combining them ([#260])
- **Breaking:** bound shim stdin capture with `CMOX_IPC_TIMEOUT` on every
  platform, so an unclosed input pipe fails with a diagnostic instead of
  hanging ([#344])
- **Breaking:** mask segment-style secrets such as `DB_PWD`, `GITHUB_KEY`, and
  `DOCKER_AUTH_CONFIG` in conflict errors, verifier failures, and spy assertion
  messages ([#361])
- **Breaking:** bound the Windows named-pipe server's concurrent clients,
  message size, and per-client read time ([#229])
- Make `replay()` a no-op when the controller is already replaying ([#150])
- Anchor the POSIX shim's IPC deadline at shim start, covering connection,
  request, response, and passthrough reporting ([#258])
- Explain the plugin's lifecycle switches and their precedence in
  `LifecycleError` messages ([#260])
- Publish the source distribution alongside the wheel, and attach both with
  their provenance bundle to GitHub Releases ([#340])
- Rename the usage guide to the user's guide, rework it from first test to
  expert use, and execute every published example in the test suite ([#392])

### Added

- Add record mode: `CommandDouble.record()` and `RecordingSession` save
  passthrough calls to JSON fixtures, with environment filtering and scrubbers
  ([#156], [#159])
- Add replay: `CommandDouble.replay()` and `ReplaySession` answer calls from
  fixtures with strict or fuzzy best-fit matching ([#161], [#162], [#163],
  [#164])
- Fail verification when a replayed fixture has unused recordings ([#165])
- Version fixture schemas as `MAJOR.MINOR.PATCH`, migrating older fixtures on
  load ([#157], [#341])
- Export `ExpectationConfigurationError` and add the
  `CmdMox(lifecycle_hint=...)` option ([#260])
- Emit bounded, structured IPC observability events with correlation
  identifiers ([#229])
- Add a 0.3.0 migration guide and this changelog ([#392])

### Removed

- **Breaking:** remove the `to_dict()` and `from_payload()` methods from
  `Invocation`, `Response`, `PassthroughRequest`, and `PassthroughResult` in
  favour of functions in `cmd_mox.ipc.wire` ([#349])
- **Breaking:** move `RobustRmtreeError` from `cmd_mox.environment` to
  `cmd_mox.fs_retry` ([#138])
- **Breaking:** move `SENSITIVE_ENV_KEY_TOKENS` from `cmd_mox.expectations` to
  `cmd_mox.domain` ([#260])

### Fixed

- Preserve `KeyboardInterrupt` and `SystemExit` raised during a test while the
  fixture tears down ([#324])
- Keep unresolvable `sys.path` entries during shim bootstrap instead of
  aborting start-up ([#348])
- Stop the Unix IPC server raising when a client disconnects mid-reply, and
  stop answering zero-byte readiness probes ([#259])
- Bound the shim's wait when the server rejects a payload ([#258])

## [0.2.0] - 2025-11-27

<!-- markdownlint-disable-next-line MD024 -->
### Added

- Support Windows: generate `.cmd` shim launchers and talk to the test process
  over named pipes ([#116], [#127], [#129])

<!-- markdownlint-disable-next-line MD024 -->
### Fixed

- Report a descriptive error when replay starts without its shim directory or
  socket path ([#119])

## [0.1.0] - 2025-10-19

_Initial release._

[0.3.0]: https://github.com/leynos/cmd-mox/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/leynos/cmd-mox/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/leynos/cmd-mox/releases/tag/v0.1.0

[#116]: https://github.com/leynos/cmd-mox/pull/116
[#119]: https://github.com/leynos/cmd-mox/pull/119
[#127]: https://github.com/leynos/cmd-mox/pull/127
[#129]: https://github.com/leynos/cmd-mox/pull/129
[#138]: https://github.com/leynos/cmd-mox/pull/138
[#149]: https://github.com/leynos/cmd-mox/pull/149
[#150]: https://github.com/leynos/cmd-mox/pull/150
[#156]: https://github.com/leynos/cmd-mox/pull/156
[#157]: https://github.com/leynos/cmd-mox/pull/157
[#159]: https://github.com/leynos/cmd-mox/pull/159
[#161]: https://github.com/leynos/cmd-mox/pull/161
[#162]: https://github.com/leynos/cmd-mox/pull/162
[#163]: https://github.com/leynos/cmd-mox/pull/163
[#164]: https://github.com/leynos/cmd-mox/pull/164
[#165]: https://github.com/leynos/cmd-mox/pull/165
[#229]: https://github.com/leynos/cmd-mox/pull/229
[#258]: https://github.com/leynos/cmd-mox/pull/258
[#259]: https://github.com/leynos/cmd-mox/pull/259
[#260]: https://github.com/leynos/cmd-mox/pull/260
[#324]: https://github.com/leynos/cmd-mox/pull/324
[#340]: https://github.com/leynos/cmd-mox/pull/340
[#341]: https://github.com/leynos/cmd-mox/pull/341
[#344]: https://github.com/leynos/cmd-mox/pull/344
[#348]: https://github.com/leynos/cmd-mox/pull/348
[#349]: https://github.com/leynos/cmd-mox/pull/349
[#361]: https://github.com/leynos/cmd-mox/pull/361
[#392]: https://github.com/leynos/cmd-mox/pull/392
