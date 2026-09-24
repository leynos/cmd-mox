#!/usr/bin/env python3
"""Generic command shim for CmdMox."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import os
import sys
import time
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    # Ensure the package is importable when executed via symlinks from a
    # temporary shim directory or when the invoking interpreter lacks the
    # project on sys.path (e.g., system python outside the venv).
    sys.path.insert(0, str(_PACKAGE_ROOT))


def _load_bootstrap_from_file() -> cabc.Callable[[], None]:
    """Load bootstrap helper without importing the package ``__init__``.

    Returns
    -------
    collections.abc.Callable[[], None]
        Bootstrap function that prepares the shim import path.

    Raises
    ------
    RuntimeError
        If the bootstrap module cannot be loaded.
    """
    bootstrap_path = Path(__file__).resolve().with_name("_shim_bootstrap.py")
    spec = importlib.util.spec_from_file_location(
        "cmd_mox._shim_bootstrap", bootstrap_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module.bootstrap_shim_path


if __name__ == "__main__":
    bootstrap_shim_path = _load_bootstrap_from_file()
    # Intentionally bootstrap twice: once here for script execution before other
    # imports, and again inside main(). bootstrap_shim_path is idempotent by
    # contract, so the duplicate call keeps early-start behaviour without
    # risking double mutation.
    bootstrap_shim_path()
else:
    from cmd_mox._shim_bootstrap import bootstrap_shim_path

from cmd_mox import _path_utils as path_utils, _shim_stdin  # ruff: ignore[module-import-not-at-top-of-file, unsorted-imports] - shim bootstrap configures sys.path before package imports
from cmd_mox.command_runner import (  # ruff: ignore[module-import-not-at-top-of-file] - shim bootstrap configures sys.path before package imports
    execute_command,
    prepare_environment,
    resolve_command_with_override,
)
from cmd_mox.environment import (  # ruff: ignore[module-import-not-at-top-of-file] - shim bootstrap configures sys.path before package imports
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    CMOX_REAL_COMMAND_ENV_PREFIX,
)
from cmd_mox.ipc import (  # ruff: ignore[module-import-not-at-top-of-file] - shim bootstrap configures sys.path before package imports
    Invocation,
    PassthroughRequest,
    PassthroughResult,
    Response,
    invoke_server,
    report_passthrough_result,
)
from cmd_mox.ipc._deadline import _remaining_time  # ruff: ignore[module-import-not-at-top-of-file] - shim bootstrap configures sys.path before package imports

CMOX_SHIM_COMMAND_ENV = "CMOX_SHIM_COMMAND"


def _normalize_windows_arg(arg: str) -> str:
    """Collapse doubled Windows batch carets in a command argument.

    Parameters
    ----------
    arg : str
        Argument as received after batch-file processing.

    Returns
    -------
    str
        Argument with redundant caret escaping removed on Windows.
    """
    if not path_utils.IS_WINDOWS or "^^" not in arg:
        return arg

    # Batch processing may introduce multiple layers of caret doubling. Reduce
    # until no escape pairs remain so downstream code sees the intended text.
    while "^^" in arg:
        arg = arg.replace("^^", "^")
    return arg


def _resolve_command_name() -> str:
    """Return the shim command name from the environment or executable path.

    Returns
    -------
    str
        Command name used to identify the invocation.
    """
    if from_env := os.environ.get(CMOX_SHIM_COMMAND_ENV):
        return from_env
    return Path(sys.argv[0]).name


def _parse_positive_finite(raw: str) -> float | None:
    """Parse *raw* as a strictly positive, finite float.

    Returns
    -------
    float or None
        The parsed value, or ``None`` when *raw* is unparseable, non-positive,
        or not finite.
    """
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 and math.isfinite(value) else None


def _validate_environment() -> float:
    """Validate required environment variables and return the timeout."""  # ruff: ignore[docstring-missing-returns] - private validator has an obvious timeout return
    if os.environ.get(CMOX_IPC_SOCKET_ENV) is None:
        print("IPC socket not specified", file=sys.stderr)
        sys.exit(1)

    timeout_raw = os.environ.get(CMOX_IPC_TIMEOUT_ENV, "5.0")
    timeout = _parse_positive_finite(timeout_raw)
    if timeout is None:
        print(f"IPC error: invalid timeout: {timeout_raw!r}", file=sys.stderr)
        sys.exit(1)

    return timeout


def _exit_ipc_error(exc: Exception, exit_code: int = 1) -> typ.NoReturn:
    """Print an IPC diagnostic and terminate with a controlled status."""
    # A closed stderr cannot prevent the shim from returning a known status.
    with contextlib.suppress(OSError, ValueError):
        print(f"IPC error: {exc}", file=sys.stderr)
    sys.exit(exit_code)


def _create_invocation(
    cmd_name: str, timeout: float, *, deadline: float | None = None
) -> Invocation:
    """Create an invocation from command-line arguments and stdin.

    Parameters
    ----------
    cmd_name : str
        Command name associated with the shim process.
    timeout : float
        Maximum time available to read non-interactive standard input.
    deadline : float or None, optional
        Shared monotonic deadline for stdin and subsequent IPC on POSIX.

    Returns
    -------
    Invocation
        Invocation containing arguments, stdin, and a copied environment.
    """
    import uuid

    stdin_data = (
        ""
        if sys.stdin.isatty()
        else _shim_stdin._read_stdin_until_eof(
            timeout, deadline=deadline, on_error=_exit_ipc_error
        )
    )
    env: dict[str, str] = dict(os.environ)  # shallow copy is sufficient (str -> str)
    argv = sys.argv[1:]
    if path_utils.IS_WINDOWS:
        argv = [_normalize_windows_arg(arg) for arg in argv]
    return Invocation(
        command=cmd_name,
        args=argv,
        stdin=stdin_data,
        env=env,
        invocation_id=uuid.uuid4().hex,
    )


def _timeout_remaining(timeout: float, deadline: float | None) -> float:
    """Return the remaining shared budget, or the original timeout.

    Returns
    -------
    float
        Remaining time before *deadline*, or *timeout* when no deadline exists.

    Raises
    ------
    TimeoutError
        If the shared deadline has expired.
    """
    if deadline is None:
        return timeout
    try:
        return _remaining_time(deadline)
    except TimeoutError:
        msg = "IPC operation timed out"
        raise TimeoutError(msg) from None


def _execute_invocation(
    invocation: Invocation, timeout: float, *, deadline: float | None = None
) -> Response:
    """Execute an invocation via IPC, handling passthrough if needed.

    Parameters
    ----------
    invocation : Invocation
        Invocation to send to the IPC server.
    timeout : float
        IPC timeout in seconds.
    deadline : float or None, optional
        Shared monotonic deadline for the request and any passthrough report.

    Returns
    -------
    Response
        Response returned by the server or passthrough command.
    """
    try:
        response = invoke_server(
            invocation,
            timeout=_timeout_remaining(timeout, deadline),
            deadline=deadline,
        )
    except (
        OSError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:  # pragma: no cover - network issues
        _exit_ipc_error(exc)

    if response.passthrough is not None:
        response = _handle_passthrough(invocation, response, timeout, deadline=deadline)
    return response


def _write_response(response: Response) -> None:
    """Write response to stdout/stderr and update environment if needed."""
    if response.env:
        os.environ |= response.env

    write_failures = [
        failure
        for stream_name, text in (
            ("stdout", response.stdout),
            ("stderr", response.stderr),
        )
        if (failure := _write_response_stream(stream_name, text)) is not None
    ]
    if write_failures:
        _exit_ipc_error(write_failures[0], exit_code=response.exit_code or 1)
    sys.exit(response.exit_code)


def _write_response_stream(
    stream_name: typ.Literal["stdout", "stderr"], text: str
) -> Exception | None:
    """Write one response stream and return its failure, if any."""  # ruff: ignore[docstring-missing-returns] - the annotation makes the optional failure explicit
    stream = getattr(sys, stream_name)
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError) as exc:
        _replace_failed_stream_with_sink(stream_name, stream)
        return exc
    return None


def _replace_failed_stream_with_sink(
    stream_name: typ.Literal["stdout", "stderr"], failed_stream: object
) -> None:
    """Prevent interpreter shutdown from retrying a failed stream flush."""
    original_name = f"__{stream_name}__"
    original_stream = getattr(sys, original_name, None)
    if failed_stream is original_stream:
        descriptor = 1 if stream_name == "stdout" else 2
        try:
            sink_descriptor = os.open(os.devnull, os.O_WRONLY)
            if sink_descriptor != descriptor:
                try:
                    os.dup2(sink_descriptor, descriptor)
                finally:
                    os.close(sink_descriptor)
        except OSError:
            pass

        sink = io.StringIO()
        setattr(sys, original_name, sink)
    else:
        sink = io.StringIO()
    setattr(sys, stream_name, sink)


def main() -> None:
    """Connect to the IPC server and execute the command behaviour."""
    bootstrap_shim_path()
    cmd_name = _resolve_command_name()
    timeout = _validate_environment()
    deadline = None if path_utils.IS_WINDOWS else time.monotonic() + timeout
    invocation = _create_invocation(cmd_name, timeout, deadline=deadline)
    response = _execute_invocation(invocation, timeout, deadline=deadline)
    _write_response(response)


def _handle_passthrough(
    invocation: Invocation,
    response: Response,
    timeout: float,
    *,
    deadline: float | None = None,
) -> Response:
    """Execute the real command and report its outcome to the server.

    Parameters
    ----------
    invocation : Invocation
        Original invocation to execute.
    response : Response
        Server response containing the passthrough directive.
    timeout : float
        IPC timeout in seconds.
    deadline : float or None, optional
        Shared monotonic deadline for the passthrough report.

    Returns
    -------
    Response
        Response returned after the real command's result is reported.
    """
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
    try:
        return report_passthrough_result(
            passthrough_result,
            timeout=_timeout_remaining(timeout, deadline),
            deadline=deadline,
        )
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        _exit_ipc_error(exc, exit_code=result_response.exit_code or 1)


def _shim_directory_from_env() -> Path | None:
    """Return the shim directory recorded in the IPC socket variable, if any.

    Returns
    -------
    pathlib.Path or None
        Parent directory of the configured socket, or ``None`` when unset.
    """
    socket_path = os.environ.get(CMOX_IPC_SOCKET_ENV)
    return Path(socket_path).parent if socket_path else None


def _merge_passthrough_path(env_path: str | None, lookup_path: str) -> str:
    """Combine PATH values while filtering the shim directory and duplicates.

    Parameters
    ----------
    env_path : str or None
        PATH value returned by the server environment preparation.
    lookup_path : str
        Fallback search path supplied by the passthrough directive.

    Returns
    -------
    str
        De-duplicated search path excluding the shim directory.
    """
    shim_dir = _shim_directory_from_env()
    return _build_search_path(env_path, lookup_path, shim_dir)


def _build_search_path(
    merged_path: str | None,
    lookup_path: str,
    shim_dir: Path | None,
) -> str:
    """Build a search PATH excluding the shim directory.

    Parameters
    ----------
    merged_path : str or None
        Existing PATH entries to prepend.
    lookup_path : str
        Additional lookup entries.
    shim_dir : pathlib.Path or None
        Directory to exclude from the resulting search path.

    Returns
    -------
    str
        Ordered, de-duplicated path entries separated for the host platform.
    """
    shim_identity = (
        path_utils.normalize_path_string(os.fspath(shim_dir))
        if shim_dir is not None
        else None
    )
    raw_entries: list[str] = []
    if merged_path:
        raw_entries.extend(merged_path.split(os.pathsep))
    raw_entries.extend(lookup_path.split(os.pathsep))

    path_parts: list[str] = []
    seen: set[str] = set()

    for raw_entry in raw_entries:
        entry = raw_entry.strip()
        if not entry:
            continue
        identity = path_utils.normalize_path_string(entry)
        if shim_identity and identity == shim_identity:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        path_parts.append(entry)

    return os.pathsep.join(path_parts)


def _resolve_passthrough_target(
    invocation: Invocation, directive: PassthroughRequest, env: dict[str, str]
) -> Path | Response:
    """Determine the executable path to use for passthrough execution.

    Parameters
    ----------
    invocation : Invocation
        Invocation whose command should be resolved.
    directive : PassthroughRequest
        Lookup path and passthrough settings from the server.
    env : dict[str, str]
        Environment used for command lookup.

    Returns
    -------
    pathlib.Path or Response
        Resolved executable path, or an error response when resolution fails.
    """
    search_path = env.get("PATH", directive.lookup_path)
    return resolve_command_with_override(
        invocation.command,
        search_path,
        override=os.environ.get(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{invocation.command}"),
    )


def _run_real_command(
    invocation: Invocation, directive: PassthroughRequest
) -> Response:
    """Resolve and execute the real command as instructed by *directive*.

    Parameters
    ----------
    invocation : Invocation
        Invocation to pass to the real command.
    directive : PassthroughRequest
        Lookup path, environment, and timeout settings.

    Returns
    -------
    Response
        Real command result or a response describing resolution failure.
    """
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
