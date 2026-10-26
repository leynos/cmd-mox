"""Bounded diagnostics and stream failures for the command shim."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import logging
import os
import sys
import time
import typing as typ

from cmd_mox import _path_utils
from cmd_mox._correlation import bounded_correlation_id
from cmd_mox.ipc import _observability

_SHIM_RESPONSE_STREAM_OPERATION = "ipc.shim.response_stream"

type _FailureOperation = typ.Literal[
    "ipc_request",
    "stdin_read",
    "passthrough_report",
    "response_write",
    "response_flush",
]
type _ShimEventOperation = typ.Literal[
    "ipc.shim.stdin_read",
    "ipc.shim.response_stream",
    "ipc.shim.passthrough_report",
    "ipc.shim.ipc_request",
]
type _ShimEventOutcome = typ.Literal[
    "read_failed", "write_failed", "flush_failed", "deadline_expired"
]
type _ResponseFailure = tuple[
    typ.Literal["response_write", "response_flush"], Exception
]


@dataclasses.dataclass(frozen=True, slots=True)
class _ShimFailureContext:
    """Capture bounded metadata shared by one shim failure event."""

    operation: _ShimEventOperation
    outcome: _ShimEventOutcome
    started: float
    correlation_id: str | None


_ERROR_DETAILS: dict[tuple[bool, _FailureOperation], str] = {
    (False, "ipc_request"): "IPC operation failed",
    (True, "ipc_request"): "IPC operation timed out",
    (False, "stdin_read"): "stdin read failed",
    (True, "stdin_read"): "timed out reading stdin",
    (False, "passthrough_report"): "passthrough reporting failed",
    (True, "passthrough_report"): "timed out reporting passthrough",
    (False, "response_write"): "response stream write failed",
    (True, "response_write"): "timed out writing response stream",
    (False, "response_flush"): "response stream flush failed",
    (True, "response_flush"): "timed out flushing response stream",
}
_ERROR_CATEGORIES: tuple[tuple[type[Exception], str], ...] = (
    (BrokenPipeError, "BrokenPipeError"),
    (TimeoutError, "TimeoutError"),
    (ConnectionError, "ConnectionError"),
    (UnicodeError, "UnicodeError"),
    (OSError, "OSError"),
    (json.JSONDecodeError, "JSONDecodeError"),
    (ValueError, "ValueError"),
    (RuntimeError, "RuntimeError"),
)


def _exit_ipc_error(
    exc: Exception,
    exit_code: int = 1,
    *,
    operation: _FailureOperation = "ipc_request",
) -> typ.NoReturn:
    """Print a bounded IPC diagnostic and terminate with a controlled status."""
    # A closed stderr cannot prevent the shim from returning a known status.
    with contextlib.suppress(OSError, ValueError):
        detail = _safe_error_detail(exc, operation)
        print(f"IPC error: {detail}", file=sys.stderr)
    sys.exit(exit_code)


def _safe_error_detail(exc: Exception, operation: _FailureOperation) -> str:
    """Return a fixed diagnostic that does not expose exception text.

    Returns
    -------
    str
        Safe message selected from the operation and timeout state.
    """
    return _ERROR_DETAILS[isinstance(exc, TimeoutError), operation]


def _bounded_error_category(exc: Exception) -> str:
    """Map caught failures to a closed set of log-safe categories.

    Returns
    -------
    str
        A known exception class name, or ``Exception`` for other failures.
    """
    return next(
        (
            category
            for exception_type, category in _ERROR_CATEGORIES
            if isinstance(exc, exception_type)
        ),
        "Exception",
    )


def _emit_shim_failure(failure: _ShimFailureContext, exc: Exception) -> None:
    """Emit bounded shim failure metadata without exception details."""
    transport: _observability.Transport = (
        "named_pipe" if _path_utils.IS_WINDOWS else "unix"
    )
    _observability.emit(
        _observability.IPCEvent(
            operation=failure.operation,
            transport=transport,
            outcome=failure.outcome,
            error_category=_bounded_error_category(exc),
            duration_ms=_observability.elapsed_ms(failure.started),
            correlation_id=bounded_correlation_id(failure.correlation_id),
        ),
        logger=logging.getLogger("cmd_mox.shim"),
    )


def _write_response_stream(
    stream_name: typ.Literal["stdout", "stderr"],
    text: str,
    *,
    correlation_id: str | None = None,
) -> _ResponseFailure | None:
    """Write one response stream and return its failure, if any."""  # ruff: ignore[docstring-missing-returns] - the annotation makes the optional failure explicit
    stream = getattr(sys, stream_name)
    started = time.perf_counter()

    def record_failure(
        outcome: typ.Literal["write_failed", "flush_failed"],
        failure_started: float,
        exc: Exception,
    ) -> _ResponseFailure:
        """Emit bounded failure metadata and replace the failed stream.

        Returns
        -------
        _ResponseFailure
            The failure operation and original exception.
        """
        failure_operation: typ.Literal["response_write", "response_flush"]
        if outcome == "write_failed":
            failure_operation = "response_write"
        else:
            failure_operation = "response_flush"
        _emit_shim_failure(
            _ShimFailureContext(
                _SHIM_RESPONSE_STREAM_OPERATION,
                outcome,
                failure_started,
                correlation_id,
            ),
            exc,
        )
        _replace_failed_stream_with_sink(stream_name, stream)
        return failure_operation, exc

    try:
        stream.write(text)
    except (OSError, ValueError) as exc:
        return record_failure("write_failed", started, exc)
    started = time.perf_counter()
    try:
        stream.flush()
    except (OSError, ValueError) as exc:
        return record_failure("flush_failed", started, exc)
    return None


def _replace_failed_stream_with_sink(
    stream_name: typ.Literal["stdout", "stderr"], failed_stream: object
) -> None:
    """Prevent interpreter shutdown from retrying a failed stream flush."""
    original_name = f"__{stream_name}__"
    original_stream = getattr(sys, original_name, None)
    sink = io.StringIO()
    if failed_stream is original_stream:
        descriptor = 1 if stream_name == "stdout" else 2
        _redirect_descriptor_to_null(descriptor)
        setattr(sys, original_name, sink)
    setattr(sys, stream_name, sink)


def _redirect_descriptor_to_null(descriptor: int) -> None:
    """Best-effort redirect of one descriptor to the null device."""
    with contextlib.suppress(OSError):
        sink_descriptor = os.open(os.devnull, os.O_WRONLY)
        if sink_descriptor != descriptor:
            try:
                os.dup2(sink_descriptor, descriptor)
            finally:
                os.close(sink_descriptor)
