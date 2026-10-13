# Adopt the Episodic Nose Duplication Gate

This execution plan (ExecPlan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`,
and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: IN PROGRESS

PLANS.md does not exist in this repository.

## Purpose / Big Picture

Give `cmd-mox` a blocking, deterministic code-duplication gate so repeated
logic is either removed or explicitly reviewed. The gate wraps the `nose` clone
detector (`nose-cli` 0.20.0) with a reasoned exception list, runs in
`make lint` and CI, and fails closed when the detector is missing, times out or
reports something the wrapper cannot adjudicate.

The design is adopted from `leynos/episodic` PR #276 at immutable revision
`d9e5ac0d254f375e2986f52d91a3b88c117c833b`. The PR's opening description
describes an earlier PyChase design; the merged `nose` code and ADR-021 take
precedence, and that is what is ported here.

Success is observable when:

- `make duplication` reports the configured production scan, exits 0 when every
  ranked family is either clean or covered by a reasoned exception, and exits 1
  with the offending families (repository-relative `path:start-end` spans,
  witness kind, value) when it is not.
- `make lint` runs the gate and still passes.
- `make duplication-test` runs the ported helper tests without downloading a
  detector.
- A planted clone in a disposable workspace fails the gate; a narrow reasoned
  exception for it makes the gate pass; adding an out-of-scope member to the
  same family makes it fail again.

## Constraints

- Adopt only the five gate/support modules and their focused tests. Do **not**
  copy `benchmarks/duplication/**`, `benchmarks/score_support.py`, the
  duplication benchmark tests, the head-to-head report, or the benchmarking CI
  targets. Do not rerun a detector competition.
- Do not import episodic's application changes, exception entries, package
  names, database fixtures, Skylos/Pylint migrations, coverage changes or
  unrelated dependencies.
- Keep the application Python floor (`requires-python = ">=3.12"`), the
  interpreter matrix and runtime dependencies unchanged. The gate helper stays
  isolated CPython 3.14 tooling, selected explicitly.
- No `cargo install`, no floating `latest` installer, no curl-to-shell
  bootstrap. `cargo-binstall` in git mode with
  `--disable-strategies compile,quick-install`; a missing trusted binary is an
  explicit provisioning failure.
- The `Makefile` allow target accepts `FIRST`/`SECOND`/`REASON` only from the
  make command line, never from ambient environment variables, and passes the
  values through the environment so quoting survives.
- Exceptions are whole-family and reasoned; no native baselines, no
  mass-generated reasons, no automatic allowlisting of the initial scan.
- Preserve every existing lint check and quality gate.

## Tolerances (Exception Triggers)

- Scope: if the adoption needs files beyond `scripts/`, `scripts/tests/`,
  `Makefile`, `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`,
  `tests/test_toolchain_contract.py`, `AGENTS.md` and `docs/`, stop and
  escalate.
- Detector: if nose 0.20.0 cannot express the configured scan, or emits a
  schema that the ported validation rejects, stop and escalate rather than
  loosening validation.
- Adjudication: if a blocking family cannot be resolved by extraction or a
  defensible reasoned exception, stop and record it rather than manufacturing a
  generic utility to silence it.
- Validation: if a required quality gate fails twice after targeted fixes, stop
  and escalate with the captured log paths.
- Ranking: the configured `top = 30` must not be raised to obtain green CI. If
  the ranked report saturates, stop and escalate with the evidence.

## Risks

- Risk: the gate's scan surface silently narrows (mistyped root, new ignore
  pattern) and reports a clean run over nothing. Severity: high. Likelihood:
  medium. Mitigation: roots are asserted to exist and select files in tests;
  the survey recorded 27 ranked production families before adjudication, so a
  subsequent empty or near-empty report is a signal, not a pass.
- Risk: a ported exception helper partially covers a family because matching
  degrades to any-member. Severity: high. Likelihood: low. Mitigation: port the
  whole-family `all(any(...))` predicate and keep its property tests.
- Risk: the allow-list writer loses comments or concurrency safety when adapted
  to this repository's `pyproject.toml`. Severity: medium. Likelihood: medium.
  Mitigation: keep `tomlkit` round-tripping, the sidecar `flock`, the atomic
  writer options, and the persistence tests.
- Risk: the application test interpreter (3.12) collects the 3.14-only tooling
  tests and fails. Severity: medium. Likelihood: medium. Mitigation: keep gate
  tests out of the application `testpaths` collection path in a way the
  application conftest cannot reach, and verify `make test` still passes.
- Risk: existing lint checks regress while wiring the gate into `lint`.
  Severity: medium. Likelihood: low. Mitigation: run the full gate suite after
  wiring, sequentially.

## Progress

- [x] (2026-10-09) Read the authoritative reference at revision `d9e5ac0d`:
  ADR-021, the five gate modules, the ten focused test files, `[tool.nose]` /
  `[tool.duplication_gate]`, the reference `Makefile`, and the duplication
  sections of `ci.yml`, `.gitignore`, `AGENTS.md` and `developers-guide.md`.
- [x] (2026-10-09) Confirmed the nose v0.20.0 JSON schema (v9) and CI guidance
  from upstream docs: reasoned exceptions wrapped around a ranked report are
  the documented integration; `--fail-on any` is not applied before the wrapper
  adjudicates.
- [x] (2026-10-09) Renamed the branch to `adopt-nose-code-deduplication`
  tracking `origin/adopt-nose-code-deduplication`.
- [x] (2026-10-09) Discovered the target repository conventions: flat layout
  with `cmd_mox/` at the repository root, `requires-python = ">=3.12"`,
  setuptools backend, `scripts/` is tooling, `src/` is Rust only, canonical
  lint is `make lint`, application platform support is Linux + Windows.
- [x] (2026-10-09) Provisioned and version-verified the pinned detector at
  `.tools/nose/nose` (untracked), and recorded the survey: 27 ranked families
  over `roots = ["cmd_mox"]` with `exclude = ["unittests/*"]`, well inside the
  `top = 30` budget, with all family sources read.
- [x] (2026-10-09) Committed `.gitignore` entries for `.tools/` and the
  duplication-gate lock (`659d92d`).
- [x] (2026-10-09) Ported the five gate modules into `scripts/`
  (`atomic_write.py`, `nose_schema.py`, `nose_detector.py`,
  `duplication_allowlist.py`, `duplication_gate.py`) and added `[tool.nose]` /
  `[tool.duplication_gate]` to `pyproject.toml` with `roots = ["cmd_mox"]`,
  `exclude = ["unittests/*"]`, `top = 30`.
- [x] (2026-10-09) Added the `Makefile` targets (`install-nose`, `duplication`,
  `duplication-test`, `duplication-allow`) and wired `check` into `lint`.
- [x] (2026-10-09) Ported the focused tests into `scripts/tests/` (124 tests,
  run with `make duplication-test`).
- [x] (2026-10-09) Adjudicated every ranked family at source across four
  passes, extracting nine genuine duplicates at their correct layer, and
  recorded eight reasoned entries covering ten families of intentional
  parallel. The ranked count fell 27 → 18 → 17 → 14 → 13 → 11 → 5 → 0 blocking
  as the extractions and entries landed; the family-level detail is in
  "Adjudication record" below.
- [x] (2026-10-09) Proved the ranked report is not saturated: a `top=300`
  control run returned the same families as the configured `top=30`, so no
  exception needed a widened scan to corroborate it.
- [x] (2026-10-09) Verified fail-closed behaviour: a planted out-of-scope
  clone was refused by the named-key exception, the corresponding entry was
  reported stale rather than silently reused, and the plant was reverted with
  no residue.
- [x] (2026-10-09) Extended the toolchain contract test for three-way pin
      agreement (`tests/test_toolchain_contract.py`), plus a contract for the
      split typecheck.
- [x] (2026-10-09) Wired CI: the detector cache keyed on runner OS and pinned
      version, a verified pinned `cargo-binstall` download, `make install-nose`,
      a version re-check, and `make duplication-test`.
- [x] (2026-10-09) Split `make typecheck` into an application pass and a PEP
      723 tooling pass, because the application's 3.12 assumption reported the
      tooling tree's 3.14-only attributes as defects.
- [x] (2026-10-09) Updated `AGENTS.md`, the developer guide, ADR 001's tier
      list, the new ADR 002 and the documentation index.
- [x] (2026-10-09) Ran the planted-clone smoke test in a disposable
      `/tmp/nose-smoke` workspace against the real pinned binary: clean pass,
      planted clone refused, narrow exception accepted, added out-of-scope
      member refused again, identical output across four runs, and all three
      fail-closed paths returning exit 2.
- [x] (2026-10-09) Confirmed and removed one stale allow entry by source
      inspection plus a widened `top=400` scan.
- [x] (2026-10-14) Ran the full gate set, sequentially, logging to `/tmp`. The
      first `lint` run surfaced three findings introduced by the deduplication
      work itself: an R9104 (trivial attribute wrapper) on `Expectation.times`,
      an R9109 (repeated substring probes) in the new CI cache contract test,
      and a SKY-U006 (unused parameter) on `_ServerLifecycle._stop_backend`.
      All three were fixed at source (details under "Adjudication record →
      Lint findings raised by the deduplication work"); `check-fmt`,
      `duplication-test`, `duplication`, `typecheck`, Markdown lint and nixie
      were already green and unchanged.
- [x] (2026-10-14) Committed as `878bc54`, pushed over SSH, and opened draft
  PR [#315](https://github.com/leynos/cmd-mox/pull/315). Not merged.

## Surprises & Discoveries

- Observation: the nose `--exclude` glob is matched relative to each ROOT, not
  to the repository root. Evidence: a repo-root-relative glob
  (`cmd_mox/unittests/*`) had no effect; `unittests/*` reduced 116 families to
  27. Impact: the configured exclusion is documented as root-relative, and the
  gate configuration carries a comment saying so.
- Observation: the largest ranked family (`6556c336b81936c9`, value 439.4) is
  not a 338-line duplicate. Evidence: a skeleton query plus source inspection
  showed the shared block is the 24-line `CallbackNamedPipeServer.__init__` /
  `CallbackIPCServer.__init__` pair; the huge value comes from ranking
  aggregation across a 769-line file paired with a 143-line file. Impact:
  adjudication must read the actual shared block, not the headline value.
- Observation: the shipped package contains `cmd_mox/unittests/`, so the
  `test` scope family at `test_doubles.py` is inside the production root.
  Impact: the scan excludes `unittests/*`, and the omission is documented
  rather than silently gating test code.
- Observation: system Python is 3.12 while the reference helper requires
  >=3.14 and uses `fcntl.flock` plus POSIX directory operations. Impact: the
  gate runs as isolated 3.14 tooling; native Windows gate operation is not
  claimed.

## Decision Log

- Decision: reuse the repository's existing atomic-write helper rather than
  adding a second equivalent implementation. Rationale: task instruction, and
  the writer needs exactly the behaviour the existing helper already provides
  once its options are honoured. Date/Author: 2026-10-09 / Claude Code.
- Decision: keep the gate's scan surface at `roots = ["cmd_mox"]` with
  `exclude = ["unittests/*"]`. Rationale: start from maintained production
  Python; `scripts/`, `tests/`, `examples/` and `features/` are tooling or
  examples, `src/` is Rust, and `cmd_mox/unittests/` is the in-package test
  tree. Date/Author: 2026-10-09 / Claude Code.
- Decision: do not apply `--fail-on any`. Rationale: the wrapper must be able
  to adjudicate reasoned exceptions before the exit status is decided.
  Date/Author: 2026-10-09 / Claude Code.
- Decision: the detector installer adds
  `--disable-strategies compile,quick-install`. Rationale: a downstream
  adoption must not silently start a costly source build or reach an unapproved
  binary source. Date/Author: 2026-10-09 / Claude Code.
- Decision: extract nine families and record eight reasoned entries covering
  ten families, rather than driving the blocking count to zero by refactoring.
  Rationale: the surviving families are signatures, docstring sections, import
  lists and one-statement idioms. Removing them would require boolean-mode
  helpers, a third re-export module, a call-site-passed literal or a reversed
  layer dependency — all explicitly forbidden by the brief. The reference
  adoption carries a comparable exception list for the same reason.
  Date/Author: 2026-10-09 / Claude Code.
- Decision: inline the two single-statement lifecycle forwarding wrappers
  (`_start_backend_thread`, `_join_backend_thread`) and hoist the `None`
  backend check into `_ServerLifecycle.stop`. Rationale: the wrappers were
  private forwarding shims kept only to give the detector something to see, and
  `stop` is the sole owner of the "no backend was ever created" state. This
  dissolved four families without adding any abstraction. Date/Author:
  2026-10-09 / Claude Code.
- Decision: keep the path-only exception keys despite their glob breadth.
  Rationale: nose reports those fragments unnamed, so `path::name` keys cannot
  match them at all. Per the brief, the reasons state explicitly that such
  entries cover future locations within their scope and are not described as
  protection against every new clone. Date/Author: 2026-10-09 / Claude Code.
- Decision: give the PEP 723 tooling under `scripts/` its own `ty.toml` and run
  `make typecheck` as two invocations, excluding `scripts` from the application
  pass. Rationale: ty assumes the application floor (3.12) from
  `requires-python`, so a single pass reported `typing.TypeIs` and
  `PurePosixPath.full_match` — both 3.13+ — as unresolved attributes in files
  whose own PEP 723 metadata declares 3.14. A per-line suppression was probed
  and rejected: ty does not narrow through a suppressed `TypeIs`, so two
  knock-on errors survived. Date/Author: 2026-10-09 / Claude Code.
- Decision: declare `cyclopts` and `tomlkit` in the tooling pass's
  `allowed-unresolved-imports` rather than adding them to the application
  dependency set. Rationale: `uv run` provisions the PEP 723 dependencies per
  invocation and they are never installed into the application virtualenv;
  adding them to the project would be a runtime-dependency change the
  application does not need. Verified that an unlisted import still fails.
  Date/Author: 2026-10-09 / Claude Code.

## Outcomes & Retrospective

The adoption landed as a first-party port rather than a cherry-pick. Nine
families were dissolved by extracting the shared behaviour at its owning layer;
the eight remaining reasoned entries cover ten families, each naming the
independent contract or architectural boundary an extraction would couple. The
detector is provisioned by `make install-nose`, the blocking check is inside
`make lint`, CI caches and re-verifies the binary, and
`tests/test_toolchain_contract.py` keeps the three version pins in agreement.

Three lessons are worth carrying forward.

1. Adjudication has to read the shared block, not the headline value. The
   largest ranked family in this scan was not the largest duplicate: ranking
   aggregates across file size, so the top family resolved to a 24-line
   constructor pair. Reading the skeleton and the source is the only reliable
   way to decide.
2. A detector that matches signatures and docstring sections produces families
   that cannot be extracted. Roughly half the surviving entries are exactly
   that. The alternative — manufacturing a boolean-mode helper or a re-export
   shim to satisfy the detector — is forbidden by the brief and would have made
   the code worse.
3. Removing a forwarding wrapper is a legitimate way to dissolve a family, and
   it is the only one that made four families disappear without adding an
   abstraction. The wrappers existed to be seen.

Retrospective on cost: the gate costs one cached binary plus one bounded query.
No benchmark, no detector comparison and no second test suite were added, and
the harness's own comparison is reused by reference rather than reproduced.

## Deliberate deviations from the reference

| Reference behaviour                                         | Downstream behaviour                                                                                    | Why                                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Installer permits cargo-binstall's `compile` strategy       | `--disable-strategies compile,quick-install`                                                            | A downstream lint run must not start a costly source build or fetch an unapproved binary.                 |
| Stale entry printed as `remove it; the duplication is gone` | Stale entry printed as `unmatched in this ranked scan; confirm the duplication is gone before removing` | Absence from a capped ranked report is not proof the duplication disappeared.                             |
| Reference atomic writer                                     | The same writer is ported unchanged                                                                     | CmdMox has no equivalent helper, so the brief's "reuse an existing helper" clause has nothing to bind to. |
| Single `make typecheck` pass                                | Two passes, `scripts/` excluded from the application pass                                               | The application's 3.12 assumption reported the 3.14-only tooling attributes as defects.                   |

_Table: Deliberate downstream deviations from the reference adoption._

Everything else matches the reference: `top = 30`, the three-channel pin, the
24-token floor, `surface = "all"`, the `roots = [<package>]` shape, the
detector pin and its Git-mode cargo-binstall provisioning, the no-op-when-
correct install, and the location-keyed whole-family exception contract.

## Adjudication record

Every ranked family in the configured scan (`roots = ["cmd_mox"]`,
`exclude = ["unittests/*"]`, `surface = "all"`, `top = 30`) was read at source.
The scan is **not saturated**: a control run at `top=300` returned the same
families as `top=30`, so nothing below the cutoff is being silently skipped.

### Saturation evidence

A
`nose query --root cmd_mox all top=300 --mode syntax,semantic,near
--min-size 24 --exclude 'unittests/*' --format json`
run returned the same family set as the configured `top=30` run. The ranking
budget therefore does not hide any family in this scan, and no intended
exception had to be corroborated by widening it.

### Extractions (genuine duplication, fixed at source)

Nine families were resolved by extracting the shared behaviour. In each case
the extraction landed at the layer that already owned the invariant; no new
generic utility, inheritance hierarchy, boolean-mode helper or cross-layer
dependency was introduced.

| Family (before)                                                                                            | Extraction                                                                                                                                                     | Layer                     |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| `CallbackIPCServer.__init__` ~ `CallbackNamedPipeServer.__init__`                                          | `_CallbackServerBase` in `cmd_mox/ipc/_server_core.py` holds the callback signature and flattens it into the base constructor; both transports inherit from it | IPC transport core        |
| `_validate_stdin` ~ `_validate_environment` (`test_doubles.py`)                                            | `_validate_optional_attr` — one optional-attribute comparison, with the two named wrappers keeping the call sites readable                                     | Spy assertion helper      |
| `_check_replay_preconditions` ~ `_check_verify_preconditions` (`controller.py`)                            | `_require_replay_environment()` — the shared environment precondition, called from both phase checks                                                           | Controller phase boundary |
| `_connect_unix_with_retries` ~ `_connect_pipe_with_retries` (`ipc/client.py`)                              | `context.validate()` hoisted into `_dispatch_request`, so no transport can reach a retry loop unvalidated                                                      | Client dispatch boundary  |
| `validate_positive_finite_timeout` ~ `validate_retry_backoff` ~ `validate_retry_jitter` (`_validators.py`) | `_validate_numeric` — the shared bool rejection and bounds application, with the caller supplying its own bound and wording                                    | Validator module          |
| `validate_invocation_payload` ~ `validate_passthrough_payload` (`ipc/json_utils.py`)                       | `_build_payload[PayloadT]` — one build-and-log-malformed helper, parameterized by model and description                                                        | IPC payload construction  |
| `Expectation.times` ~ `Expectation.times_called`                                                           | `times` delegates to `times_called`                                                                                                                            | Fluent DSL                |
| `pywin32` import guards (repeated per transport module)                                                    | One guarded import block in `cmd_mox/ipc/windows.py`, re-exported from its `__all__` so the fake-based tests keep patching module attributes                   | Windows transport seam    |
| `_ServerLifecycle.stop`'s `None`-tolerating hooks (three sites)                                            | `stop` skips a backend that was never created; `_stop_backend` now takes a non-optional `BackendT` and `_join_backend_thread` was inlined                      | Lifecycle base class      |

Two consequences worth recording. First, inlining `_join_backend_thread` and
`_start_backend_thread` removed two single-statement forwarding wrappers rather
than preserving them to satisfy the detector — they were exactly the
"compatibility shims for private APIs" the brief forbids, and their guards
belonged in the one caller. Second, hoisting the `None` check into `stop` also
dissolved two unrelated one-line families (a `if <x> is None: return` idiom and
a duplicate `if server is None: return` pair) as a by-product, because the
guarded statements no longer exist.

### Reasoned exceptions (intentional parallels)

Ten families are covered by the eight `[tool.duplication_gate]` entries below;
three of those entries cover two families each. Every entry names the specific
independent contract or boundary an extraction would couple. No exception was
written to obtain a green run: every one of them is a family whose shared text
is a signature, a docstring section, an import list or a one-statement idiom.

| Entry                                                                                          | Why it must stay duplicated                                                                                                                                                                                                              |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ipc/client.py::invoke_server` ~ `report_passthrough_result`                                   | nose matches a 3-line signature and a `Returns` docstring block; each body is one delegating call to `_send_request` with a different request kind. A shared wrapper would erase two distinct public entry points without sharing logic. |
| `ipc/_server_core.py::_default_invocation_response` ~ `response_strategy.py::default_response` | Deliberate layer boundary. `response_strategy` imports `Response` from `cmd_mox.ipc`, so the `ipc` package cannot import it back without a cycle. One is the transport fallback, the other a user-selectable strategy.                   |
| `shimgen.py::is_broken_symlink` ~ `has_non_symlink_collision`                                  | Inverse one-line predicates. A shared helper would need a boolean mode flag selecting `is_symlink()` or `exists()` — the exact shape-only helper the brief forbids.                                                                      |
| `pytest_plugin.py` ~ `verifiers.py` (guard shape)                                              | `if not <collection>: return "<literal>"` in four diagnostic formatters, each with a different literal and surrounding formatter.                                                                                                        |
| `expectations.py::in_order` ~ `any_order`                                                      | Fluent DSL verbs differing only in the boolean they store; a combined setter would force callers to pass that boolean.                                                                                                                   |
| `environment.py` ~ `fs_retry.py` ~ `ipc/named_pipe.py`                                         | One `if not IS_WINDOWS: return` idiom in three unrelated Windows-only paths (PATHEXT, read-only repair, accept-loop guard). The first two are called unconditionally by cross-platform callers and must no-op on POSIX.                  |
| `verifiers.py` ~ `test_doubles.py`                                                             | Inverse length comparisons in the order verifier plus a repeated `self._validate_spy_usage("<method>")` call whose argument differs every time.                                                                                          |
| `controller.py` ~ `passthrough.py`                                                             | One call repeated: `_require_replay_environment()` from two phase checks, and `_prune_expired_locked()` from two ledger operations. The shared helper already exists in both cases.                                                      |

The path-only entries (`environment.py`, `fs_retry.py`, `ipc/named_pipe.py`,
`pytest_plugin.py`, `verifiers.py`, `test_doubles.py`, `controller.py`,
`passthrough.py`) use globs because nose reports those fragments **unnamed**; a
`path::name` key could never match them. Those entries therefore also cover any
future family contained entirely within their files. They are scoped
exceptions, not a claim that every new clone in those files is safe, and the
reasons say so.

### Fail-closed verification

A planted clone was added to `cmd_mox/shimgen.py` (a third
`path.is_symlink() and not path.exists()` predicate) and the gate correctly (a)
refused to apply the named-key symlink exception, and (b) reported that
existing entry as **stale** rather than degrading to an empty findings list:

```text
stale allow entry (cmd_mox/shimgen.py::is_broken_symlink ~
  cmd_mox/shimgen.py::has_non_symlink_collision): unmatched in this ranked scan
duplicate code: 1 unsuppressed family/families
  cmd_mox/shimgen.py:330-343 is_broken_symlink ~
  cmd_mox/shimgen.py:364-377 has_non_symlink_collision ~
  cmd_mox/shimgen.py:325-327 new_out_of_scope_broken_link_clone (similar, value 18.1)
```

The plant was then reverted, and `make duplication` returned to
`duplication gate passed; 11 allowed by reasoned exceptions` with no stale
entries reported.

### Disposable-workspace end-to-end demonstration

The in-repo plant above proves the gate refuses a planted clone inside this
tree. A separate smoke test exercised the whole gate contract against the real
pinned binary in a throwaway workspace under `/tmp/nose-smoke`, whose package
is deliberately small and whose `top = 30` budget is far above the two families
it can produce — so the ranking cap cannot hide the canary the way a saturated
real scan could. The five gate modules were copied in unchanged; only the
workspace's own `pyproject.toml` points them at the planted package.

| Step                            | Command                                                   | Result                                                                     |
| ------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------------------- |
| Clean surface                   | `check` with one module present                           | `duplication gate passed`, exit 0                                          |
| Substantial planted clone       | `check` with `pkg/beta.py` added                          | 2 unsuppressed families, exit 1                                            |
| Narrow reasoned exception       | `allow --first pkg/alpha.py::summarize_ledger --second …` | family silenced; the small family still blocked, exit 1                    |
| Both families allowed           | `check`                                                   | `2 allowed by reasoned exceptions`, exit 0                                 |
| Added out-of-scope third member | `check` after adding `pkg/gamma.py`                       | 3-member family refused, original entry reported stale, exit 1             |
| Determinism                     | `check` ×4, normalized with timing lines stripped         | one identical output hash across all four runs                             |
| Missing binary                  | `check` with `NOSE_BIN` pointing at a non-existent path   | `configuration error: cannot run …`, exit 2, names `make install-nose`     |
| Wrong version                   | stub binary reporting `nose 0.19.0`                       | `configuration error: … reports 'nose 0.19.0' but the gate pins …`, exit 2 |
| Malformed report                | stub reporting the pinned version, printing non-JSON      | `configuration error: nose report is not valid JSON`, exit 2               |

_Table: Disposable-workspace smoke-test evidence._

The original planted-clone run, verbatim:

```text
duplicate code: 2 unsuppressed family/families
  pkg/alpha.py:11-56 summarize_ledger ~ pkg/beta.py:11-56 summarize_statement (subdag, value 51.8)
  pkg/alpha.py:59-61 count_active_accounts ~ pkg/beta.py:59-61 count_active_labels (exact, value 3.9)
```

and, with a third copy added to a file neither exception names:

```text
stale allow entry (pkg/alpha.py::count_active_accounts ~
  pkg/beta.py::count_active_labels): unmatched in this ranked scan;
  confirm the duplication is gone before removing it
duplicate code: 1 unsuppressed family/families
  pkg/alpha.py:59-61 count_active_accounts ~
  pkg/beta.py:59-61 count_active_labels ~
  pkg/gamma.py:6-8 count_live_rows (exact, value 9.6)
```

No planted clone and no temporary exception remains: the workspace lives under
`/tmp` only, and `git status` in the repository tree shows no smoke-test
residue.

### Stale-entry removal

The first full run after the smoke test reported one stale entry, the
`cmd_mox/ipc/named_pipe.py` ~ `cmd_mox/ipc/server.py` import-list exception. It
was not deleted on the gate's word. Source inspection showed the two import
blocks now diverge — different stdlib imports, different shared helper modules
— and a widened `top=400` scan (which returns the entire ten-family report)
contained no family spanning both files. The entry was therefore removed, and
`make duplication` now passes with ten families allowed by eight entries and no
stale entries.

### Lint findings raised by the deduplication work

The first full `make lint` run after the extractions landed surfaced three
findings, all of them introduced by the deduplication work rather than
pre-existing:

| Finding                                                                   | Cause                                                                                                                                                                                        | Resolution                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `R9104` `trivial-attribute-wrapper` on `Expectation.times`                | Dissolving the duplicated `times`/`times_called` bodies left `times` a one-line forwarding alias, which the df12 wrapper rule flags.                                                         | Kept the delegation — it is what removes the clone family — and added a reasoned `# pylint: disable-next=trivial-attribute-wrapper` pragma naming `times` as deliberate public DSL.                                                    |
| `R9109` `prefer-snapshot-substring` in the new CI cache contract test     | The test probed the raw `ci.yml` text with five substring assertions, tripping the rule's three-probe threshold.                                                                             | Rewrote the test to assert on the PyYAML-decoded workflow document (the idiom `tests/test_skylos_lint_contract.py` already uses) instead of on its text, so each fact is a structural assertion and no suppression is needed.          |
| `SKY-U006` `unused parameter: server` on `_ServerLifecycle._stop_backend` | Making `stop` skip a never-started backend required `_stop_backend` to take a non-optional `BackendT`; the base hook's parameter is deliberately unused because both transports override it. | Restored the `type = "parameter"` entry-point rule for `cmd_mox.ipc._server_core._ServerLifecycle._stop_backend.server` with a refreshed caller-specific reason, and its two mirrored entries in `tests/test_skylos_lint_contract.py`. |

The `R9104` pragma names a df12-only message, which the classic PyPy pass
cannot register; Pylint reports `W0012` for such a pragma even under
`disable = ["all"]`. The estate pattern therefore applies: `pyproject.toml`
disables `unknown-option-value` for the classic pass, `pylintrc-df12.toml`
enables it for the plugin pass that registers the complete vocabulary, and
`tests/test_pylint_tier_contract.py` now pins that split so a future edit
cannot silently stop validating pragma names.

## Context and Orientation

The gate pipeline is five cooperating modules under `scripts/`:

- `scripts/nose_detector.py` — reads `[tool.nose]`, resolves and
  version-checks the pinned binary, builds the argument vector, runs it in the
  repository root with a bounded timeout, and normalizes the JSON report into
  ordered findings.
- `scripts/nose_schema.py` — the report schema validators, the `Location` /
  `Finding` dataclasses, and the error hierarchy (`GateConfigError`,
  `GateExecutionError`).
- `scripts/duplication_allowlist.py` — loads reasoned exceptions, validates
  exception keys, and matches an exception against a whole family.
- `scripts/atomic_write.py` — the temporary-sibling replacement helper used by
  the allow-list writer.
- `scripts/duplication_gate.py` — the CLI entry point that partitions findings
  into blocking, allowed and stale, reports them, and implements `check` and
  `allow`.

The task brief requires reusing an existing suitable local helper rather than
creating a second equivalent implementation, but inspection showed no such
helper exists here: `scripts/typos_rollout_cache.py` writes a cache blob with
`Path.write_text`, and the allow-list writer needs a temporary sibling, mode
preservation, a file-and-directory fsync, and failure cleanup. Adapting that
cache writer into a general atomic writer would have changed its contract for
its only caller to serve an unrelated one, so the reference's
`scripts/atomic_write.py` is ported as a first-party gate module instead. That
is a deliberate deviation from "reuse an existing helper", recorded because the
precondition (a suitable existing helper) does not hold in this repository.

Supporting changes live in `Makefile` (targets and pins), `pyproject.toml`
(`[tool.nose]`, `[tool.duplication_gate]`), `scripts/tests/` (the ported
focused tests, beside the existing `test_typos_rollout.py`),
`tests/test_toolchain_contract.py` (pin agreement), `.github/workflows/ci.yml`
(detector cache, gate run), `AGENTS.md` and `docs/`.

## Plan of Work

Stage A: port the five modules into `scripts/`, adapting repository paths,
keeping the reference structure, error hierarchy and Python-3.14 inline-script
declaration. The atomic writer is ported rather than reusing
`scripts/typos_rollout_cache.py`, whose cache-blob contract is unrelated to
what the allow-list writer needs; see "Context and Orientation".

Stage B: configure the scan in `pyproject.toml` and install it via the
`Makefile` (`install-nose`, `duplication`, `duplication-test`,
`duplication-allow`), wiring `check` into `lint`.

Stage C: port and adapt the ten focused test files into `scripts/tests/`,
replacing episodic paths with `cmd_mox` equivalents, and extend the toolchain
contract test.

Stage D: adjudicate the 27 ranked production families. Extract genuine
duplication at the correct layer with regression tests; record intentional
parallels as whole-family reasoned exceptions.

Stage E: wire CI, run the planted-clone smoke test in a disposable workspace,
and confirm deterministic normalized output.

Stage F: document (ADR, developer guide, agent instructions, index, command
help), run every required gate sequentially with `tee` logging, commit, push
and open a draft pull request.

## Concrete Steps

1. Port the modules; keep `NOSE_BIN`, the version check, the explicit working
   directory, JSON output and the bounded timeout.

2. Add configuration:

    [tool.nose]
    version = "0.20.0"
    roots = ["cmd_mox"]
    mode = "syntax,semantic,near"
    min-size = 24
    surface = "all"
    top = 30
    exclude = ["unittests/*"]

3. Install and run:

    make install-nose
    make duplication | tee /tmp/duplication-$(git branch --show-current).out

4. Port tests and run:

    make duplication-test | tee /tmp/duplication-test-$(git branch --show-current).out

5. Adjudicate every blocking family, then re-run. Repeat until the gate is
   green for the right reasons.

6. Run the full gate suite, sequentially, each with `tee`:

    make check-fmt
    make lint
    make typecheck
    make test
    make markdownlint
    make nixie

7. Commit, push, open the draft PR.

## Validation and Acceptance

The change is accepted only if all of the following hold:

- `make duplication` exits 0 on the adjudicated tree and exits 1 when a planted
  clone is added.
- A narrow reasoned exception makes the planted clone pass; one extra member
  outside the exception's keys makes it fail again.
- `make duplication-test` passes without downloading the detector.
- `make lint`, `make check-fmt`, `make typecheck`, `make test`,
  `make markdownlint` and `make nixie` all pass, or their pre-existing failures
  are reported as such with evidence.
- The final diff contains no benchmark corpus, no episodic application change,
  no runtime-dependency or Python-floor change, and no broad suppression.

## Idempotence and Recovery

Every step is safe to re-run. `make install-nose` is a no-op when the installed
detector already reports the pinned version. The planted-clone smoke test runs
in a disposable workspace outside the repository and leaves nothing behind.

If the gate reports a family that cannot be adjudicated, record it in this plan
with the family id, spans and the reason it is unresolved rather than
suppressing it or raising the ranking budget.

## Artefacts and Notes

Reference: `leynos/episodic` PR #276, revision
`d9e5ac0d254f375e2986f52d91a3b88c117c833b`.

Primary evidence logs (all under `/tmp`, names use the branch slug):

- `/tmp/duplication-adopt-nose-code-deduplication.out`
- `/tmp/duplication-test-adopt-nose-code-deduplication.out`
- `/tmp/lint-adopt-nose-code-deduplication.out`
- `/tmp/check-fmt-adopt-nose-code-deduplication.out`
- `/tmp/typecheck-adopt-nose-code-deduplication.out`
- `/tmp/test-adopt-nose-code-deduplication.out`
- `/tmp/markdownlint-adopt-nose-code-deduplication.out`
- `/tmp/nixie-adopt-nose-code-deduplication.out`

## Interfaces and Dependencies

New tooling dependencies, isolated to the gate helper's inline script metadata
(not application dependencies): `cyclopts==4.25.2` and `tomlkit==0.15.1`.

New command-line surface:

```text
make install-nose
make duplication
make duplication-test
make duplication-allow FIRST='path[::name]' [SECOND='path[::name]'] REASON='...'
```

Exit statuses: 0 clean or fully allowed, 1 unsuppressed families, 2
configuration or tool execution error.

## Revision note (required when editing an ExecPlan)

Initial draft created from the completed reference reading and the 27-family
production survey.

Revised 2026-10-14: recorded the outcome of the full gate run, added "Lint
findings raised by the deduplication work" to the adjudication record, and
closed the gate-run checkbox. The delivery checkbox stays open until the push
and draft pull request complete.

Revised again 2026-10-14: reconciled the adjudication counts with the artefacts
the gate actually reads. Three claims were wrong: the extraction count (nine
table rows, not eleven), the exception count (eight `[tool.duplication_gate]`
entries covering ten ranked families, not eleven families), and the ported test
count (124, not 123). A stale table row described a `ipc/named_pipe.py` ~
`ipc/server.py` import-list exception that had already been removed in
"Stale-entry removal"; it was deleted, and the path-only entry list was
corrected to match. Every count now reproduces from `pyproject.toml` and a
detector scan.
