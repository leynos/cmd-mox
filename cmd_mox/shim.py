#!/usr/bin/env python3
"""Generic command shim for CmdMox."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV
from cmd_mox.ipc import Invocation, invoke_server


def main() -> None:
    """Connect to the IPC server and execute the command behaviour."""
    cmd_name = Path(sys.argv[0]).name
    socket_path = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if socket_path is None:
        print("IPC socket not specified", file=sys.stderr)
        sys.exit(1)

    stdin_data = "" if sys.stdin.isatty() else sys.stdin.read()
    invocation = Invocation(
        command=cmd_name,
        args=sys.argv[1:],
        stdin=stdin_data,
        env=dict(os.environ),
    )

    try:
        timeout = float(os.environ.get(CMOX_IPC_TIMEOUT_ENV, "5.0"))
        response = invoke_server(invocation, timeout=timeout)
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - network issues
        print(f"IPC error: {exc}", file=sys.stderr)
        sys.exit(1)

    if response.env:
        os.environ |= response.env

    sys.stdout.write(response.stdout)
    sys.stderr.write(response.stderr)
    sys.exit(response.exit_code)


if __name__ == "__main__":  # pragma: no cover - manual entry
    main()
