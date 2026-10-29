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

## Upgrade shared fixture workflows

Upgrade every CmdMox installation that reads or writes a shared fixture before
sharing fixtures written by the updated release. If some consumers cannot be
upgraded together, keep using fixtures in the format supported by the oldest
consumer; do not rewrite shared fixtures to three components until all
consumers support them. Preserve a copy of existing fixtures and use the
updated release's loader and migration path when preparing fixtures for the new
format. The new format does not provide a guaranteed lossless downgrade to
older releases.
