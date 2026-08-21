"""Contract tests for the blocking Skylos dead-code lint gate."""

import shutil
import subprocess
import tomllib
import typing as typ
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WHITELIST_NAMES = frozenset({"bootstrap_shim_path"})
EXPECTED_ENTRYPOINT_REASONS = {
    "cmd_mox.environment._Win32Function.argtypes": (
        "_get_short_path assigns this ctypes argument signature to "
        "GetShortPathNameW through the typed protocol."
    ),
    "cmd_mox.environment._Win32Function.restype": (
        "_get_short_path assigns this ctypes return type to GetShortPathNameW "
        "through the typed protocol."
    ),
    "cmd_mox.ipc.server._BaseIPCServer._export_environment": (
        "_ServerLifecycle.start invokes this inherited hook before backend "
        "creation to export the active IPC environment."
    ),
    "cmd_mox.ipc.server.IPCServer._post_stop_cleanup": (
        "_ServerLifecycle.stop invokes this Unix transport hook after joining "
        "the backend thread to remove the socket."
    ),
    "cmd_mox.ipc.server.IPCServer._prepare_backend_start": (
        "_ServerLifecycle.start invokes this Unix transport hook before "
        "creating _InnerServer."
    ),
    "cmd_mox.ipc.server.IPCServer._stop_backend": (
        "_ServerLifecycle.stop invokes this Unix transport hook to shut down "
        "_InnerServer."
    ),
    "cmd_mox.ipc.server.IPCServer._wait_until_ready": (
        "_ServerLifecycle.start invokes this Unix transport hook after "
        "starting the backend thread to wait for the socket."
    ),
    "cmd_mox.ipc.server.NamedPipeServer._prepare_backend_start": (
        "_ServerLifecycle.start invokes this named-pipe hook; it is a no-op "
        "because named pipes leave no socket artefact."
    ),
    "cmd_mox.ipc.server.NamedPipeServer._stop_backend": (
        "_ServerLifecycle.stop invokes this named-pipe hook to stop the state "
        "and join its clients."
    ),
    "cmd_mox.ipc.server.NamedPipeServer._wait_until_ready": (
        "_ServerLifecycle.start invokes this named-pipe hook to wait for the "
        "state readiness event."
    ),
    "cmd_mox.ipc.server.ParsedRequest.validate": (
        "_request_pipeline invokes this validator before dispatch for Unix and "
        "named-pipe request ingress."
    ),
    "cmd_mox.ipc.server._NamedPipeState._poke_pipe": (
        "_NamedPipeState.stop invokes this helper to wake the named-pipe accept loop."
    ),
    "cmd_mox.ipc.server._NamedPipeState.stop": (
        "NamedPipeServer._wait_until_ready invokes this on timeout and "
        "NamedPipeServer._stop_backend invokes it during shutdown."
    ),
    "cmd_mox.ipc.server._ServerLifecycle._stop_backend.server": (
        "_ServerLifecycle.stop passes the stored backend instance through this "
        "abstract lifecycle hook."
    ),
}
EXPECTED_ENTRYPOINT_FULL_NAMES = frozenset(EXPECTED_ENTRYPOINT_REASONS)


def _pyproject() -> dict[str, object]:
    """Load the repository's Python project configuration."""
    return tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )


def _skylos_config() -> dict[str, object]:
    """Return the Skylos configuration from the project file."""
    tool_config = typ.cast("dict[str, object]", _pyproject()["tool"])
    return typ.cast("dict[str, object]", tool_config["skylos"])


def test_skylos_is_a_pinned_external_tool() -> None:
    """Keep Skylos out of the project environment and pin its tool release."""
    config = _pyproject()
    dependency_groups = typ.cast("dict[str, list[str]]", config["dependency-groups"])

    dependencies = dependency_groups["dev"]
    assert not any(dependency.startswith("skylos") for dependency in dependencies), (
        "Expected Skylos to be separately provisioned from the development "
        "dependency group."
    )
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "SKYLOS_VERSION = 4.33.2" in makefile, (
        "Expected the separately provisioned Skylos tool version to be exact."
    )
    assert "--from 'skylos==$(SKYLOS_VERSION)' skylos" in makefile, (
        "Expected Skylos to run from its separately provisioned tool environment."
    )


def test_skylos_allow_target_uses_the_standalone_subcommand() -> None:
    """Keep the name-only whitelist command separate from the scan command."""
    make_executable = shutil.which("make")
    assert make_executable is not None, "Expected make to be available for the test."

    result = subprocess.run(  # noqa: S603 - test executes make without a shell
        [
            make_executable,
            "--no-print-directory",
            "--dry-run",
            "NAME=bootstrap_shim_path",
            "skylos-allow",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, "Expected the Skylos allow-list target to expand."
    whitelist_commands = [
        line for line in result.stdout.splitlines() if "skylos whitelist" in line
    ]
    assert len(whitelist_commands) == 1, (
        "Expected the allow-list target to call the standalone Skylos "
        "whitelist subcommand without scan options."
    )
    whitelist_command = whitelist_commands[0]
    assert whitelist_command.endswith('skylos whitelist "${SKYLOS_NAME}"'), (
        "Expected the standalone command to put the whitelist subcommand "
        "before its name."
    )
    scan_options = ("--config-file", "--category", "--gate")
    assert not any(option in whitelist_command for option in scan_options), (
        "Expected the standalone whitelist command not to include scan options."
    )


def test_skylos_allow_target_requires_a_name() -> None:
    """Prevent an incomplete allow-list operation from invoking Skylos."""
    make_executable = shutil.which("make")
    assert make_executable is not None, "Expected make to be available for the test."

    result = subprocess.run(  # noqa: S603 - test executes make without a shell
        [make_executable, "--no-print-directory", "skylos-allow"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, "Expected an unnamed allow-list operation to fail."
    assert "Error: NAME is required for a named whitelist exception" in result.stderr


def test_skylos_configuration_is_strict_and_reasoned() -> None:
    """Require reasons for every configured Skylos exception."""
    skylos = _skylos_config()
    whitelist = typ.cast("dict[str, object]", skylos["whitelist"])
    documented = typ.cast("dict[str, str]", whitelist["documented"])
    whitelist_names = frozenset(typ.cast("list[str]", whitelist["names"]))
    assert whitelist_names == EXPECTED_WHITELIST_NAMES, (
        "Expected the reviewed Skylos whitelist names to stay enabled."
    )
    assert frozenset(documented) == whitelist_names, (
        "Expected every documented Skylos whitelist exception to be enabled."
    )
    assert all(reason.strip() for reason in documented.values()), (
        "Expected every documented Skylos whitelist entry to have a reason."
    )

    dead_code = typ.cast("dict[str, object]", skylos["dead_code"])
    entrypoints = typ.cast("list[dict[str, object]]", dead_code["entrypoints"])
    entrypoint_full_names = frozenset(
        full_name
        for entrypoint in entrypoints
        for full_name in typ.cast("list[str]", entrypoint["full_name"])
    )
    assert entrypoint_full_names == EXPECTED_ENTRYPOINT_FULL_NAMES, (
        "Expected the reviewed Skylos entry points to stay enabled."
    )
    entrypoint_reasons = {
        full_name: typ.cast("str", entrypoint["reason"])
        for entrypoint in entrypoints
        for full_name in typ.cast("list[str]", entrypoint["full_name"])
    }
    assert entrypoint_reasons == EXPECTED_ENTRYPOINT_REASONS, (
        "Expected every Skylos entry point to retain its verified runtime caller."
    )
    assert all(
        isinstance(reason := entrypoint.get("reason"), str) and reason.strip()
        for entrypoint in entrypoints
    ), "Expected every Skylos dead-code entry point to have a reason."

    gate = typ.cast("dict[str, object]", skylos["gate"])
    assert gate["strict"] is True, "Expected the Skylos gate to run in strict mode."


def test_make_lint_runs_local_blocking_dead_code_scan() -> None:
    """Keep the Skylos invocation deterministic and production-scoped."""
    make_executable = shutil.which("make")
    assert make_executable is not None, "Expected make to be available for the test."

    result = subprocess.run(  # noqa: S603 - test executes make without a shell
        [make_executable, "--no-print-directory", "--dry-run", "lint"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, "Expected make lint dry run to succeed."
    skylos_commands = [
        line
        for line in result.stdout.splitlines()
        if "skylos --config-file pyproject.toml" in line
    ]
    assert len(skylos_commands) == 1, (
        "Expected make lint to expand exactly one blocking Skylos command."
    )
    skylos_command = skylos_commands[0]
    assert "cmd_mox --category" in skylos_command, (
        "Expected the blocking Skylos command to use production-only targets."
    )
    assert " tests" not in skylos_command, (
        "Expected tests to be excluded from the production Skylos graph."
    )
    assert (
        "--category dead_code --gate --format concise --no-upload "
        "--no-provenance --no-grep-verify" in skylos_command
    ), "Expected the blocking Skylos command to retain its gate flags."
