# Architectural decision record (ADR) 002: Nose code-duplication gate

## Status

Accepted. CmdMox runs `nose` 0.20.0 behind `scripts/duplication_gate.py` as a
blocking check inside `make lint`.

## Date

2026-10-09.

## Context and problem statement

CmdMox had no machine check against copy-paste duplication. Ruff and the three
Pylint tiers cover style, correctness and dead code, but none of them compares
one function against another. Duplication therefore accumulated silently until
a reviewer noticed it.

The estate already solved this problem. Episodic evaluated two clone detectors
head to head, chose `nose` 0.20.0, and wrapped it in a small gate that keeps
exceptions reviewable in version control (Episodic ADR-021, PR #276). CmdMox
adopts that decision by reference rather than repeating the detector
competition. Upstream `nose` is the Rust `nose-cli` clone detector from
`corca-ai/nose`, not the unrelated Python test framework of the same name on
PyPI.

CmdMox differs from Episodic in ways the adoption has to respect. CmdMox ships
a flat-layout package at the repository root and supports Python 3.12, while
Episodic targets 3.14. CmdMox keeps Rust sources under `src/` and its tooling
under `scripts/`. The gate helper itself needs 3.14, so the adoption has to
hold two interpreter floors apart without moving the application's.

## Decision drivers

- Catch function-level duplication mechanically, not by reviewer attention.
- Keep every suppression attached to a written reason, in a diff, in the same
  file as the rest of the project configuration.
- Reuse the estate's evaluated tool choice rather than re-running the
  comparison.
- Keep the application's Python floor, dependency set and platform support
  unchanged.
- Fail closed: a missing or wrong detector, a timeout or a malformed report must
  never look like a clean scan.
- Keep CI cost proportional to a lint gate, not to a benchmark or a full second
  test suite.

## Options considered

### No duplication check

The status quo. Ruff and Pylint remain the whole quality gate. Duplication is
caught only when a reviewer reads both copies.

### Nose behind a reasoned gate, as Episodic runs it

`nose` reports families ranked by refactoring value,
`scripts/duplication_gate.py` removes families covered by whole-family reasoned
exceptions, and the gate fails while unsuppressed families remain. Adopted here.

### A detector with native baseline and ignore files

A baseline records what is duplicated, not why it may stay. It also suppresses
by detector-assigned identifier or by line span, so the file churns whenever
code above it moves and a reviewer cannot see the justification.

| Topic              | No check | Reasoned gate     | Native baseline      |
| ------------------ | -------- | ----------------- | -------------------- |
| Coverage           | None     | Ranked families   | Ranked families      |
| Suppression review | n/a      | Reason per entry  | None                 |
| Churn on edits     | n/a      | Location globs    | Line spans and ids   |
| Tool cost          | None     | One pinned binary | One pinned binary    |
| Estate precedent   | n/a      | Episodic ADR-021  | Rejected by Episodic |

_Table 1: Duplication-gate options._

## Decision outcome

CmdMox adopts the Episodic nose gate.

The detector is pinned to 0.20.0 in three places that
`tests/test_toolchain_contract.py` keeps in agreement: `NOSE_VERSION` in the
`Makefile`, `NOSE_VERSION` in `.github/workflows/ci.yml`, and `version` in
`[tool.nose]` in `pyproject.toml`. `make install-nose` resolves
`nose-cli@0.20.0` through `cargo-binstall`'s Git mode against the upstream
repository, requires the installed binary to report the pinned version, and is
a no-op once it does.

`make lint` runs `make install-nose` and then the gate, so the blocking check
is not a separate step developers can forget. `make duplication` runs the gate
alone.

### Scan scope

`[tool.nose]` pins `roots = ["cmd_mox"]` with `exclude = ["unittests/*"]`.

`cmd_mox` is the installed package. `scripts/` is tooling, `tests/`,
`examples/` and `features/` are tests and examples, and `src/` is Rust, so none
of them is scanned. `cmd_mox/unittests/` ships inside the package, so the
exclusion is needed to keep the gate on maintained production code; the
omission is a decision recorded here rather than an accident.

The exclusion glob is matched relative to each root, not to the repository root.
`unittests/*` therefore names `cmd_mox/unittests/`; the repo-root form
`cmd_mox/unittests/*` matches nothing and would silently scan the test tree.

All three detection channels are pinned explicitly as
`mode = "syntax,semantic,near"` so a change to nose's defaults cannot widen or
narrow the gate unnoticed. The semantic channel is a narrow, witness-backed
subset of Type-4 duplication; it is not a general semantic-clone guarantee. The
floor of `min-size = 24` counts nose intermediate-language (IL) tokens, not
Python lines or abstract syntax tree nodes.

`surface = "all"` widens what nose reports, but `top = 30` bounds what the gate
adjudicates. The thirty highest-ranked families are checked; duplication below
that ranking is reported nowhere and enforced nowhere. A control run at
`top=300` returned the same families as the configured `top=30`, so the budget
is not saturated at the time of adoption. Saturation must be re-checked rather
than assumed, and a larger budget is a deliberate change, not a way to reach a
green build.

The scan surface is pinned by `scripts/tests/test_nose_config_contract.py`
rather than left to review. It reads this production table through the gate's
own `load_settings`, compares it with the intended scan, and asserts that each
root exists inside the repository and still selects production modules outside
the excluded subtree, and that each exclusion still hides files. Without that
check the failure this ADR is most exposed to — a root or exclusion that
silently stops matching, so the gate passes over an empty surface — would leave
no trace beyond a green build.

### Exceptions

An exception is a TOML entry under `[tool.duplication_gate]` with a non-blank
`reason`:

```toml
[[tool.duplication_gate.allow]]
members = ["cmd_mox/ipc/server.py::IPCServer.start", "cmd_mox/ipc/named_pipe.py::NamedPipeServer.start"]
reason = "A specific, reviewed explanation of why these must remain separate."
```

One `unit` key or several `members` keys, never both. A key is a
repository-relative POSIX path matched as a full glob, optionally suffixed
`::name` to require the exact unit name nose reported. A `::name` key never
matches an unnamed fragment.

An entry silences a family only when **every** location in that family matches
one of the entry's keys. Partial coverage from several entries does not
combine, and a new copy in an unlisted file blocks the build. Keys reference
locations rather than line numbers or detector identifiers, so moving code does
not invalidate an exception and regenerating the report does not renumber one.

Path-only keys are globs. They cover every location in matching files, present
and future, and the reasons say so where they are used. They are not a claim
that any future clone inside those files is safe.

Record entries with:

```bash
make duplication-allow FIRST='path[::name]' [SECOND='path[::name]'] REASON='...'
```

`FIRST`, `SECOND` and `REASON` are read only from the make command line, so an
ambient environment variable of the same name cannot create an exception. The
underlying CLI accepts repeated `--second` for families with more members.

### Stale entries

The gate reports allow entries that matched no finding. Absence from a capped
ranked report means "unmatched in this scan"; it is not proof the duplication
disappeared. An entry whose family fell below the `top = 30` cutoff is still
stale by that definition. Confirm with source inspection, or with an adequately
widened scan, before removing one, and do not delete an entry merely because
the gate called it stale.

### Failure behaviour

Exit statuses are 0 for a clean or fully allowed surface, 1 for unsuppressed
families, and 2 for a configuration or tool-execution error. A missing binary,
a version mismatch, a timeout, a non-zero detector exit, malformed JSON and an
invalid report shape all fail closed with an actionable diagnostic. None of
them degrades to an empty findings list or a skipped successful check.

## Consequences

- `make lint` and `make duplication` depend on `make install-nose`. CI caches
  `.tools/nose` under a key carrying the runner operating system and the pinned
  version, and re-verifies the reported version after restoring, so a cache hit
  from another platform or version cannot serve the gate.
- The installer forbids `cargo-binstall`'s `compile` and `quick-install`
  strategies. A platform with no prebuilt release binary is an explicit
  provisioning failure rather than a silent, costly source build or a fetch
  from an unapproved binary source.
- The gate runs as isolated CPython 3.14 tooling. The Makefile provisions that
  environment through the vendored `uv_gate` helper's tool channel, naming the
  interpreter and the two dependencies (`cyclopts==4.25.2`, `tomlkit==0.15.1`)
  explicitly; the script keeps its own PEP 723 block for direct `uv run` use,
  and a contract test fails if the two copies drift. The tool channel is used
  rather than `uv_gate run` because `run` always adds `--frozen`, which a PEP
  723 script cannot satisfy without a committed script lockfile, and it never
  goes online, so a cold cache could never provision the environment. The gate
  neither installs nor imports the application, builds no native extension and
  starts no application service. The application's own floor stays at 3.12 and
  its runtime dependencies are unchanged.
- The helper uses `fcntl.flock` and POSIX directory operations, and native
  Windows gate operation is not claimed. The supported developer workflow is
  POSIX, including WSL; Linux CI runs the gate. CmdMox's own platform support
  is unaffected, and the Windows CI job does not run the gate.
- `scripts/` carries its own `ty.toml`, and `make typecheck` excludes `scripts`
  from its application pass and checks it separately on 3.14. One invocation
  cannot check both surfaces, because the application pass would report
  interpreter mismatches in the tooling tree as defects.
- The gate depends on a pre-1.0 tool distributed as a platform-specific binary
  through GitHub releases rather than as a Python package.
- `make duplication-test` runs the focused helper tests on 3.14 with injected
  runners, so the suite never downloads the detector. CI runs it separately
  from the application suite.
- Nothing in CI runs a detector comparison, a benchmark or a second full test
  suite for this gate.

## Rejected alternatives

- **Repeating Episodic's detector comparison.** The decision is reused by
  reference. CmdMox records no independent precision, recall or timing claim.
- **`cargo install`, a floating `latest` installer, or a curl-to-shell
  bootstrap.** Each either compiles from source on an ordinary lint run or
  trusts an unverified artefact. In CI the `cargo-binstall` release archive is
  pinned to version 1.25.2 and the Linux x86_64 archive is verified against a
  hard-coded SHA-256 checksum. `make install-nose` then verifies the detector
  itself by requiring it to report the pinned `nose` version.
- **nose's native baseline and ignore files.** Rejected for the reasons in
  Table 1: an entry has to carry its justification.
- **Importing Episodic's application changes.** Episodic's exception list, path
  renames, database fixtures and unrelated tool migrations describe Episodic's
  tree. Only the gate modules and their focused tests are in scope here.
- **A shared multi-repository tooling framework.** The gate is ported as
  first-party code with attribution. Extracting an estate-wide framework is a
  larger decision that this adoption does not take.

## Follow-up work

- Re-check saturation when the ranked family count approaches `top = 30`.
- Review exceptions when the underlying code changes, and confirm stale reports
  against source before removing an entry.
- Revisit the detector pin when upstream releases a version whose report schema
  or channel behaviour changes.
