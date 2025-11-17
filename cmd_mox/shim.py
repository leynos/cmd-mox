#!/usr/bin/env python3
"""Generic command shim for CmdMox."""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import ntpath
import os
import sys
import typing as t
from pathlib import Path

_BOOTSTRAP_SPEC = importlib.util.spec_from_file_location(
    "cmd_mox._shim_bootstrap", Path(__file__).resolve().with_name("_shim_bootstrap.py")
)
if _BOOTSTRAP_SPEC is None or _BOOTSTRAP_SPEC.loader is None:  # pragma: no cover
    _bootstrap_error = "Unable to load cmd_mox._shim_bootstrap"
    raise ImportError(_bootstrap_error)
_bootstrap_module = importlib.util.module_from_spec(_BOOTSTRAP_SPEC)
_BOOTSTRAP_SPEC.loader.exec_module(_bootstrap_module)
bootstrap_shim_path = t.cast("t.Any", _bootstrap_module).bootstrap_shim_path
CMOX_SHIM_COMMAND_ENV = "CMOX_SHIM_COMMAND"
IS_WINDOWS = os.name == "nt"


bootstrap_shim_path()
uuid = importlib.import_module("uuid")
_command_runner = importlib.import_module("cmd_mox.command_runner")
execute_command = _command_runner.execute_command
prepare_environment = _command_runner.prepare_environment
resolve_command_with_override = _command_runner.resolve_command_with_override
validate_override_path = _command_runner.validate_override_path
_environment_mod = importlib.import_module("cmd_mox.environment")
CMOX_IPC_SOCKET_ENV = _environment_mod.CMOX_IPC_SOCKET_ENV
CMOX_IPC_TIMEOUT_ENV = _environment_mod.CMOX_IPC_TIMEOUT_ENV
CMOX_REAL_COMMAND_ENV_PREFIX = _environment_mod.CMOX_REAL_COMMAND_ENV_PREFIX
_ipc_mod = importlib.import_module("cmd_mox.ipc")
Invocation = _ipc_mod.Invocation
PassthroughRequest = _ipc_mod.PassthroughRequest
PassthroughResult = _ipc_mod.PassthroughResult
Response = _ipc_mod.Response
invoke_server = _ipc_mod.invoke_server
report_passthrough_result = _ipc_mod.report_passthrough_result

# Backwards compatibility alias retained for tests exercising shim helpers.
_validate_override_path = validate_override_path


def _path_separator() -> str:
    return ";" if IS_WINDOWS else os.pathsep


def _resolve_command_name() -> str:
    if from_env := os.environ.get(CMOX_SHIM_COMMAND_ENV):
        return from_env
    return Path(sys.argv[0]).name


def _validate_environment() -> float:
    """Validate required environment variables and return timeout."""
    if os.environ.get(CMOX_IPC_SOCKET_ENV) is None:
        print("IPC socket not specified", file=sys.stderr)
        sys.exit(1)

    timeout_raw = os.environ.get(CMOX_IPC_TIMEOUT_ENV, "5.0")
    try:
        timeout = float(timeout_raw)
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError  # noqa: TRY301 - unify error handling
    except ValueError:
        print(f"IPC error: invalid timeout: {timeout_raw!r}", file=sys.stderr)
        sys.exit(1)

    return timeout


def _create_invocation(cmd_name: str) -> Invocation:
    """Create an Invocation from command-line arguments and stdin."""
    stdin_data = "" if sys.stdin.isatty() else sys.stdin.read()
    env: dict[str, str] = dict(os.environ)  # shallow copy is sufficient (str -> str)
    return Invocation(
        command=cmd_name,
        args=sys.argv[1:],
        stdin=stdin_data,
        env=env,
        invocation_id=uuid.uuid4().hex,
    )


def _execute_invocation(invocation: Invocation, timeout: float) -> Response:
    """Execute invocation via IPC, handling passthrough if needed."""
    try:
        response = invoke_server(invocation, timeout=timeout)
    except (
        OSError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:  # pragma: no cover - network issues
        print(f"IPC error: {exc}", file=sys.stderr)
        sys.exit(1)

    if response.passthrough is not None:
        response = _handle_passthrough(invocation, response, timeout)
    return response


def _write_response(response: Response) -> None:
    """Write response to stdout/stderr and update environment if needed."""
    if response.env:
        os.environ |= response.env

    sys.stdout.write(response.stdout)
    sys.stderr.write(response.stderr)
    sys.exit(response.exit_code)


def main() -> None:
    """Connect to the IPC server and execute the command behaviour."""
    cmd_name = _resolve_command_name()
    timeout = _validate_environment()
    invocation = _create_invocation(cmd_name)
    response = _execute_invocation(invocation, timeout)
    _write_response(response)


def _handle_passthrough(
    invocation: Invocation, response: Response, timeout: float
) -> Response:
    """Execute the real command and report its outcome back to the server."""
    directive = response.passthrough
    if directive is None:  # pragma: no cover - defensive guard
        return response

    result_response = _run_real_command(invocation, directive)
    passthrough_result = PassthroughResult(
        invocation_id=directive.invocation_id,
        stdout=result_response.stdout,
        stderr=result_response.stderr,
        exit_code=result_response.exit_code,
    )
    return report_passthrough_result(passthrough_result, timeout=timeout)


def _shim_directory_from_env() -> Path | None:
    """Return the shim directory recorded in the IPC socket variable, if any."""
    socket_path = os.environ.get(CMOX_IPC_SOCKET_ENV)
    return Path(socket_path).parent if socket_path else None


def _normalise_path_entry(entry: str) -> str:
    """Return a platform-appropriate normalised path for comparisons."""
    module = ntpath if IS_WINDOWS else os.path
    normalised = module.normpath(entry)
    if IS_WINDOWS:
        normalised = module.normcase(normalised)
    return normalised


def _merge_passthrough_path(env_path: str | None, lookup_path: str) -> str:
    """Combine PATH values while filtering the shim directory and duplicates."""
    shim_dir = _shim_directory_from_env()
    return _build_search_path(env_path, lookup_path, shim_dir)


def _iter_path_entries(
    raw_path: str | None, shim_dir: Path | None
) -> t.Iterator[tuple[str, str]]:
    """Yield path entries paired with their normalized comparison keys."""
    if not raw_path:
        return

    shim_identity = _normalise_path_entry(os.fspath(shim_dir)) if shim_dir else None
    separator = _path_separator()
    for raw_entry in raw_path.split(separator):
        entry = raw_entry.strip()
        if not entry:
            continue
        identity = _normalise_path_entry(entry)
        if shim_identity and identity == shim_identity:
            continue
        yield entry, identity


def _add_unique_entries(
    entries: t.Iterable[tuple[str, str]],
    path_parts: list[str],
    seen: set[str],
) -> None:
    """Add unique entries to path_parts, tracking them in *seen*."""
    for entry, identity in entries:
        if identity in seen:
            continue
        path_parts.append(entry)
        seen.add(identity)


def _build_search_path(
    merged_path: str | None,
    lookup_path: str,
    shim_dir: Path | None,
) -> str:
    """Build a search PATH excluding the shim directory."""
    path_parts: list[str] = []
    seen: set[str] = set()

    _add_unique_entries(_iter_path_entries(merged_path, shim_dir), path_parts, seen)
    _add_unique_entries(_iter_path_entries(lookup_path, shim_dir), path_parts, seen)

    return _path_separator().join(path_parts)


def _resolve_passthrough_target(
    invocation: Invocation, directive: PassthroughRequest, env: dict[str, str]
) -> Path | Response:
    """Determine the executable path to use for passthrough execution."""
    search_path = env.get("PATH", directive.lookup_path)
    return resolve_command_with_override(
        invocation.command,
        search_path,
        override=os.environ.get(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{invocation.command}"),
    )


def _run_real_command(
    invocation: Invocation, directive: PassthroughRequest
) -> Response:
    """Resolve and execute the real command as instructed by *directive*."""
    env = prepare_environment(
        directive.lookup_path, directive.extra_env, invocation.env
    )
    env["PATH"] = _merge_passthrough_path(env.get("PATH"), directive.lookup_path)
    resolved = _resolve_passthrough_target(invocation, directive, env)

    if isinstance(resolved, Response):
        return resolved

    return execute_command(resolved, invocation, env, timeout=directive.timeout)


if __name__ == "__main__":  # pragma: no cover - manual entry
    main()
