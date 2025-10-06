"""JSON IPC server and client helpers for CmdMox."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import math
import os
import random
import re
import socket
import socketserver
import threading
import time
import typing as t
from pathlib import Path

from ._validators import validate_positive_finite_timeout
from .environment import CMOX_IPC_SOCKET_ENV, EnvironmentManager
from .expectations import SENSITIVE_ENV_KEY_TOKENS

# Pre-normalize tokens once for case-insensitive checks
_SENSITIVE_TOKENS: tuple[str, ...] = tuple(
    tok.casefold() for tok in SENSITIVE_ENV_KEY_TOKENS
)

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

logger = logging.getLogger(__name__)

SENSITIVE_ENV_KEY_PATTERN: t.Final[re.Pattern[str]] = re.compile(
    r"(?:^|_|\b)(?:key|token|secret|password)(?:_|$|\b)", re.IGNORECASE
)

DEFAULT_CONNECT_RETRIES: t.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: t.Final[float] = 0.05
DEFAULT_CONNECT_JITTER: t.Final[float] = 0.2
MIN_RETRY_SLEEP: t.Final[float] = 0.001
_REPR_FIELD_LIMIT: t.Final[int] = 256
_SECRET_ENV_KEY_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?i)(^|[_-])(KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?|PASS(?:WORD)?|PWD)(?=[_-]|\d|$)"
)


def _shorten(text: str, limit: int = _REPR_FIELD_LIMIT) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


@dc.dataclass(slots=True)
class RetryConfig:
    """Configuration for connection retry behavior."""

    retries: int = DEFAULT_CONNECT_RETRIES
    backoff: float = DEFAULT_CONNECT_BACKOFF
    jitter: float = DEFAULT_CONNECT_JITTER

    def __post_init__(self) -> None:
        """Validate retry configuration values."""
        # Validate only retry-related parameters here to avoid coupling to timeout.
        _validate_retries(self.retries)
        _validate_backoff(self.backoff)
        _validate_jitter(self.jitter)


@dc.dataclass(slots=True)
class Invocation:
    """Information reported by a shim to the IPC server."""

    command: str
    args: list[str]
    stdin: str
    env: dict[str, str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    invocation_id: str | None = None

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serializable mapping of this invocation."""
        payload: dict[str, t.Any] = {
            "command": self.command,
            "args": list(self.args),
            "stdin": self.stdin,
            "env": dict(self.env),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }
        if self.invocation_id is not None:
            payload["invocation_id"] = self.invocation_id
        return payload

    def apply(self, resp: Response) -> None:
        """Copy stdout/stderr/exit_code from resp (env is not copied)."""
        self.stdout, self.stderr, self.exit_code = (
            resp.stdout,
            resp.stderr,
            resp.exit_code,
        )

    def __repr__(self) -> str:
        """Return a convenient debug representation."""
        # Keep explicit dict construction and field shortening for readability.
        # Redact env keys using both normalized token checks and a regex that
        # matches word boundaries and common separators. This errs on the side
        # of safety and keeps behavior compatible with main's improvements.
        safe_env: dict[str, str] = {}
        for key, value in self.env.items():
            key_cf = key.casefold()
            should_redact = any(tok in key_cf for tok in _SENSITIVE_TOKENS) or (
                _SECRET_ENV_KEY_RE.search(key) is not None
            )
            safe_env[key] = "<redacted>" if should_redact else value

        data = {
            "command": self.command,
            "args": list(self.args),
            "stdin": _shorten(self.stdin, _REPR_FIELD_LIMIT),
            "stdout": _shorten(self.stdout, _REPR_FIELD_LIMIT),
            "stderr": _shorten(self.stderr, _REPR_FIELD_LIMIT),
            "exit_code": self.exit_code,
            "env": safe_env,
        }
        return f"Invocation({data!r})"


@dc.dataclass(slots=True)
class PassthroughRequest:
    """Instruction for a shim to execute the real command."""

    invocation_id: str
    lookup_path: str
    extra_env: dict[str, str] = dc.field(default_factory=dict)
    timeout: float = 30.0

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serialisable mapping of this request."""
        return {
            "invocation_id": self.invocation_id,
            "lookup_path": self.lookup_path,
            "extra_env": dict(self.extra_env),
            "timeout": self.timeout,
        }


@dc.dataclass(slots=True)
class PassthroughResult:
    """Result payload returned by a shim after a passthrough execution."""

    invocation_id: str
    stdout: str
    stderr: str
    exit_code: int

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serialisable mapping of this result."""
        return {
            "invocation_id": self.invocation_id,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }


@dc.dataclass(slots=True)
class Response:
    """Response from the IPC server back to a shim."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    env: dict[str, str] = dc.field(default_factory=dict)
    passthrough: PassthroughRequest | None = None

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serializable mapping of this response."""
        data: dict[str, t.Any] = {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "env": dict(self.env),
        }
        if self.passthrough is not None:
            data["passthrough"] = self.passthrough.to_dict()
        return data

    @classmethod
    def from_payload(cls, payload: dict[str, t.Any]) -> Response:
        """Construct a :class:`Response` from a JSON payload."""
        passthrough_payload = payload.get("passthrough")
        passthrough: PassthroughRequest | None = None
        if isinstance(passthrough_payload, dict):
            passthrough = _build_passthrough_request(passthrough_payload)
        payload = payload.copy()
        payload.pop("passthrough", None)
        env = payload.get("env")
        if not isinstance(env, dict):
            payload["env"] = {}
        try:
            response = cls(**payload)
        except TypeError as exc:
            msg = "Invalid response payload from IPC server"
            raise RuntimeError(msg) from exc
        response.passthrough = passthrough
        return response


def _read_all(sock: socket.socket) -> bytes:
    """Read all data from *sock* until EOF."""
    chunks = []
    while chunk := sock.recv(1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_json_safely(data: bytes) -> dict[str, t.Any] | None:
    """Return a JSON object parsed from *data* or ``None`` on failure."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return t.cast("dict[str, t.Any]", payload)


def _validate_invocation_payload(payload: dict[str, t.Any]) -> Invocation | None:
    """Return an :class:`Invocation` if *payload* has the required fields."""
    try:
        return Invocation(**payload)  # type: ignore[arg-type]
    except TypeError:
        logger.exception("IPC payload missing required fields: %r", payload)
        return None


def _validate_passthrough_payload(
    payload: dict[str, t.Any],
) -> PassthroughResult | None:
    """Return a :class:`PassthroughResult` for passthrough result payloads."""
    try:
        return PassthroughResult(**payload)  # type: ignore[arg-type]
    except TypeError:
        logger.exception("IPC passthrough payload missing required fields: %r", payload)
        return None


def _build_passthrough_request(payload: dict[str, t.Any]) -> PassthroughRequest | None:
    """Convert *payload* into a :class:`PassthroughRequest` when possible."""
    try:
        invocation_id = str(payload["invocation_id"])
        lookup_path = str(payload["lookup_path"])
    except KeyError:
        logger.exception("Passthrough directive missing required fields: %r", payload)
        return None

    extra_env_raw = payload.get("extra_env", {})
    extra_env: dict[str, str] = {}
    if isinstance(extra_env_raw, dict):
        extra_env = {str(key): str(value) for key, value in extra_env_raw.items()}

    timeout_raw = payload.get("timeout", 30.0)
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        logger.debug("Invalid passthrough timeout %r; using default", timeout_raw)
        timeout = 30.0

    return PassthroughRequest(
        invocation_id=invocation_id,
        lookup_path=lookup_path,
        extra_env=extra_env,
        timeout=timeout,
    )


_RequestValidator = t.Callable[[dict[str, t.Any]], t.Any | None]
# First argument is the active IPCServer instance; typed as Any to avoid
# forward reference issues when the class is defined later in the module.
_RequestProcessor = t.Callable[[t.Any, t.Any], Response]
_DispatchArg = t.TypeVar("_DispatchArg", Invocation, PassthroughResult)


def _process_invocation(server: IPCServer, invocation: Invocation) -> Response:
    """Invoke :meth:`IPCServer.handle_invocation` for *invocation*."""
    return server.handle_invocation(invocation)


def _process_passthrough_result(
    server: IPCServer, result: PassthroughResult
) -> Response:
    """Invoke :meth:`IPCServer.handle_passthrough_result` for *result*."""
    return server.handle_passthrough_result(result)


_REQUEST_HANDLERS: dict[str, tuple[_RequestValidator, _RequestProcessor]] = {
    "invocation": (_validate_invocation_payload, _process_invocation),
    "passthrough-result": (
        _validate_passthrough_payload,
        _process_passthrough_result,
    ),
}


def _get_validated_socket_path() -> Path:
    """Fetch the IPC socket path from the environment."""
    sock = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if sock is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)
    return Path(sock)


def _create_unix_socket(timeout: float) -> socket.socket:
    """Create a Unix stream socket with *timeout* applied."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    return sock


def _attempt_connection(sock: socket.socket, address: str) -> None:
    """Connect *sock* to *address* or raise on failure."""
    sock.connect(address)


def _validate_retries(retries: int) -> None:
    """Validate retry attempt count."""
    if retries < 1:
        msg = "retries must be >= 1"
        raise ValueError(msg)


def _validate_connection_timeout(timeout: float) -> None:
    """Validate overall timeout value."""
    validate_positive_finite_timeout(timeout)


def _validate_backoff(backoff: float) -> None:
    """Validate linear backoff value."""
    if not (backoff >= 0 and math.isfinite(backoff)):
        msg = "backoff must be >= 0 and finite"
        raise ValueError(msg)


def _validate_jitter(jitter: float) -> None:
    """Validate jitter fraction."""
    if not (0.0 <= jitter <= 1.0 and math.isfinite(jitter)):
        msg = "jitter must be between 0 and 1 and finite"
        raise ValueError(msg)


def _validate_connection_params(timeout: float, retry_config: RetryConfig) -> None:
    """Ensure connection retry parameters are sensible."""
    _validate_retries(retry_config.retries)
    _validate_connection_timeout(timeout)
    _validate_backoff(retry_config.backoff)
    _validate_jitter(retry_config.jitter)


def calculate_retry_delay(attempt: int, backoff: float, jitter: float) -> float:
    """Return the sleep delay for a 0-based *attempt*; never shorter than
    MIN_RETRY_SLEEP.
    """
    delay = backoff * (attempt + 1)
    if jitter:
        # Randomise the linear backoff within the jitter bounds to avoid
        # thundering herds if many clients retry simultaneously.
        factor = random.uniform(1.0 - jitter, 1.0 + jitter)  # noqa: S311
        delay *= factor
    # Prevent a zero-length sleep which can spin on some platforms.
    return max(delay, MIN_RETRY_SLEEP)


def _connect_with_retries(
    sock_path: Path,
    timeout: float,
    retry_config: RetryConfig,
) -> socket.socket:
    """Connect to *sock_path* retrying on :class:`OSError`.

    Opens an ``AF_UNIX`` socket and performs ``retry_config.retries`` connection
    attempts. ``retry_config.retries`` is the total number of attempts and must
    be at least one. The delay between attempts scales linearly with
    ``retry_config.backoff`` and the attempt number. ``timeout`` must be
    positive, ``retry_config.backoff`` non-negative, and ``retry_config.jitter``
    within ``[0, 1]``. The last ``OSError`` is raised if every attempt fails.
    """
    _validate_connection_params(timeout, retry_config)
    address = str(sock_path)
    for attempt in range(retry_config.retries):
        sock = _create_unix_socket(timeout)
        try:
            _attempt_connection(sock, address)
        except OSError as exc:
            sock.close()
            logger.debug(
                "IPC connect attempt %d/%d to %s failed: %s",
                attempt + 1,
                retry_config.retries,
                address,
                exc,
            )
            if attempt < retry_config.retries - 1:
                delay = calculate_retry_delay(
                    attempt, retry_config.backoff, retry_config.jitter
                )
                time.sleep(delay)
                continue
            raise
        else:
            return sock
    # Loop always returns or raises; this is defensive.
    msg = (
        "Unreachable code reached in socket connection loop: all retry "
        "attempts exhausted and no socket returned. This indicates a logic "
        "error or unexpected control flow."
    )
    raise RuntimeError(msg)  # pragma: no cover


def _cleanup_stale_socket(socket_path: Path) -> None:
    """Remove a pre-existing socket file if no server is listening."""
    if not socket_path.exists():
        return
    try:
        # Ensure the probe socket is closed on all paths.
        with contextlib.closing(
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        ) as probe:
            try:
                probe.connect(str(socket_path))
                msg = f"Socket {socket_path} is still in use"
                raise RuntimeError(msg)
            except (ConnectionRefusedError, OSError):
                # No server is listening; the file is stale and can be removed.
                pass
        socket_path.unlink()
    except OSError as exc:  # pragma: no cover - unlikely race
        logger.warning("Could not unlink stale socket %s: %s", socket_path, exc)


def _wait_for_socket(socket_path: Path, timeout: float) -> None:
    """Poll for *socket_path* to appear within *timeout* seconds."""
    timeout_end = time.monotonic() + timeout
    wait_time = 0.001
    while time.monotonic() < timeout_end:
        if socket_path.exists():
            return
        time.sleep(wait_time)
        wait_time = min(wait_time * 1.5, 0.1)
    msg = f"Socket file {socket_path} not created within timeout"
    raise RuntimeError(msg)


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def _parse_and_validate_request(
        self, raw: bytes
    ) -> tuple[dict[str, t.Any], str] | None:
        """Return a payload and kind when *raw* contains a valid request."""
        payload = _parse_json_safely(raw)
        if payload is None:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                logger.exception("IPC received malformed JSON")
                return None
            logger.error("IPC payload not a dict: %r", obj)
            return None

        copied_payload = payload.copy()
        kind = str(copied_payload.pop("kind", "invocation"))
        return copied_payload, kind

    def _lookup_handler(
        self, kind: str
    ) -> tuple[_RequestValidator, _RequestProcessor] | None:
        """Return the registered handler for *kind* if available."""
        handler_entry = _REQUEST_HANDLERS.get(kind)
        if handler_entry is None:
            logger.error("Unknown IPC payload kind: %r", kind)
            return None
        return handler_entry

    def _process_request(self, processor: _RequestProcessor, obj: object) -> Response:
        """Execute *processor* and wrap unexpected failures."""
        try:
            return processor(self.server.outer, obj)  # type: ignore[attr-defined]
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("IPC handler raised an exception")
            message = str(exc) or exc.__class__.__name__
            return Response(stderr=message, exit_code=1)

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        parsed = self._parse_and_validate_request(raw)
        if parsed is None:
            return

        payload, kind = parsed
        handler_entry = self._lookup_handler(kind)
        if handler_entry is None:
            return

        validator, processor = handler_entry
        obj = validator(payload)
        if obj is None:
            return

        response = self._process_request(processor, obj)
        self.wfile.write(json.dumps(response.to_dict()).encode("utf-8"))
        self.wfile.flush()


class _InnerServer(socketserver.ThreadingUnixStreamServer):
    """Threaded Unix stream server passing requests to :class:`IPCServer`."""

    def __init__(self, socket_path: Path, outer: IPCServer) -> None:
        self.outer = outer
        super().__init__(str(socket_path), _IPCHandler)
        self.daemon_threads = True


@dc.dataclass(slots=True)
class IPCHandlers:
    """Optional callbacks customising :class:`IPCServer` behaviour."""

    handler: t.Callable[[Invocation], Response] | None = None
    passthrough_handler: t.Callable[[PassthroughResult], Response] | None = None


def _validate_timeout(timeout: float, param_name: str = "timeout") -> None:
    """Validate that a timeout value is positive and finite."""
    if not (timeout > 0 and math.isfinite(timeout)):
        msg = f"{param_name} must be positive and finite, got {timeout!r}"
        raise ValueError(msg)


def _validate_accept_timeout(accept_timeout: float | None) -> None:
    """Validate that accept_timeout is positive and finite when provided."""
    if accept_timeout is not None:
        _validate_timeout(accept_timeout, "accept_timeout")


@dc.dataclass(slots=True)
class TimeoutConfig:
    """Timeout configuration forwarded by :class:`CallbackIPCServer`."""

    timeout: float = 5.0
    accept_timeout: float | None = None

    def __post_init__(self) -> None:
        """Validate timeout values to catch misconfiguration early."""
        _validate_timeout(self.timeout)
        _validate_accept_timeout(self.accept_timeout)


class IPCServer:
    """Run a Unix domain socket server for shims.

    The server listens on a Unix domain socket created by
    :class:`~cmd_mox.environment.EnvironmentManager`. Clients connect via the
    ``CMOX_IPC_SOCKET`` path and communicate using JSON messages. Connection
    attempts default to a five second timeout, but this can be overridden by
    setting :data:`~cmd_mox.environment.CMOX_IPC_TIMEOUT_ENV` in the
    environment. See the `IPC server` section of the design document for
    details on the rationale and configuration:
    ``docs/python-native-command-mocking-design.md``.
    """

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        """Create a server listening at *socket_path*.

        ``timeout`` controls startup and shutdown waits. ``accept_timeout``
        determines how often the server checks for shutdown requests while
        waiting for incoming connections. If not provided, it defaults to one
        tenth of ``timeout`` capped at 0.1 seconds. ``handlers`` groups optional
        callbacks that let callers provide custom invocation and passthrough
        logic without subclassing :class:`IPCServer`. When omitted the server
        echoes the command name and raises for passthrough results, matching
        previous behaviour.
        """
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self.accept_timeout = accept_timeout or min(0.1, timeout / 10)
        self._server: _InnerServer | None = None
        self._thread: threading.Thread | None = None
        handlers = handlers or IPCHandlers()
        self._handler = handlers.handler
        self._passthrough_handler = handlers.passthrough_handler

    def _dispatch(
        self,
        handler: t.Callable[[_DispatchArg], Response] | None,
        argument: _DispatchArg,
        *,
        default: t.Callable[[_DispatchArg], Response],
        error_builder: t.Callable[[_DispatchArg, Exception], RuntimeError]
        | None = None,
    ) -> Response:
        """Invoke *handler* when provided, otherwise fall back to *default*."""
        if handler is None:
            return default(argument)
        if error_builder is None:
            return handler(argument)
        try:
            return handler(argument)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise error_builder(argument, exc) from exc

    @staticmethod
    def _default_invocation_response(invocation: Invocation) -> Response:
        """Echo the command name when no handler overrides the behaviour."""
        return Response(stdout=invocation.command)

    @staticmethod
    def _raise_unhandled_passthrough(result: PassthroughResult) -> Response:
        """Raise when passthrough results lack a configured handler."""
        msg = f"Unhandled passthrough result for {result.invocation_id}"
        raise RuntimeError(msg)

    @staticmethod
    def _build_passthrough_error(
        result: PassthroughResult, exc: Exception
    ) -> RuntimeError:
        """Create the wrapped passthrough error surfaced to callers."""
        msg = f"Exception in passthrough handler for {result.invocation_id}: {exc}"
        return RuntimeError(msg)

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------
    def __enter__(self) -> IPCServer:
        """Start the server when entering a context."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        """Stop the server when leaving a context."""
        self.stop()

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the background server thread."""
        if self._thread:
            msg = "IPC server already started"
            raise RuntimeError(msg)

        _cleanup_stale_socket(self.socket_path)

        env_mgr = EnvironmentManager.get_active_manager()
        if env_mgr is not None:
            env_mgr.export_ipc_environment(timeout=self.timeout)

        self._server = _InnerServer(self.socket_path, self)
        self._server.timeout = self.accept_timeout
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        _wait_for_socket(self.socket_path, self.timeout)

    def stop(self) -> None:
        """Stop the server and clean up the socket."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(self.timeout)
            self._thread = None
        self._server = None
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()

    # ------------------------------------------------------------------
    # Request processing
    # ------------------------------------------------------------------
    def handle_invocation(self, invocation: Invocation) -> Response:
        """Process invocations using the configured handler when available."""
        return self._dispatch(
            self._handler,
            invocation,
            default=self._default_invocation_response,
        )

    def handle_passthrough_result(self, result: PassthroughResult) -> Response:
        """Handle passthrough results via the configured callback when provided."""
        return self._dispatch(
            self._passthrough_handler,
            result,
            default=self._raise_unhandled_passthrough,
            error_builder=self._build_passthrough_error,
        )


class CallbackIPCServer(IPCServer):
    """IPCServer variant that delegates to callbacks."""

    def __init__(
        self,
        socket_path: Path,
        handler: t.Callable[[Invocation], Response],
        passthrough_handler: t.Callable[[PassthroughResult], Response],
        *,
        timeouts: TimeoutConfig | None = None,
    ) -> None:
        """Initialise a callback-driven IPC server.

        ``timeouts`` groups the startup and accept timeout configuration so the
        legacy subclass stays within the four argument limit while remaining
        backwards compatible with previous keyword arguments.
        """
        timeouts = timeouts or TimeoutConfig()
        super().__init__(
            socket_path,
            timeout=timeouts.timeout,
            accept_timeout=timeouts.accept_timeout,
            handlers=IPCHandlers(
                handler=handler,
                passthrough_handler=passthrough_handler,
            ),
        )
        # The base class now stores the callbacks and the inherited
        # ``handle_*`` methods perform the delegation.  We keep this subclass
        # for backwards compatibility with existing imports.


def _send_request(
    kind: str,
    data: dict[str, t.Any],
    timeout: float,
    retry_config: RetryConfig | None,
) -> Response:
    """Send a JSON request of *kind* to the IPC server."""
    retry = retry_config or RetryConfig()
    sock_path = _get_validated_socket_path()
    payload = dict(data)
    payload["kind"] = kind
    payload_bytes = json.dumps(payload).encode("utf-8")

    with _connect_with_retries(sock_path, timeout, retry) as client:
        client.sendall(payload_bytes)
        client.shutdown(socket.SHUT_WR)
        raw = _read_all(client)

    parsed = _parse_json_safely(raw)
    if parsed is None:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg)
    return Response.from_payload(parsed)


def invoke_server(
    invocation: Invocation,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send *invocation* to the IPC server and return its response.

    Attempts to connect ``retry_config.retries`` times (default:
    :data:`DEFAULT_CONNECT_RETRIES`) with a linear backoff of
    ``retry_config.backoff`` seconds (default:
    :data:`DEFAULT_CONNECT_BACKOFF`). A jitter fraction (default:
    :data:`DEFAULT_CONNECT_JITTER`) spreads delays to avoid synchronized retries.
    ``retry_config.retries`` counts the total number of attempts and must be at
    least one. ``timeout`` must be positive,
    ``retry_config.backoff`` non-negative, and ``retry_config.jitter`` within
    ``[0, 1]``. The underlying
    :class:`OSError`
    bubbles up if the client cannot connect. The same timeout also applies
    to send/receive operations and may surface as :class:`socket.timeout`.
    A :class:`ValueError` is raised for invalid parameters and
    :class:`RuntimeError` if the server responds with invalid JSON.
    """
    return _send_request("invocation", invocation.to_dict(), timeout, retry_config)


def report_passthrough_result(
    result: PassthroughResult,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send passthrough execution results back to the IPC server."""
    return _send_request("passthrough-result", result.to_dict(), timeout, retry_config)
