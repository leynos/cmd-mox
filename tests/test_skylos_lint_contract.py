"""Contract tests for the blocking Skylos dead-code lint gate."""

import shutil
import subprocess
import tomllib
import typing as typ
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WHITELIST_NAMES = frozenset({"bootstrap_shim_path"})
EXPECTED_ENTRYPOINT_FULL_NAMES = frozenset({
    "cmd_mox.environment._Win32Function.argtypes",
    "cmd_mox.environment._Win32Function.restype",
    "cmd_mox.ipc.server._BaseIPCServer._export_environment",
    "cmd_mox.ipc.server.IPCServer._post_stop_cleanup",
    "cmd_mox.ipc.server.IPCServer._prepare_backend_start",
    "cmd_mox.ipc.server.IPCServer._stop_backend",
    "cmd_mox.ipc.server.IPCServer._wait_until_ready",
    "cmd_mox.ipc.server.NamedPipeServer._prepare_backend_start",
    "cmd_mox.ipc.server.NamedPipeServer._stop_backend",
    "cmd_mox.ipc.server.NamedPipeServer._wait_until_ready",
    "cmd_mox.ipc.server.ParsedRequest.validate",
    "cmd_mox.ipc.server._NamedPipeState._poke_pipe",
    "cmd_mox.ipc.server._NamedPipeState.stop",
    "cmd_mox.ipc.server._ServerLifecycle._stop_backend.server",
})


def _pyproject() -> dict[str, object]:
    """Load the repository's Python project configuration."""
    return tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )


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


def test_skylos_configuration_is_strict_and_reasoned() -> None:
    """Require reasons for every configured Skylos exception."""
    config = _pyproject()
    tool_config = typ.cast("dict[str, object]", config["tool"])
    skylos = typ.cast("dict[str, object]", tool_config["skylos"])
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
