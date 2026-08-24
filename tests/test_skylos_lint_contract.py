"""Contract tests for Skylos dead-code detection in Make and CI.

Skylos scan options must follow its command-only CLI, while the standalone
``whitelist`` subcommand must appear immediately after ``skylos``. Skylos uses
its own Python AST, so the CLI must pin Python 3.14 to understand the project's
syntax. Makeutil provides structured Makefile assertions without depending on
whitespace or nearby source text.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tomllib
import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_MAKEUTIL_COMMAND: typ.Final = ("makeutil", "parse", "Makefile")
_MAKEUTIL_REVISION: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
_MAKEUTIL_TOOLCHAIN: typ.Final = "nightly-2026-05-28"
_MAKEUTIL_INSTALL_TOKENS: typ.Final = (
    "rustup",
    "toolchain",
    "install",
    "${MAKEUTIL_TOOLCHAIN}",
    "--profile",
    "minimal",
    "RUSTFLAGS=-Zpolonius=next",
    "cargo",
    "+${MAKEUTIL_TOOLCHAIN}",
    "install",
    "--git",
    "https://github.com/leynos/makeutil",
    "--rev",
    "${MAKEUTIL_REVISION}",
    "--locked",
    "--force",
    "makeutil",
)
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


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object, naming the unexpected ``subject`` on failure."""
    assert isinstance(value, dict), f"Expected {subject} to be a JSON object."
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON object array, naming the unexpected ``subject`` on failure."""
    assert isinstance(value, list), f"Expected {subject} to be a JSON array."
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string array, naming the unexpected ``subject`` on failure."""
    assert isinstance(value, list), f"Expected {subject} to be a JSON array."
    assert all(isinstance(item, str) for item in value), (
        f"Expected {subject} to contain only JSON strings."
    )
    return tuple(typ.cast("list[str]", value))


def _makefile_report() -> dict[str, object]:
    """Return Makeutil's complete, successfully parsed Makefile report."""
    completed = subprocess.run(  # noqa: S603 - fixed parser command.
        _MAKEUTIL_COMMAND,
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    report = typ.cast("dict[str, object]", json.loads(completed.stdout))
    parse = _mapping(report.get("parse"), subject="Makeutil parse report")
    assert parse.get("status") == "complete", (
        f"Makeutil must complete the Makefile parse, received {parse!r}."
    )
    return report


def _sole_variable(name: str) -> dict[str, object]:
    """Return Makeutil's sole variable fact for ``name``."""
    variables = _objects(_makefile_report().get("variables"), subject="variables")
    matches = [variable for variable in variables if variable.get("name") == name]
    assert len(matches) == 1, (
        f"Expected one Makefile variable named {name!r}, found {len(matches)}."
    )
    return matches[0]


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the only parsed rule for ``target`` that has recipes."""
    rules = _objects(_makefile_report().get("rules"), subject="rules")
    matches = [
        rule
        for rule in rules
        if target in _text_sequence(rule.get("targets"), subject="rule targets")
        and _objects(rule.get("recipes"), subject="rule recipes")
    ]
    assert len(matches) == 1, (
        f"Expected one recipe-bearing Makefile rule named {target!r}, found "
        f"{len(matches)}."
    )
    return matches[0]


def _variable_tokens(name: str) -> tuple[str, ...]:
    """Return shell-like tokens from Makeutil's raw variable value."""
    value = _sole_variable(name).get("raw_value")
    assert isinstance(value, str), f"Expected {name!r} to have a string value."
    return tuple(shlex.split(value))


def _recipe_tokens(target: str) -> tuple[tuple[str, ...], ...]:
    """Return shell-like tokens for every recipe in ``target``."""
    recipes = _objects(
        _sole_recipe_rule(target).get("recipes"), subject=f"{target} recipes"
    )
    return tuple(
        tuple(shlex.split(recipe_text))
        for recipe in recipes
        if isinstance(recipe_text := recipe.get("text"), str)
    )


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return the named job from a repository workflow."""
    workflow = yaml.safe_load((REPOSITORY_ROOT / workflow_path).read_text())
    workflow_mapping = _mapping(workflow, subject=f"{workflow_path} workflow")
    jobs = _mapping(workflow_mapping.get("jobs"), subject=f"{workflow_path} jobs")
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} job {job_name!r}")


def _sole_workflow_step(
    workflow_path: str, job_name: str, step_name: str
) -> dict[str, object]:
    """Return the sole named CI step from ``job_name``."""
    job = _workflow_job(workflow_path, job_name)
    steps = _objects(
        job.get("steps"), subject=f"{workflow_path} job {job_name!r} steps"
    )
    matches = [step for step in steps if step.get("name") == step_name]
    assert len(matches) == 1, (
        f"Expected one {step_name!r} step in {workflow_path} job {job_name!r}, "
        f"found {len(matches)}."
    )
    return matches[0]


def _run_skylos_allow(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the incomplete whitelist boundary without invoking Skylos."""
    environment: dict[str, str] = dict(os.environ)
    environment["NAME"] = ""
    environment.pop("REASON", None)
    environment.pop("SYMBOL", None)
    command = ["make", "skylos-allow", *arguments]
    return subprocess.run(  # noqa: S603 - fixed local Make boundary command.
        command,
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that ``command`` installs the pinned Makeutil parser."""
    assert isinstance(command, str), (
        f"{contract} must provide a Makeutil installation shell command."
    )
    assert (
        tuple(shlex.split(command.replace("\\\n", ""))) == _MAKEUTIL_INSTALL_TOKENS
    ), f"{contract} must pin the Makeutil installation command."


def _pyproject() -> dict[str, object]:
    """Load the repository's Python project configuration."""
    return tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )


def _skylos_config() -> dict[str, object]:
    """Return the Skylos configuration from the project file."""
    tool_config = typ.cast("dict[str, object]", _pyproject()["tool"])
    return typ.cast("dict[str, object]", tool_config["skylos"])


def test_lint_recipe_runs_the_production_dead_code_gate() -> None:
    """``make lint`` must scan only production code with Skylos's strict gate."""
    config = _pyproject()
    dependency_groups = typ.cast("dict[str, list[str]]", config["dependency-groups"])
    assert not any(
        dependency.startswith("skylos") for dependency in dependency_groups["dev"]
    ), "Skylos dependency contract must keep the detector out of the dev group."
    assert _variable_tokens("SKYLOS_VERSION") == ("4.33.2",), (
        "Skylos version contract must pin 4.33.2."
    )
    assert _variable_tokens("SKYLOS_PRODUCTION_TARGETS") == ("cmd_mox",), (
        "Skylos production-target contract must scan cmd_mox."
    )
    assert _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == ("tests",), (
        "Skylos exclusion contract must omit tests."
    )
    skylos_commands = [
        command for command in _recipe_tokens("lint") if command[:1] == ("$(SKYLOS)",)
    ]
    assert skylos_commands == [
        (
            "$(SKYLOS)",
            "$(SKYLOS_PRODUCTION_TARGETS)",
            "--exclude",
            "$(SKYLOS_EXCLUDE_FOLDERS)",
            "--category",
            "dead_code",
            "--gate",
            "--format",
            "concise",
            "--no-upload",
            "--no-provenance",
            "--no-grep-verify",
        )
    ], "Skylos lint command contract must scan production dead code strictly."


def test_whitelist_target_uses_skylos_subcommand_contract() -> None:
    """``skylos whitelist`` must precede its arguments and scan options."""
    assert _variable_tokens("SKYLOS_CLI") == (
        "$(UV_ENV)",
        "$(UV)",
        "tool",
        "run",
        "--python",
        "3.14",
        "--from",
        "skylos==$(SKYLOS_VERSION)",
        "skylos",
    ), "Skylos CLI contract must pin Python 3.14 and its tool release."
    assert _variable_tokens("SKYLOS") == (
        "$(SKYLOS_CLI)",
        "--config-file",
        "pyproject.toml",
    ), "Skylos scan command contract must add only the configuration file."
    whitelist_commands = [
        command
        for command in _recipe_tokens("skylos-allow")
        if command[:1] == ("$(SKYLOS_CLI)",)
    ]
    assert whitelist_commands == [
        (
            "$(SKYLOS_CLI)",
            "whitelist",
            "$${SKYLOS_SYMBOL}",
            "--reason",
            "$${SKYLOS_REASON}",
        )
    ], "Skylos whitelist command contract must dispatch before --reason."


def test_skylos_allow_requires_symbol_and_reason() -> None:
    """The whitelist target must reject incomplete input without running Skylos."""
    for arguments, expected_error in (
        ((), "Error: SYMBOL is required for a named whitelist exception"),
        (
            ("SYMBOL=bootstrap_shim_path",),
            "Error: REASON is required for a named whitelist exception",
        ),
    ):
        completed = _run_skylos_allow(*arguments)

        assert completed.returncode == 2, (
            "Skylos whitelist boundary must reject missing required arguments."
        )
        assert expected_error in completed.stderr, (
            "Skylos whitelist boundary must name the missing required argument."
        )


def test_skylos_allow_dry_run_preserves_the_whitelist_command_contract() -> None:
    """A valid dry run must reveal the command without writing an exception."""
    completed = subprocess.run(
        (
            "make",
            "--dry-run",
            "skylos-allow",
            "SYMBOL=bootstrap_shim_path",
            "REASON=Loaded by bootstrap shim",
        ),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        text=True,
    )

    assert completed.returncode == 0, (
        "Skylos whitelist dry-run contract must accept complete input."
    )
    assert (
        'skylos whitelist "${SKYLOS_SYMBOL}" --reason "${SKYLOS_REASON}"'
        in completed.stdout
    ), "Skylos whitelist dry-run contract must preserve subcommand argument order."


def test_skylos_configuration_is_strict_and_reasoned() -> None:
    """Require exact, caller-specific reasons for every Skylos exception."""
    skylos = _skylos_config()
    whitelist = _mapping(skylos.get("whitelist"), subject="Skylos whitelist")
    documented = typ.cast("dict[str, str]", whitelist["documented"])
    whitelist_names = frozenset(typ.cast("list[str]", whitelist["names"]))
    assert whitelist_names == EXPECTED_WHITELIST_NAMES, (
        "Skylos whitelist contract must keep reviewed names enabled."
    )
    assert frozenset(documented) == whitelist_names, (
        "Skylos whitelist contract must document every enabled exception."
    )
    assert all(reason.strip() for reason in documented.values()), (
        "Skylos whitelist contract must give every exception a reason."
    )
    dead_code = _mapping(skylos.get("dead_code"), subject="Skylos dead-code config")
    entrypoints = _objects(dead_code.get("entrypoints"), subject="Skylos entrypoints")
    entrypoint_full_names = frozenset(
        full_name
        for entrypoint in entrypoints
        for full_name in _text_sequence(
            entrypoint.get("full_name"), subject="entrypoint full name"
        )
    )
    assert entrypoint_full_names == EXPECTED_ENTRYPOINT_FULL_NAMES, (
        "Skylos entry-point contract must keep reviewed runtime callers enabled."
    )
    entrypoint_reasons = {
        full_name: typ.cast("str", entrypoint["reason"])
        for entrypoint in entrypoints
        for full_name in _text_sequence(
            entrypoint.get("full_name"), subject="entrypoint full name"
        )
    }
    assert entrypoint_reasons == EXPECTED_ENTRYPOINT_REASONS, (
        "Skylos entry-point contract must retain each verified runtime caller."
    )
    assert all(
        isinstance(reason := entrypoint.get("reason"), str) and reason.strip()
        for entrypoint in entrypoints
    ), "Skylos entry-point contract must give every exception a reason."
    gate = _mapping(skylos.get("gate"), subject="Skylos gate config")
    assert gate.get("strict") is True, (
        "Skylos gate configuration must enable strict mode."
    )


def test_ci_runs_the_lint_target_and_installs_makeutil() -> None:
    """Full-suite CI jobs must install the same pinned Makefile parser."""
    lint_step = _sole_workflow_step(
        ".github/workflows/ci.yml", "quality", "Run lint and dead-code detection"
    )
    assert lint_step.get("run") == "make lint", (
        "CI lint-step contract must invoke the shared make lint target."
    )
    for workflow_path, job_name in (
        (".github/workflows/ci.yml", "quality"),
        (".github/workflows/coverage-main.yml", "coverage-upload"),
    ):
        job = _workflow_job(workflow_path, job_name)
        environment = _mapping(
            job.get("env"), subject=f"{workflow_path} {job_name} environment"
        )
        assert environment.get("MAKEUTIL_REVISION") == _MAKEUTIL_REVISION, (
            f"{workflow_path} {job_name} must pin the Makeutil revision."
        )
        assert environment.get("MAKEUTIL_TOOLCHAIN") == _MAKEUTIL_TOOLCHAIN, (
            f"{workflow_path} {job_name} must pin the Makeutil toolchain."
        )
        parser_step = _sole_workflow_step(
            workflow_path, job_name, "Install Makefile parser"
        )
        _assert_makeutil_installation(
            parser_step.get("run"),
            contract=f"{workflow_path} {job_name} Makeutil-install contract",
        )
