"""Windows named pipe client helpers for talking to the IPC server."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import os
import time
import typing as t
from pathlib import Path

try:
    import win32file
    import win32pipe
    import pywintypes
    import winerror
    import win32con
    WINDOWS_AVAILABLE = True
except ImportError:
    WINDOWS_AVAILABLE = False

from cmd_mox._validators import (
    validate_positive_finite_timeout,
    validate_retry_attempts,
    validate_retry_backoff,
    validate_retry_jitter,
)
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV

from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import parse_json_safely
from .models import Invocation, PassthroughResult, Response

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_RETRIES: t.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: t.Final[float] = 0.05
DEFAULT_CONNECT_JITTER: t.Final[float] = 0.2
MIN_RETRY_SLEEP: t.Final[float] = 0.001

CONNECTION_TIMEOUT_MS: t.Final[int] = 5000  # 5 seconds in milliseconds


@dc.dataclass(slots=True)
class WindowsRetryConfig:
    """Configuration for Windows named pipe connection retry behavior."""

    retries: int = DEFAULT_CONNECT_RETRIES
    backoff: float = DEFAULT_CONNECT_BACKOFF
    jitter: float = DEFAULT_CONNECT_JITTER

    def __post_init__(self) -> None:
        """Validate retry configuration values."""
        validate_retry_attempts(self.retries)
        validate_retry_backoff(self.backoff)
        validate_retry_jitter(self.jitter)

    def validate(self, timeout: float) -> None:
        """Re-validate retry configuration alongside the connection timeout."""
        validate_positive_finite_timeout(timeout)
        self.__post_init__()


def calculate_retry_delay(attempt: int, backoff: float, jitter: float) -> float:
    """Return the sleep delay for a 0-based *attempt*; never shorter than
    :data:`MIN_RETRY_SLEEP`.
    """
    delay = backoff * (attempt + 1)
    if jitter:
        import random
        # Randomise the linear backoff within the jitter bounds to avoid
        # thundering herds if many clients retry simultaneously.
        factor = random.uniform(1.0 - jitter, 1.0 + jitter)  # noqa: S311
        delay *= factor
    return max(delay, MIN_RETRY_SLEEP)


def _connect_to_named_pipe(
    pipe_name: str,
    timeout_ms: int = CONNECTION_TIMEOUT_MS,
) -> int:
    """Connect to a Windows named pipe."""
    if not pipe_name.startswith(r'\\.\pipe\\'):
        pipe_name = r'\\.\pipe\\' + pipe_name
    
    try:
        pipe_handle = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,  # No sharing
            None,  # Default security
            win32con.OPEN_EXISTING,
            0,  # Normal attributes
            None
        )
        
        if pipe_handle == win32file.INVALID_HANDLE_VALUE:
            import win32api  # noqa: PLC0415
            error = win32api.GetLastError()
            if error == winerror.ERROR_PIPE_BUSY:
                raise RuntimeError("Named pipe is busy")
            elif error == winerror.ERROR_FILE_NOT_FOUND:
                raise RuntimeError(f"Named pipe not found: {pipe_name}")
            else:
                raise RuntimeError(f"Failed to connect to named pipe: {error}")
        
        # Set pipe mode to message read mode
        # Note: pywin32 handles PyHANDLE automatically for SetNamedPipeHandleState
        try:
            win32pipe.SetNamedPipeHandleState(
                pipe_handle,
                win32pipe.PIPE_READMODE_MESSAGE,
                None,
                None
            )
        except AttributeError:
            # Fallback if SetNamedPipeHandleState is not available
            pass
        
        return pipe_handle.handle if hasattr(pipe_handle, 'handle') else int(pipe_handle)
        
    except pywintypes.error as e:
        raise RuntimeError(f"Failed to connect to named pipe {pipe_name}: {e}") from e


def _get_validated_pipe_name() -> str:
    """Fetch the IPC pipe name from the environment."""
    pipe_name = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if pipe_name is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)
    # Convert Unix-style pipe paths to Windows named pipe format
    if '/' in pipe_name or '\\\\' not in pipe_name:
        # Convert filepath style to named pipe style
        return pipe_name.replace('/', '\\').replace('\\\\', '\\')
    return pipe_name


def _read_all_from_pipe(pipe_handle: int, timeout: float) -> bytes:
    """Read all data from named pipe until EOF."""
    chunks = []
    deadline = time.monotonic() + timeout if timeout > 0 else None
    
    while True:
        try:
            result, data = win32file.ReadFile(pipe_handle, 65536)
            if result == winerror.ERROR_MORE_DATA:
                chunks.append(data)
                continue
            elif result != 0:
                if result == winerror.ERROR_BROKEN_PIPE:
                    break  # Normal end
                else:
                    raise RuntimeError(f"Failed to read from pipe: {result}")
            
            if data:
                chunks.append(data)
            else:
                break  # EOF
                
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                break
            raise
        
        # Check timeout
        if deadline and time.monotonic() > deadline:
            raise TimeoutError("Read timeout exceeded")
    
    return b''.join(chunks)


def _send_request_with_retries(
    kind: str,
    data: dict[str, t.Any],
    timeout: float,
    retry_config: WindowsRetryConfig,
) -> Response:
    """Send a JSON request of *kind* to the Windows IPC server with retries."""
    retry_config.validate(timeout)
    pipe_name = _get_validated_pipe_name()
    payload = dict(data)
    payload["kind"] = kind
    payload_bytes = json.dumps(payload).encode("utf-8")
    
    for attempt in range(retry_config.retries):
        pipe_handle = None
        try:
            # Wait for pipe to become available if busy
            if attempt > 0 and win32pipe.WaitNamedPipe(pipe_name, int(timeout * 1000)):
                delay = calculate_retry_delay(
                    attempt - 1, retry_config.backoff, retry_config.jitter
                )
                import time
                time.sleep(delay)
            
            # Connect to the pipe
            pipe_handle = _connect_to_named_pipe(pipe_name, int(timeout * 1000))
            
            # Send request
            win32file.WriteFile(pipe_handle, payload_bytes)
            
                    # Read response
            raw_response = _read_all_from_pipe(pipe_handle, timeout)
            
            # Close the handle after reading
            import contextlib
            with contextlib.suppress(pywintypes.error):
                win32file.CloseHandle(pipe_handle)
            
            parsed = parse_json_safely(raw_response)
            if parsed is None:
                msg = "Invalid JSON from IPC server"
                raise RuntimeError(msg)
            
            return Response.from_payload(parsed)
            
        except (RuntimeError, pywintypes.error) as exc:
            logger.debug(
                "Windows IPC attempt %d/%d to %s failed: %s",
                attempt + 1,
                retry_config.retries,
                pipe_name,
                exc,
            )
            
            if attempt < retry_config.retries - 1:
                continue
            else:
                raise
                
        finally:
            if pipe_handle is not None:
                try:
                    win32file.CloseHandle(pipe_handle)
                except pywintypes.error:
                    pass
    
    msg = (
        "Unreachable code reached in Windows named pipe connection loop: all retry "
        "attempts exhausted and no connection returned. This indicates a logic "
        "error or unexpected control flow."
    )
    raise RuntimeError(msg)  # pragma: no cover


def invoke_windows_server(
    invocation: Invocation,
    timeout: float,
    retry_config: WindowsRetryConfig | None = None,
) -> Response:
    """Send *invocation* to the Windows IPC server and return its response."""
    retry = retry_config or WindowsRetryConfig()
    return _send_request_with_retries(KIND_INVOCATION, invocation.to_dict(), timeout, retry)


def report_windows_passthrough_result(
    result: PassthroughResult,
    timeout: float,
    retry_config: WindowsRetryConfig | None = None,
) -> Response:
    """Send passthrough execution results back to the Windows IPC server."""
    return _send_request_with_retries(
        KIND_PASSTHROUGH_RESULT,
        result.to_dict(),
        timeout,
        retry_config or WindowsRetryConfig(),
    )


def is_windows_ipc_available() -> bool:
    """Return True if Windows IPC components are available."""
    return WINDOWS_AVAILABLE and win32pipe is not None and win32file is not None