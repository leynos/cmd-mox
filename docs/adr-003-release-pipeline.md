# Architectural decision record (ADR) 003: Release pipeline

## Status

Accepted. As of 2026-10-26, CmdMox uses an sdist-first, attested release
workflow that publishes to PyPI and reconciles the GitHub Release with PyPI.

## Date

2026-10-26.

## Context and problem statement

CmdMox 0.2.0 was published to PyPI by hand with a wheel and a source
distribution. The GitHub Release for the same version contains only the wheel.
The PyPI files and GitHub asset came from separate builds. There was no
repeatable release path or check that both destinations carried identical bytes.

The release workflow also needs to tolerate a run that stops after publishing
only some files. Repeating a tag must not try to overwrite immutable PyPI or
GitHub assets, and the GitHub Release must not be published until its assets
match PyPI. Cuprum solved the same class of problem in
[cuprum#488](https://github.com/leynos/cuprum/pull/488); its
[ADR-017](https://github.com/leynos/cuprum/blob/main/docs/adr-017-release-pipeline.md)
records the corresponding release-pipeline decision.

## Decision drivers

- Build and check the source distribution on every pull request before a tag is
  created.
- Attest the files that will be published and ship those same bytes to both
  destinations.
- Make retries safe when PyPI publication or GitHub asset upload is partial.
- Keep distribution bytes immutable after either destination has them.
- Keep the current package pure Python and avoid unused native build jobs.

## Options considered

| Option                              | Source build                | Retry behaviour                           | Destination consistency            |
| ----------------------------------- | --------------------------- | ----------------------------------------- | ---------------------------------- |
| Manual PyPI and GitHub uploads      | Separate local builds       | Operator-managed                          | Not verified                       |
| Tag-time wheel-only GitHub workflow | Wheel built at tag time     | PyPI remains manual                       | Sdist can be absent from GitHub    |
| Sdist-first attested workflow       | One sdist and wheel from it | Idempotent PyPI and GitHub reconciliation | SHA-256 checked before publication |

_Table 1: Release-pipeline options and their operational consequences._

## Decision outcome

CmdMox adopts the sdist-first release process used by Cuprum, scoped to the
current pure-Python package.

Plain `uv build` creates the source distribution first and builds the wheel
from that archive. The shared action requires exactly one sdist and at least
one pure-Python wheel. Pull-request CI runs the action, so a missing or
unbuildable sdist fails before tag time. `make build-release` uses the same
`uv build` path.

The `check-version` job compares the tag and `project.version` as exact strings
after removing a leading `v`. It also determines whether the public version is
a pre-release. Attestation waits for both this check and the build. It attests
all wheels and the single sdist, then hands the files and Sigstore bundle off
together as `release-dist` so later jobs use the attested bytes.

PyPI publication runs with Trusted Publishing in the `pypi` environment. The
workflow reads the PyPI Simple index and drops every filename already present
before using `pypa/gh-action-pypi-publish` with `skip-existing: true`. A
missing project index is treated as empty. A repeated tag therefore succeeds
without uploading an existing filename.

The workflow creates or reuses a draft GitHub Release before publishing to
PyPI. Before taking the asset snapshot, it attaches the current run's
provenance bundle to a draft when that filename is absent. Later runs carry
that bundle forward, preserving the attestation for PyPI's canonical bytes if
the earlier run published to PyPI but failed before GitHub publication. An
already-published release is not changed by this early attachment step. After
PyPI contains every distribution, the final job reconciles the GitHub assets
with PyPI's SHA-256 digests and then publishes the release. For each filename,
source priority is PyPI, then an existing GitHub asset, then this run's local
build. Existing assets are never overwritten. When bytes are carried over,
their original provenance bundle is retained; the workflow does not replace it
with a bundle for different bytes.

The first automated release must be a version above `0.2.0`. PyPI already holds
the hand-uploaded `0.2.0` wheel and sdist, so re-tagging `0.2.0` is a PyPI
no-op. Its GitHub Release has no sdist or bundle from this workflow.

## Known risks and limitations

- PyPI's Trusted Publisher and the GitHub `pypi` environment must agree on the
  owner, repository, workflow filename and environment name. A mismatch blocks
  publication.
- The exact-string version check rejects tags whose spelling differs from
  `project.version`, even if packaging tools would normalize them to the same
  version.
- PyPI index updates can take time to appear. The workflow waits for every
  published filename before it stages or publishes the GitHub assets.
- Existing bytes remain authoritative. A release with inconsistent PyPI and
  GitHub digests fails verification rather than replacing either copy.

## Outstanding decisions

- Reintroduce a native build matrix and platform-specific wheel attestation if
  CmdMox adds native distributions. The unused cibuildwheel workflow and action
  are removed for the current pure-Python package.
- Revisit release telemetry only if CmdMox adopts a telemetry sink and records
  its operating contract. Cuprum's telemetry steps are omitted.
- Keep Cargo checks out of this Python release workflow; native work will need
  its own build and verification design.
