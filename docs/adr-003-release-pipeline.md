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
together as `release-dist`, so later jobs use the attested bytes.

PyPI publication runs with Trusted Publishing in the `pypi` environment. The
workflow reads the PyPI Simple index and drops every filename already present
before using `pypa/gh-action-pypi-publish` with `skip-existing: true`. A
missing project index is treated as empty. A repeated tag therefore succeeds
without uploading an existing filename.

The workflow creates or reuses a draft GitHub Release before publishing to
PyPI. It selects canonical bytes in the order PyPI, existing GitHub asset, then
this run's local build. Before the PyPI job starts, the draft seed stage
retains the complete canonical distribution set and the provenance bundle that
attests it. A single bundle must cover every canonical distribution; the
workflow does not combine subjects from separate builds into one
sdist-and-wheel release. Existing assets are never overwritten.

Retaining the complete pair on the draft makes full-run retries safe. If PyPI
accepted the sdist but rejected the wheel, a later run selects the PyPI sdist
and the matching wheel already on the draft, then republishes the original
wheel bytes. It does not pair that sdist with a wheel from a new build. The
retained bundle continues to attest the original pair. If either distribution
or a single bundle covering the pair is unavailable, provenance validation
fails before another PyPI upload. After PyPI contains every distribution, the
final job checks that PyPI and GitHub hold matching SHA-256 digests and then
publishes the GitHub Release.

The first automated release must have a numeric release segment greater than
`0.2.0`. The version check rejects older versions and pre-release,
post-release, or local variants with the `0.2.0` release segment. This keeps
the historical version family out of the automated workflow: its GitHub Release
does not contain the PyPI-matching sdist or a bundle from this workflow. PyPI
already holds the hand-uploaded `0.2.0` wheel and sdist, so a PyPI staging run
for those filenames is a no-op.

## Known risks and limitations

- PyPI's Trusted Publisher and the GitHub `pypi` environment must agree on the
  owner, repository, workflow filename and environment name. A mismatch blocks
  publication.
- The exact-string version check rejects tags whose spelling differs from
  `project.version`, even if packaging tools would normalize them to the same
  version.
- PyPI index updates can take time to appear. The draft seed stages assets
  before PyPI publication; the final job waits for every published filename
  before reconciling and publishing the GitHub Release.
- Existing bytes remain authoritative. A release with inconsistent PyPI and
  GitHub digests fails verification rather than replacing either copy.
- PyPI permanently reserves uploaded filenames, including files later removed.
  If the draft seed upload is partial, a failed-job rerun reuses the original
  `release-dist`. If the pair is defective and PyPI has accepted no
  distribution, remove the draft's distributions and matching bundle before a
  full rerun. Once PyPI has accepted any distribution, advance the project
  version and cut a new tag for corrected bytes. See
  [PyPI's upload troubleshooting guidance](https://docs.pypi.org/publishing/troubleshooting/#why-am-i-getting-a-filename-or-contents-already-exists-or-filename-has-been-previously-used-error).

## Outstanding decisions

- Reintroduce a native build matrix and platform-specific wheel attestation if
  CmdMox adds native distributions. The unused cibuildwheel workflow and action
  are removed for the current pure-Python package.
- Revisit release telemetry only if CmdMox adopts a telemetry sink and records
  its operating contract. Cuprum's telemetry steps are omitted.
- Keep Cargo checks out of this Python release workflow; native work will need
  its own build and verification design.
