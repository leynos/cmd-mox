# RFC 001: Act execution environment for command doubles

## Status and scope

Proposed, 2026-09-30. This Request for Comments (RFC) specifies an opt-in
container execution environment and a thin adapter for `act`. It does not
implement either capability, approve the proposal, or change the default local
execution environment. New API names and examples below are provisional.

Companion documents are the [core design](python-native-command-mocking-design.md),
[stateful fake design](cmd-mox-fake-capabilities-design.md), and
[roadmap](roadmap.md#15-container-execution-and-act-integration).

## Context and problem statement

The local-validation guide separates direct helper tests using CmdMox from
black-box workflow tests using `act` and pytest. Host-side command shims do not
intercept commands in an isolated runner container. The workflow harness instead
asserts on exit status, artefacts, workspace effects, and structured logs.[^1]

A missing intermediate layer would execute the real workflow while replacing
selected external commands, such as `gh`, with controlled responses. This would
test argument construction, shell redirection, failure handling, and downstream
outputs without contacting the corresponding external service. Existing
black-box tests and authoritative GitHub-runner checks would remain necessary.

The source baseline for this proposal is CmdMox commit
`808befb775643e360d92a192bd87d852e2ee074f`. Relevant existing boundaries are:

- [Environment management](../cmd_mox/environment.py) combines resource
  allocation with changes to the host process environment.
- [Shim generation](../cmd_mox/shimgen.py) creates POSIX symlinks to an absolute
  installation path. Copying that directory alone does not deploy its target.
- [The controller](../cmd_mox/controller.py) accepts an injected `environment`,
  but still selects launcher and server behaviour using local assumptions.
- [The shim](../cmd_mox/shim.py) already performs passthrough execution on the
  client side. However, its lookup configuration originates in the controller,
  and shim-directory discovery assumes the socket shares that directory.
- [Invocation models](../cmd_mox/ipc/models.py) lack a working directory and an
  execution-target identity.

These are deployment and execution-context problems, not a need for a second
expectation matcher. Python callbacks, comparators, recording, and verification
should remain in the existing controller.

## Goals and non-goals

The proposal has four goals: preserve the fluent command-double API; prepare
shims for an explicit execution target without changing host command resolution;
make interception failures visible independently of workflow success; and retain
real workflow execution and observable outputs around the mocked boundary.

The first delivery targets a Linux pytest process and a local Linux container
daemon sharing the relevant filesystem and kernel. It supports one instrumented
job and one selected matrix leg per session, non-interactive text commands,
static responses, host-side `.runs(...)` handlers, and canned spies. Concurrent
command invocations within that job remain in scope.

The following are not first-delivery goals:

- A general workflow executor, a replacement shell, or a new fake-command DSL.
- Global interception of all process execution, shell builtins, functions,
  absolute executable paths, or commands that bypass the configured `PATH`.
- Transparent instrumentation of service containers, Docker actions, nested
  containers, or reusable workflows with additional execution environments.
- Native Windows runners, remote daemons, or automatic host-to-VM socket
  forwarding for Docker Desktop and Podman machine.
- Interactive terminals, streaming or binary protocol parity, arbitrary remote
  Python execution, or a security sandbox for hostile workflows.
- Certification of GitHub token scopes, OpenID Connect (OIDC), or live service
  behaviour. Those remain separate validation layers.

## Architecture and ownership

The workflow executes in the runner. A runner-local shim reports an invocation
to the host controller, which selects a response using the existing doubles.
The shim applies that response in the runner process. The Act adapter translates
prepared resources into runner configuration; it does not select responses.

```plaintext
pytest process                           Act job container
  CmdMox controller                        real workflow / helper
  expectations and Python callbacks                   |
  journal and verification                   runner-local command shim
             ^                                        |
             +--------------- IPC --------------------+

pytest harness -> ActBinding configuration -> act process
pytest harness <- artefacts, workspace effects, structured logs
```

_Figure 1: Command substitution crosses the runner boundary through
inter-process communication (IPC); workflow execution remains with Act._

### Execution environment contract

Introduce an internal execution-environment protocol through the existing
`CmdMox(environment=...)` seam. Separate resource preparation, target activation,
and cleanup. Use composition rather than subclassing `EnvironmentManager` and
reassigning its internal paths after entry.

A prepared environment describes the launcher bundle, controller endpoint,
runner endpoint, target platform, explicit shim directories, writable diagnostic
location, environment overlay, and owned resources. Host paths and target paths
are distinct typed values. Runner POSIX paths must not pass through host-specific
path normalization.

The existing local environment remains the default, preserving its context
manager, environment restoration, and public lifecycle. Container preparation
must not modify host `PATH`, `PATHEXT`, working directory, or process-wide
`CMOX_*` settings. Starting Act must use an explicit host environment snapshot.

Controller operations that currently choose shims or transports directly must
delegate to prepared-environment capabilities. Target launcher selection and
controller transport selection are independent. Local Windows named-pipe support
must remain intact even though the initial container backend is Linux-only.

Freeze the container's exported command set at `replay()`. Reject late command
registration with a lifecycle error instead of silently leaving a missing shim.
This restriction applies to the new container environment, not existing local
registration behaviour.

### Resource layout and endpoint mapping

Allocate resources outside the checked-out workspace, so checkout cleanup does
not delete a live session. A conceptual layout is:

```plaintext
Host resource                         Runner mount
session/bundle/                       /opt/cmd-mox/          read-only
short-runtime-root/session/ipc/       /run/cmd-mox/ipc/
session/status/                       /run/cmd-mox/status/   writable
```

_Figure 2: Immutable launchers, the live endpoint, and diagnostic state have
separate ownership and mount permissions._

Bind the host socket before starting Act, and mount its containing directory
rather than copying the socket or assuming both sides share one pathname. Export
`CMOX_IPC_SOCKET=/run/cmd-mox/ipc/ipc.sock` inside the runner. Choose a short
host socket root independently of pytest's potentially deep temporary path.

Validate command names, mount targets, source existence, permissions, and path
lengths before invocation. Reject overlapping mounts that hide the bundle or
endpoint. The socket directory and files must use explicit owner/group access
appropriate to the selected container user; broad world-writable permissions are
not an acceptable fallback. Mount only session resources, not the host home
folder, Python installation, or whole temporary directory.

Docker resolves bind sources on the daemon host, not necessarily the client
machine.[^2] A local client configuration alone therefore does not establish a
supported topology. Require a qualified same-host configuration and an in-runner
handshake. Reject remote or VM-backed arrangements with actionable diagnostics
unless a separately implemented backend supports them. Do not silently switch
to an unauthenticated TCP listener.

### Relocatable launcher bundle

The Python backend is sufficient for the initial bridge. Package all required
runtime modules with relative internal references and a manifest containing the
package version, protocol capabilities, command names, and bundle identity.
Do not depend on editable installations, host `PYTHONPATH`, or absolute symlink
targets outside the bundle.

Launch through a configured, absolute runner interpreter. The current
[package metadata](../pyproject.toml) requires Python 3.12 or newer; validate the
actual interpreter, imports, and permissions in the runner before use. A shim
for `python` or `python3` must not recursively select itself as its interpreter.

The planned phase 13 `cmdmox-mock` binary is a later interchangeable backend.
Select native assets by target operating system, architecture, and runtime
compatibility, not by the host wheel alone. Cache versioned bundles and verified
binaries; never compile Rust or install dependencies in every scenario. An
explicitly requested broken backend must fail rather than silently fall back.

## Act adapter and activation

`ActBinding` is an optional, thin adapter over a prepared container environment.
Its proposed responsibilities are constructing extra Act arguments and explicit
host environment values, validating configuration conflicts, checking activation
and completion, and exposing owned-resource metadata for harness cleanup.
It must not invoke Act or perform expectation matching itself.

The existing pytest harness continues to own subprocess execution, timeouts,
artefact-server lifetime, workspace isolation, and black-box assertions. Ordinary
CmdMox users must not require Act, a container daemon, or an Act-specific package
at import time.

### Configuration generation

The adapter generates mounts through `--container-options` and supplies runner
settings through an explicit environment file. It must account for Act's own
option parsing as well as process argument quoting. Test spaces and special
characters in host paths; reject forms the supported Act version cannot encode
unambiguously instead of constructing shell commands from concatenated input.

Act loads configuration from several `.actrc` locations.[^3] The harness must
isolate those locations or supply their contents for validation. It must also
supply explicit `.env`, secrets, and variables files so local credentials do not
leak through implicit defaults. Sanitizing only `subprocess` environment values
is insufficient.

Act's CLI qualifies `--container-options` for job containers without their own
options property.[^4] The first adapter must reject conflicting job-container
options, mount targets, or unsupported job graphs before running the scenario.
Do not silently overwrite production workflow settings. Select an explicit
workflow, job, and matrix leg; do not assume `-j` alone isolates every dependency
or reusable-workflow execution target.

Do not pass the host's expanded `PATH` into the runner. Preserve the runner path
and prepend the shim directory during activation. Image versions and Act
versions belong to an explicit, tested compatibility profile; mutable image tags
are not pins. Record the resolved image digest and tooling versions in test
metadata without hard-coding a moving latest-version claim into this RFC.

### Activation and coverage boundaries

An explicit activation step in the actual workflow is the first supported
integration. The following YAML is illustrative and requires the proposed
bundle and adapter:

```yaml
- name: Activate command doubles for local validation
  if: ${{ env.ACT && env.CMOX_IPC_SOCKET != '' }}
  shell: bash
  run: |
    source /opt/cmd-mox/activate.sh
```

Act documents `ACT` for step-level conditionals.[^3] The script must establish
its current shell path, append the shim directory to `$GITHUB_PATH`, and verify
the mounted bundle, interpreter, endpoint, status directory, and command
resolution. Additions to `$GITHUB_PATH` affect subsequent steps, not the process
that writes the file.[^5]

The handshake authenticates the session and negotiates protocol capabilities.
It uses a control message, not a fake command expectation. The controller records
successful activation for the intended target and exact exported command set.
An Act exit code of zero without that activation is an infrastructure failure,
including scenarios containing only optional stubs.

Place activation after relevant tool setup and before the first command that
must be doubled. Re-run activation or a resolution check after steps that can
prepend competing paths. It cannot retroactively instrument earlier action
pre-steps, and one successful check does not prove all future invocations use
the shims. Retain this coverage limitation in diagnostics and documentation.

A later workflow-overlay mode may inject activation into a temporary copy, but
must retain a reviewable diff and preserve original command bodies, conditions,
and dependencies. It must not become the implicit default. Docker-action and
nested-container coverage likewise require separate qualification, not an
assumption that job mounts automatically propagate everywhere.

## Invocation and protocol contract

Extend invocation context with optional, backward-compatible working-directory
and execution-scope fields. For container sessions, require a session ID,
invocation ID, target ID, target platform, and runner `cwd`. The target identifies
the single supported job/matrix scope. Later multi-job support must add explicit
scope routing before sharing controllers. Do not infer step IDs from log timing.

Keep raw runner values for diagnostics. Any portable workspace projection must
be explicit and must not overwrite the original value. Matchers and callbacks
remain host-side Python objects; no closure serialization is involved.

Use a versioned envelope for the container bridge, separate from the persisted
record/replay fixture schema. Negotiate before accepting invocation payloads.
An incompatible peer must fail with both versions identified; never feed unknown
fields into a legacy constructor and hope they work. Keep existing local IPC
behaviour compatible, and test old fixtures independently of bridge versioning.

Validate message kinds, identities, exit codes, field types, and maximum sizes.
Bound stdin collection and response waits as well as connection attempts; a
command waiting forever for stdin must not escape the scenario deadline. The
initial bridge transports finite text, not interactive, streaming, or binary
I/O. Malformed or oversized messages cause infrastructure errors, never success
responses or implicit passthrough.

Retry connection establishment only before sending an invocation. After any
ambiguous submission, do not resend automatically. Server-side invocation IDs
must prevent duplicate expectation consumption. Repeated IDs with changed
payloads are protocol errors. Exactly-once execution across crashes is not a
promised property; uncertain outcomes fail the test.

### Matching, concurrency, and callback environment

Retain existing stub, mock, and spy semantics. Stubs do not become mandatory
calls merely because they run in a container. Required counts and ordering remain
explicit, and configured non-zero responses remain valid command outcomes.

Serialize matching, expectation reservation, and journal mutations so concurrent
commands cannot consume one expectation twice. Initially serialize host callback
execution too; do not hold a global dispatch lock while waiting for a real
passthrough process. Reject unsupported callback re-entry explicitly rather than
allowing a deadlock. Record admission and completion separately, and do not use
completion order to invent a total order across unrelated commands.

Container callbacks receive runner data through `Invocation`. They must not rely
on the host working directory or mutate global `os.environ` to emulate the
runner. Preserve expectation matching and explicit environment overlays, but
apply them to invocation/response values rather than replacing host state.
Document this container-specific rule without changing existing local callback
behaviour. A stateful Python callback may keep state in the controller across
many shim processes; crossing that boundary alone does not require file-backed
state.

## Failure reporting and session lifecycle

Distinguish expected command failure from interception failure. A configured
exit status may be caught by the workflow; a mismatched mock, callback exception,
invalid protocol, missing activation, or lost endpoint must still fail pytest.
The infrastructure failure record must survive bounded-journal eviction.

A socket failure cannot reliably report itself over the failed socket. Provide
a separate session-owned status mount. Before sending an invocation, the launcher
writes a uniquely named start record; it then records a validated completion or
failure. Use exclusive file creation and atomic replacement for small bounded
records, not concurrent append to one shared JSON file. These records contain
identifiers and redacted error categories, not credentials or full payloads.

At finalization, reconcile launcher records with controller admission and
completion records. Missing terminal records, unacknowledged outcomes, corrupt
records, and reported failures invalidate the session even when workflow code
used `|| true`. Validate status-directory access during activation. A launcher
unable to write diagnostics must fail immediately and emit a reserved structured
error on stderr. The harness must recognize that fallback as a sticky session
failure, not merely retain it as ordinary workflow output. Reject quiet/logging
settings that hide the fallback. An absent expected completion is not success.

This is failure detection for cooperating test processes, not a tamper-proof
auditing mechanism. Commands that bypass the launcher, or hostile code that
forges/removes its status records, remain outside the guarantee.

The lifecycle is:

1. Allocate owned resources and register doubles without changing host state.
2. Freeze the command set, start IPC, and prepare the binding.
3. Start Act, require in-runner activation, and serve command invocations.
4. Wait for Act and relevant runner processes to finish, or cancel them using
   the harness deadline and exact owned-resource identities.
5. Stop accepting new invocations, drain accepted work to a bounded deadline,
   reconcile status, and run verification against a stable journal.
6. Finalize recordings and retain redacted diagnostics before deleting owned
   sockets, mounts, containers, and temporary directories.

Cleanup must run after setup failure, timeout, callback failure, or interruption.
Keep the controller alive through action post-steps that remain in the supported
job environment. A timed-out or incomplete drain is an infrastructure failure.
Preserve both workflow and verification failures, for example through an
exception group or attached pytest report, rather than hiding either one.

Act documents limits to run-step cancellation and job timeout support.[^6]
Killing only the Act subprocess therefore does not establish container cleanup.
Track exact container IDs or qualified session labels through the selected
runtime. Never prune unrelated containers. Retention for debugging is an
explicit option and must redact secrets and report retained resource paths.

## Passthrough, recordings, and filesystem effects

### Runner-local passthrough and fixture reuse

Reject `.passthrough()` in the initial container backend before starting Act.
Enable it only after runner-local resolution and result reporting pass dedicated
acceptance tests. The shim must execute the real command using the invocation's
current runner `PATH` with all explicit shim directories removed. Do not use the
controller's original path, or infer a shim directory from the socket location.
Preserve tools installed by earlier workflow steps and reject overrides that
resolve back into a shim.

Passthrough preserves runner `cwd`, applies explicit runner environment overlays,
and reports completion to the controller without holding its dispatch lock.
Connection retries must not repeat a real command. The local backend's existing
lookup behaviour remains unchanged.

Reuse phase 12 recording and replay after that contract is proven. Normalize
workspace-dependent values only through explicit mappings; scrub environment,
arguments, stdin, and outputs before persistence or diagnostic export. Session
IDs and ephemeral socket paths must not become fixture matching requirements.
Do not introduce an Act-only fixture format or pickle Python callbacks.

### Observable filesystem and workflow effects

A host callback writing a relative file writes on the host, not automatically
inside the runner. Later workspace helpers must take an explicit mapping from a
permitted runner root to a host bind-mount root, reject traversal and symlink
escapes, and refuse unmapped paths. Resolve the actual mount mapping rather than
assuming `$GITHUB_WORKSPACE` equals the host repository path.

Reuse phase 14 state-store and atomic-write helpers where they fit. Persistence
is optional for controller-owned in-memory fake state. Do not make all stateful
fake work a prerequisite for static responses or the first bridge.

Effects in container-private paths require a later, narrow runner-side effect
API with permitted roots and typed operations. Arbitrary remote evaluation is
not part of this proposal. A fake package manager must not silently create host
executables while pretending it installed them inside the runner.

Updating a shim's environment cannot change its parent shell or later steps.
Workflow outputs and persistent environment changes require runner-local writes
to GitHub command files or real shell code consuming the canned stdout.[^5]
Keep this distinction explicit for `$GITHUB_OUTPUT`, `$GITHUB_ENV`, and
`$GITHUB_PATH`; the response environment is not a substitute for those effects.

## Security, portability, and isolation

The endpoint grants access to host callbacks, so only trusted test workflows
should receive it. Use a per-session capability and restrictive filesystem
permissions to prevent accidental cross-session access. Authentication is not
isolation from a workflow that already possesses that capability.

Keep credentials out of fixtures, status records, argv-based configuration, and
retained logs. Default runner environment capture to an explicit allowlist plus
required context and keys needed by configured environment expectations. Tests
must opt into additional callback-visible values. Scrub diagnostic output
separately from matcher data. Never infer that a key
allowlist also sanitizes secrets embedded in argument or output strings.

Use fake credentials for stubbed publishing scenarios. Commands not intercepted
remain real. Act's offline mode can fetch missing actions and images; it does not
provide network isolation.[^3] CmdMox must not claim hermeticity or token-scope
validation. Any network restrictions belong to the separate execution profile.

Qualify rootless Podman separately, including user/group mappings and Security
Enhanced Linux (SELinux) volume labels.[^7] Never repair access failures with an
unrestricted permission change or disable host security globally. Existing local
Linux, macOS, and Windows tests must continue to pass after shared refactoring.

For pytest-xdist, isolate more than socket names: use separate worktrees,
artefact and status directories, container identities, writable caches, and
server ports. Act's CLI has an artefact-server default port, so sessions must not
all inherit it.[^4] The harness must reserve or allocate ports through a tested
mechanism rather than using an unprotected find-free-port-then-close sequence.
Disable unsupported server features or serialize them until safe allocation
exists. Do not advertise parallel Act support merely because CmdMox paths differ.

## Illustrative pytest integration

The following proposed API preserves the guide's `run_act` helper. That helper
must gain explicit working-directory, host-environment, and extra-argument
parameters, together with the lifecycle and isolation guarantees above. It is
not an existing runnable example.

```python
import json
from pathlib import Path

from cmd_mox import CmdMox
from cmd_mox.containers import ContainerEnvironment
from cmd_mox.integrations.act import ActBinding


def test_release_probe(tmp_path: Path, isolated_worktree: Path) -> None:
    environment = ContainerEnvironment(
        session_dir=tmp_path / "cmd-mox",
        runner_python="/usr/bin/python3",
    )
    with CmdMox(environment=environment) as mox:
        mox.mock("gh").with_args(
            "release", "view", "--json", "tagName"
        ).returns(stdout='{"tagName":"v9.9.9"}\n')
        mox.replay()
        binding = ActBinding(environment)

        code, _, logs = run_act(
            job="release-probe",
            artifact_dir=tmp_path / "artifacts",
            cwd=isolated_worktree,
            extra_args=binding.arguments(),
            env=binding.host_environment(),
        )
        binding.assert_complete()
        assert code == 0, logs
        output = isolated_worktree / "out" / "release.json"
        assert json.loads(output.read_text())["tagName"] == "v9.9.9"
```

The real workflow step would redirect the substituted command's output:

```bash
mkdir -p out
gh release view --json tagName > out/release.json
```

`assert_complete()` checks activation and bridge health after the harness has
stopped runner execution. The context manager retains mandatory expectation
verification. Context teardown must also check bridge health, so omitting the
explicit assertion cannot turn an unhealthy session into success. Fixture-based
automatic lifecycle integration is a later ergonomic layer over the same rules.

## Alternatives and trade-offs

Keeping only host helper mocks and black-box Act tests avoids a bridge, but
cannot control command dependencies while testing real workflow orchestration.
That remains a useful baseline, not a substitute for this additional layer.

Running pytest and the controller inside the job avoids cross-boundary IPC but
changes the harness deployment and complicates host assertions and arbitrary
host callbacks. It remains a possible execution profile, not the initial one.

Pre-generated shell stubs or standalone serialized scenarios avoid a live
controller but lose Python callbacks or duplicate matching and verification.
Mounting the host installation avoids bundling work but ties tests to host paths,
interpreters, and permissions. Neither is the proposed supported contract.

A network transport or relay could support remote daemons and VM-backed engines,
but adds authentication, exposure, discovery, and lifecycle requirements. Defer
it until the local bridge and a concrete target topology justify that work.

## Validation and adoption

Write tests before implementation. Unit and property tests should cover endpoint
mapping, relocatable paths, command-set freezing, option encoding, message
validation, duplicate invocation IDs, environment isolation, and status-record
reconciliation. Regression tests must retain local lifecycle and platform
behaviour. Tests must not require a live service or real publishing credentials.

Container tests should cover success, expected non-zero results, mismatches
swallowed by `|| true`, callback errors, missing or removed sockets, invalid
sessions, unsupported interpreters, unwritable diagnostics, incomplete records,
parallel commands, late path shadowing, and cancellation. Host-side tests must
show that mocking `git`, `docker`, or `python3` does not intercept the harness.

The first Act acceptance scenario must execute an actual workflow, activate a
`gh` double, exercise shell redirection, and verify both the invocation contract
and the resulting JSON. A negative case must fail pytest even when Act exits
successfully. A parallel scenario must prove cross-session isolation and leave
no owned resources after timeout. Pin the compatibility profile and publish
redacted failure artefacts from the integration job.

Keep ordinary tests daemon-independent. Run a small, resource-bounded integration
lane with cached images and bundles; do not fan out the whole suite per backend.
Later native and Podman qualification should reuse this contract suite.

Adoption extends the source guide with an additional controlled-dependency
workflow section rather than replacing its black-box approach. The validation
ladder becomes direct helper tests, Act tests with selected doubles, Act smoke
tests with real dependencies, and authoritative GitHub-runner validation.

## Delivery plan and open decisions

Roadmap phase 15 records the work as proposed and post-MVP. The initial usable
plateau comprises steps 15.1 through 15.5: environment separation, bridge
resources and protocol, Act binding, lifecycle/isolation, and a documented
acceptance example. Only explicit dependencies gate those steps; completing
phases 13 and 14 in their entirety is not required. Task 14.1.1 supplies
the atomic JSON writer for bridge status records; the state-store and router
remain optional later capabilities.

Steps 15.6 and 15.7 add native assets, runner passthrough, recording/replay, and
mapped workspace effects by reusing the relevant earlier tasks. Step 15.8
qualifies Podman and evaluates further execution targets independently. Each
plateau must remain usable without enabling later capabilities implicitly.

Before accepting public APIs, resolve the exact prepared-environment protocol,
the first pinned Act/image profile, numeric message and timeout limits, the
status-record schema, and the supported container user/permission configuration.
Before enabling passthrough, resolve child-command interception semantics and
explicit runner-path override policy. Before adding remote transports or shared
multi-job controllers, write a separate threat model and scope-routing design.
These choices must not weaken the invariants or silently broaden support.

[^1]:
    [Local validation of GitHub Actions with act and pytest](https://github.com/leynos/agent-helper-scripts/blob/main/documentation-library/local-validation-of-github-actions-with-act-and-pytest.md).

[^2]:
    [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/),
    particularly daemon-host resolution and Docker Desktop behaviour.

[^3]:
    [Act usage guide](https://nektosact.com/usage/index.html): configuration
    files, step conditionals, matrix selection, and offline-mode behaviour.

[^4]:
    [Act CLI definitions](https://github.com/nektos/act/blob/master/cmd/root.go):
    container options, explicit environment files, and artefact-server settings.

[^5]:
    [GitHub workflow commands](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands):
    environment files, outputs, and path propagation between steps.

[^6]:
    [Act unsupported functionality](https://nektosact.com/not_supported.html),
    including cancellation and job timeouts.

[^7]:
    [Podman run documentation](https://docs.podman.io/en/latest/markdown/podman-run.1.html):
    volume permissions, user namespaces, and SELinux labelling.
