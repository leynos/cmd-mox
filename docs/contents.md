# Documentation Contents

- [User Guide](./users-guide.md): User-facing workflows and shim timeout
  behaviour.
- [Usage Guide](./usage-guide.md): Mocking, stubbing, and spying on commands.
- [Developer Guide](./developers-guide.md): Local checks and linting policy.
- [IPC Timeout Migration Note](./ipc-timeout-migration-note.md): Prepare for
  bounded shim stdin and IPC waits in the next minor release.
- [IPC Payload Migration Note](./ipc-payload-migration-note.md): Replace
  removed payload conversion methods before upgrading.
- [Expectation Count Migration Note](./expectation-count-migration-note.md):
  Replace invalid `times()` counts and update explicitly counted spies.
- [Fixture Schema Migration Note](./fixture-schema-migration-note.md): Prepare
  for three-component recording fixture versions.
- [Go Environment Migration Note](./go-env-migration-note.md): Prepare for
  exact-name filtering of Go variables in new recordings.
- [Handler Environment Migration Note](./handler-environment-migration-note.md):
  Read `with_env()` overrides from `Invocation.env` and expect touched-key
  restoration from `temporary_env()`.
- [Shellmock Migration Guide](./shellmock-migration-guide.md): Migrate from
  `shellmock` to CmdMox.
- [Design Doc](./python-native-command-mocking-design.md): Design rationale.
- [Fake Capabilities Design](./cmd-mox-fake-capabilities-design.md):
  Durable fixture writes and reusable helpers for stateful command fakes.
- [RFC 001](./rfc-001-act-execution-environment.md): Proposed container
  execution environment and Act integration for workflow command doubles.
- [Roadmap](./roadmap.md): Planned features and progression.
- [ADR 001](./adr-001-linting-architecture.md): Python linting architecture,
  including the dated addendum for the fourth Skylos tier.
- [ADR 002](./adr-002-code-duplication-gate.md): The nose code-duplication
  gate, its scan scope, its exception contract and its provisioning.
- [ADR 003](./adr-003-release-pipeline.md): The sdist-first release pipeline,
  Trusted Publishing and reconciliation between PyPI and GitHub Releases.
