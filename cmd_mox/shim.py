#!/usr/bin/env python3
"""Generic command shim for CmdMox."""

from __future__ import annotations

import json
import math
import os
import sys
import uuid
from pathlib import Path

from cmd_mox.command_runner import (
    execute_command,
    prepare_environment,
    resolve_command_path,
)
from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    CMOX_REAL_COMMAND_ENV_PREFIX,
)
from cmd_mox.ipc import (
    Invocation,
    PassthroughRequest,
    PassthroughResult,
    Response,
    invoke_server,
    report_passthrough_result,
)


def main() -> None:
    """Connect to the IPC server and execute the command behaviour."""
    cmd_name = Path(sys.argv[0]).name
    socket_path = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if socket_path is None:
        print("IPC socket not specified", file=sys.stderr)
        sys.exit(1)

    stdin_data = "" if sys.stdin.isatty() else sys.stdin.read()
    env: dict[str, str] = dict(os.environ)  # shallow copy is sufficient (str -> str)
    invocation = Invocation(
        command=cmd_name,
        args=sys.argv[1:],
        stdin=stdin_data,
        env=env,
        invocation_id=uuid.uuid4().hex,
    )

    timeout_raw = os.environ.get(CMOX_IPC_TIMEOUT_ENV, "5.0")
    try:
        timeout = float(timeout_raw)
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError  # noqa: TRY301 - unify error handling
    except ValueError:
        print(f"IPC error: invalid timeout: {timeout_raw!r}", file=sys.stderr)
        sys.exit(1)
    try:
        response = invoke_server(invocation, timeout=timeout)
        if response.passthrough is not None:
            response = _handle_passthrough(invocation, response, timeout)
    except (
        OSError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:  # pragma: no cover - network issues
        print(f"IPC error: {exc}", file=sys.stderr)
        sys.exit(1)

    if response.env:
        os.environ |= response.env

    sys.stdout.write(response.stdout)
    sys.stderr.write(response.stderr)
    sys.exit(response.exit_code)


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


def _run_real_command(
    invocation: Invocation, directive: PassthroughRequest
) -> Response:
    """Resolve and execute the real command as instructed by *directive*."""
    override = os.environ.get(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{invocation.command}")
    if override:
        resolved = Path(override)
        if not resolved.is_absolute():
            resolved = resolved.resolve()
        if not resolved.exists():
            return Response(stderr=f"{invocation.command}: not found", exit_code=127)
        if not resolved.is_file():
            return Response(
                stderr=f"{invocation.command}: invalid executable path", exit_code=126
            )
        if not os.access(resolved, os.X_OK):
            return Response(
                stderr=f"{invocation.command}: not executable", exit_code=126
            )
    else:
        resolved = resolve_command_path(invocation.command, directive.lookup_path)
        if isinstance(resolved, Response):
            return resolved

    env = prepare_environment(
        directive.lookup_path, directive.extra_env, invocation.env
    )
    return execute_command(resolved, invocation, env, timeout=directive.timeout)


if __name__ == "__main__":  # pragma: no cover - manual entry
    main()
