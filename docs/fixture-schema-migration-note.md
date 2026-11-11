# Fixture schema migration note

This note describes the change to recording fixture schema versions. For the
complete versioning and migration rules, see the
[schema versioning section of the Usage Guide](usage-guide.md#schema-versioning-and-migration).

## What changes

Newly written fixtures use the numeric `MAJOR.MINOR.PATCH` form. The current
schema version is `1.0.0`. Existing two-component versions such as `1.0` remain
loadable and normalize to `1.0.0` when read by the updated CmdMox release.

Compatibility is one-way across this format change: releases that require a
two-component version cannot read fixtures written with three components.
Readers of the updated release tolerate minor and patch differences within the
same major version and raise `ValueError` for a higher major version with no
migration path.

## Stricter fixture field validation

Fixture loading now validates every field the 1.0.0 writer emits instead of
defaulting or coercing malformed values. A fixture that omits a required key,
or supplies a value of the wrong JSON type, fails to load with a `ValueError`
naming the offending path, for example
`Invalid fixture schema: recordings[0].stdout: expected str, got NoneType`.

Three fields remain optional and may be absent or `null`:
`metadata.test_module`, `metadata.test_function`, and
`scrubbing_rules[].description`. A missing `version` is the separate legacy
exception: it is treated as `0.0.0` and migrated forward. An explicit non-string
`version` is rejected.

Update a fixture that fails validation by adding the missing key or replacing
the malformed value with the type the schema requires. In particular,
`recordings`, `scrubbing_rules`, `scrubbing_rules[].applied_to`, and the
invocation fields `stdin`, `env_subset`, `stdout`, `stderr`, `exit_code`, and
`duration_ms` are no longer defaulted.

## Upgrade shared fixture workflows

Upgrade every CmdMox installation that reads or writes a shared fixture before
sharing fixtures written by the updated release. If some consumers cannot be
upgraded together, keep using fixtures in the format supported by the oldest
consumer; do not rewrite shared fixtures to three components until all
consumers support them. Preserve a copy of existing fixtures and use the
updated release's loader and migration path when preparing fixtures for the new
format. The new format does not provide a guaranteed lossless downgrade to
older releases.
