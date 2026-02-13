# Make `CmdMox.replay()` Idempotent in Replay Phase

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT

PLANS.md does not exist in this repository.

## Purpose / Big Picture

Allow repeated calls to `CmdMox.replay()` to be safe when the controller is
already in `Phase.REPLAY`, while preserving all other lifecycle protections.
Success is observable when calling `replay()` twice in a row inside an entered
controller context no longer raises `LifecycleError`, and existing lifecycle
errors still occur for invalid transitions (for example calling `replay()`
before `__enter__()` or after `verify()`).

This change removes the need for downstream monkeypatches that wrap replay with
a phase guard and restores historical helper behavior that may call `replay()`
multiple times.

## Constraints

- Keep the public lifecycle model `record -> replay -> verify` intact.
- Only relax one edge: `replay()` called during `Phase.REPLAY` becomes a no-op.
- Preserve existing behavior for:
  - `replay()` before entering context (`LifecycleError`).
  - `replay()` during `Phase.VERIFY` (`LifecycleError`).
  - `verify()` phase checks and environment cleanup.
- Follow repository testing workflow for modified functionality:
  - update tests first,
  - observe failure,
  - implement code,
  - re-run all quality gates.
- Update user-facing docs in `docs/` to match final behavior.

## Tolerances (Exception Triggers)

- Scope: if implementation requires changing more than 5 files or 250 net new
  lines, stop and escalate.
- Semantics: if idempotence appears to require restarting IPC server or
  clearing `journal`, stop and escalate with options.
- Compatibility: if relaxing replay breaks plugin auto-lifecycle guarantees or
  context manager teardown guarantees, stop and escalate.
- Validation: if any required quality gate fails twice after targeted fixes,
  stop and escalate with captured logs.

## Risks

- Risk: Hidden coupling to strict replay transition in tests or helpers.
  Severity: medium.
  Likelihood: medium.
  Mitigation: add focused regression tests for repeated replay and retain
  strict errors for other phases.

- Risk: Accidental side effects on second replay call (server restart, journal
  reset, shim churn).
  Severity: medium.
  Likelihood: low.
  Mitigation: assert no-op behavior explicitly in tests (phase unchanged,
  existing recorded invocation/journal behavior unaffected).

- Risk: Documentation drift if strict lifecycle wording is not clarified.
  Severity: low.
  Likelihood: medium.
  Mitigation: update `docs/usage-guide.md` lifecycle section to state the
  idempotent replay exception.

## Progress

- [x] (2026-02-13 00:00 UTC) Drafted initial ExecPlan in
  `docs/execplans/idempotent-replay.md`.
- [ ] Modify relevant unit/behavior tests first to codify new requirement.
- [ ] Run targeted tests and confirm they fail before code change.
- [ ] Implement controller replay no-op behavior for `Phase.REPLAY`.
- [ ] Update documentation for lifecycle wording.
- [ ] Run full quality gates (`make test`, `make lint`, `make check-fmt`,
  `make typecheck`) and markdown gates if needed.
- [ ] Finalize plan sections (`Surprises`, `Decision Log`, `Outcomes`).

## Surprises & Discoveries

- Observation: Repository-level project memory tools (`qdrant-find`,
  `qdrant-store`) are not available in this environment session.
  Evidence: `list_mcp_resources` and `list_mcp_resource_templates` returned no
  configured resources/templates.
  Impact: Discovery relied on local repository inspection only.

## Decision Log

- Decision: Implement idempotence directly in `CmdMox.replay()` instead of
  monkeypatching in test harnesses.
  Rationale: Avoids hidden runtime mutation, keeps behavior explicit, and lets
  tests/documentation define contract centrally.
  Date/Author: 2026-02-13 / Codex.

- Decision: Preserve strict lifecycle errors outside the replay-repeat case.
  Rationale: Maintains guardrails and avoids masking real lifecycle misuse.
  Date/Author: 2026-02-13 / Codex.

## Outcomes & Retrospective

Pending implementation.

This section must be updated with:

- what changed,
- what evidence proved success,
- what follow-up work (if any) remains.

## Context and Orientation

Primary runtime behavior lives in `cmd_mox/controller.py`, especially:

- `CmdMox.replay()`
- `_check_replay_preconditions()`
- lifecycle state via `CmdMox.phase` and `Phase`.

Existing lifecycle expectations are tested in:

- `cmd_mox/unittests/test_controller_lifecycle.py`
- behavioral scenarios under `features/controller.feature` and
  `tests/test_controller_bdd.py`.

User-facing lifecycle documentation appears in:

- `docs/usage-guide.md` (Basic workflow section).

The pytest plugin drives auto-lifecycle and should remain unaffected:

- `cmd_mox/pytest_plugin.py`.

## Plan of Work

Stage A: Define behavioral contract in tests first.

Update lifecycle tests so they express the new rule: a second `replay()` call
in replay phase does nothing and does not raise, while replay from invalid
phases still raises. Run targeted tests to demonstrate the pre-change failure.

Stage B: Implement minimal runtime change.

Adjust controller replay flow so `Phase.REPLAY` returns immediately. Ensure this
no-op path does not restart IPC server, does not clear `journal`, and does not
mutate environment state.

Stage C: Align docs.

Amend lifecycle wording to keep strict sequence language but call out that
`replay()` is idempotent when already replaying.

Stage D: Validate and close.

Run full repository quality gates and record outcomes in this plan.

## Concrete Steps

1. Modify tests first in `cmd_mox/unittests/test_controller_lifecycle.py`:
   - replace the expectation that second `replay()` raises;
   - add/adjust assertions showing second `replay()` is a no-op;
   - keep assertions for strict failures in other phases.

2. Run targeted test module and confirm fail-before-fix:

    set -o pipefail
    pytest -q cmd_mox/unittests/test_controller_lifecycle.py | tee /tmp/idempotent-replay-targeted-fail.log

3. Implement replay no-op in `cmd_mox/controller.py`:
   - early-return when `self._phase is Phase.REPLAY`;
   - keep existing preconditions for all other phases.

4. Re-run targeted lifecycle tests:

    set -o pipefail
    pytest -q cmd_mox/unittests/test_controller_lifecycle.py | tee /tmp/idempotent-replay-targeted-pass.log

5. Update lifecycle docs in `docs/usage-guide.md`.

6. Run full code quality gates (required for Python changes):

    set -o pipefail
    make test | tee /tmp/idempotent-replay-make-test.log

    set -o pipefail
    make lint | tee /tmp/idempotent-replay-make-lint.log

    set -o pipefail
    make check-fmt | tee /tmp/idempotent-replay-make-check-fmt.log

    set -o pipefail
    make typecheck | tee /tmp/idempotent-replay-make-typecheck.log

7. If docs changed, also run markdown validation:

    set -o pipefail
    make markdownlint | tee /tmp/idempotent-replay-markdownlint.log

    set -o pipefail
    make nixie | tee /tmp/idempotent-replay-nixie.log

## Validation and Acceptance

The change is accepted only if all statements below are true:

- Repeating `replay()` while already in `Phase.REPLAY` does not raise.
- `replay()` still raises `LifecycleError` when called in invalid lifecycle
  states (`Phase.RECORD` without entering context, `Phase.VERIFY`).
- No regression in full test/lint/format/typecheck gates.
- Documentation reflects the replay idempotence behavior accurately.

Evidence to capture in implementation PR/summary:

- targeted lifecycle test output,
- full gate command exit status and log file paths,
- updated file list with rationale.

## Idempotence and Recovery

Implementation steps are safe to re-run.

If a change partially applies:

- reset only the in-progress edit to the affected file(s) manually,
- re-run the same step,
- re-run targeted tests before continuing.

If a quality gate fails:

- fix only the reported issue category first,
- re-run the same gate command,
- then re-run all required gates before completion.

## Artifacts and Notes

Expected touched files during implementation:

- `cmd_mox/controller.py`
- `cmd_mox/unittests/test_controller_lifecycle.py`
- `docs/usage-guide.md`
- `docs/execplans/idempotent-replay.md` (this plan, kept current).

Primary command logs:

- `/tmp/idempotent-replay-targeted-fail.log`
- `/tmp/idempotent-replay-targeted-pass.log`
- `/tmp/idempotent-replay-make-test.log`
- `/tmp/idempotent-replay-make-lint.log`
- `/tmp/idempotent-replay-make-check-fmt.log`
- `/tmp/idempotent-replay-make-typecheck.log`
- `/tmp/idempotent-replay-markdownlint.log`
- `/tmp/idempotent-replay-nixie.log`

## Interfaces and Dependencies

No new dependencies are expected.

Public API impact:

- behavioral relaxation of `CmdMox.replay()` during `Phase.REPLAY`;
- no new symbols required.

Potential downstream effect:

- tests/helpers that currently guard repeated replay manually may simplify.

## Revision note (required when editing an ExecPlan)

Initial draft created on 2026-02-13 for implementing idempotent replay
behavior in `CmdMox` while preserving strict lifecycle guards elsewhere.
