# Comparison/Migration Guide for shellmock Users

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE

PLANS.md does not exist in this repository.

## Purpose / Big Picture

Deliver a clear, runnable migration guide that helps shellmock users adopt
CmdMox without guesswork. Success is observable when a new documentation page
shows side-by-side translations from shellmock CLI usage to CmdMox Python
fixtures, lists feature parity and gaps, and the documentation index links to
it. A reader should be able to copy one of the examples, run tests, and get the
expected mocked command behaviour.

## Constraints

- Documentation-only change: do not modify runtime code or public APIs.
- Keep all documentation in `docs/` and link it from `docs/contents.md`.
- Do not add new dependencies or tooling.
- Use existing terminology from `docs/usage-guide.md` and
  `docs/python-native-command-mocking-design.md` for consistency.
- No non-ASCII characters unless the target file already uses them.

## Tolerances (Exception Triggers)

- Scope: if the documentation update needs more than 6 files or more than 400
  net new lines, stop and escalate.
- Dependencies: if referencing new external tools or libraries becomes
  necessary, stop and escalate.
- Ambiguity: if shellmock behaviour is unclear from its docs and materially
  affects guidance, stop and present options with trade-offs.
- Validation: if `make markdownlint` or `make nixie` fail twice after fixes,
  stop and escalate with logs.

## Risks

    - Risk: Misstating shellmock semantics or CLI flags.
      Severity: medium
      Likelihood: medium
      Mitigation: Cross-check shellmock README and examples; note assumptions.

    - Risk: CmdMox examples drift from current public API.
      Severity: medium
      Likelihood: low
      Mitigation: Align examples with `docs/usage-guide.md` and existing
      examples in `examples/`.

    - Risk: Migration guide is too high-level to be actionable.
      Severity: low
      Likelihood: medium
      Mitigation: Provide copy-pasteable examples and a checklist.

## Progress

    - [x] (2026-01-12) Drafted initial ExecPlan.
    - [x] (2026-01-12) Created `docs/shellmock-migration-guide.md` with mapping,
      examples, and checklist.
    - [x] (2026-01-12) Linked the guide from `docs/contents.md` and added a
      cross-reference in `docs/usage-guide.md`.
    - [x] (2026-01-12) Ran `make markdownlint` and `make nixie`.

## Surprises & Discoveries

    - Observation: None yet.
      Evidence: N/A
      Impact: N/A

## Decision Log

    - Decision: Plan to publish the migration guide as a standalone document
      in `docs/shellmock-migration-guide.md`, linked from `docs/contents.md`
      and referenced from `docs/usage-guide.md`.
      Rationale: Keeps the guide discoverable and focused while preserving the
      usage guide as the main API reference.
      Date/Author: 2026-01-12 / Codex
    - Decision: Keep shellmock snippets conceptual and note that CLI syntax
      can vary by version.
      Rationale: Avoids locking the guide to a single shellmock CLI variant
      while still giving users a clear translation path.
      Date/Author: 2026-01-12 / Codex

## Outcomes & Retrospective

Completed the shellmock migration guide, linked it from the documentation
index and usage guide, and recorded design decisions in
`docs/python-native-command-mocking-design.md`. Documentation linting and
Mermaid validation pass via `make markdownlint` and `make nixie` (warnings about
the `nixie` Makefile target remain pre-existing).

## Context and Orientation

CmdMox documentation lives in `docs/usage-guide.md` (user-facing API guidance)
and `docs/python-native-command-mocking-design.md` (design rationale and
feature mappings, including a shellmock-to-CmdMox table). The documentation
index is `docs/contents.md`. The roadmap entry for this work is under
"VIII. Documentation, Examples & Usability" in `docs/cmd-mox-roadmap.md`.

Shellmock is a shell-script-based command mocking tool. CmdMox provides similar
behaviour with Python shims and a record-replay-verify workflow.

## Plan of Work

Stage A: Research and outline (no code changes).

Review shellmock documentation and identify its core CLI flags and workflow
patterns. Compare them to CmdMox APIs and existing examples in `examples/` and
`docs/usage-guide.md`. Draft an outline: quick-start migration, feature mapping
(table), translated examples, and behavioural differences (e.g., verification
phase, environment handling, stdin matching).

Stage B: Draft the migration guide document.

Create `docs/shellmock-migration-guide.md` with:

- A short "why migrate" summary.
- A side-by-side feature mapping table (shellmock CLI flags to CmdMox API).
- At least two end-to-end examples: simple stub, strict mock with verification.
- A section on differences and gotchas (PATH shims, strict verify phase,
  fixture lifecycle).
- A checklist for migrating existing shellmock tests.

Stage C: Integrate with the docs index.

Add the new guide to `docs/contents.md`. Add a short note or link from
`docs/usage-guide.md` so it is discoverable for new users.

Stage D: Validate documentation quality.

Run `make markdownlint` and `make nixie` to ensure Markdown and Mermaid
validation succeed. Fix any lint or diagram issues.

## Concrete Steps

1) Inspect existing documentation and examples:

    rg -n "shellmock" docs
    rg -n "mock" examples

2) Draft the migration guide at `docs/shellmock-migration-guide.md` following
   the outline from Stage B.

3) Update `docs/contents.md` with a link entry for the new guide.

4) Add a cross-reference in `docs/usage-guide.md` (e.g., under the introduction
   or FAQ) pointing to the migration guide.

5) Validate docs:

    set -o pipefail
    make markdownlint | tee /tmp/markdownlint.log

    set -o pipefail
    make nixie | tee /tmp/nixie.log

## Validation and Acceptance

Quality criteria (what "done" means):

- The migration guide exists at `docs/shellmock-migration-guide.md` and is
  linked from `docs/contents.md`.
- The guide includes a feature mapping table and at least two translated
  examples.
- `make markdownlint` and `make nixie` both exit with status 0.

Quality method (how we check):

- Run the linting commands above and confirm zero errors in the logs.

## Idempotence and Recovery

The documentation edits are safe to re-run. If linting fails, fix the
reported Markdown issues and re-run the commands. If the guide content needs
adjustment, edit the single guide file and re-run linting.

## Artifacts and Notes

Expected linting transcripts (examples only):

    $ make markdownlint
    markdownlint docs/...

    $ make nixie
    nixie: all mermaid diagrams are valid

## Interfaces and Dependencies

No new dependencies. Reference shellmock documentation for CLI flag meanings
and use existing CmdMox APIs from `docs/usage-guide.md` and
`docs/python-native-command-mocking-design.md`.

## Revision note (required when editing an ExecPlan)

Updated the plan to COMPLETE status, recorded implementation progress, and
summarised outcomes after creating the migration guide, linking it in the docs
index and usage guide, and running the required lint and Mermaid validations.
