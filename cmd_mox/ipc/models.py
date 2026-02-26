"""Data models and serialisation helpers for IPC communication."""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as t

from cmd_mox.expectations import is_sensitive_recording_env_key

logger = logging.getLogger(__name__)

_REPR_FIELD_LIMIT: t.Final[int] = 256


def _shorten(text: str, limit: int = _REPR_FIELD_LIMIT) -> str:
    if limit <= 0:
        return ""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


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
        """Copy stdout/stderr/exit_code from *resp* (env is not copied)."""
        self.stdout, self.stderr, self.exit_code = (
            resp.stdout,
            resp.stderr,
            resp.exit_code,
        )

    def __repr__(self) -> str:
        """Return a convenient debug representation."""
        safe_env: dict[str, str] = {}
        for key, value in self.env.items():
            safe_env[key] = (
                "<redacted>" if is_sensitive_recording_env_key(key) else value
            )

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
        if env is not None and not isinstance(env, dict):
            logger.warning(
                "Payload 'env' is not a dict: %r (type: %s)",
                env,
                type(env).__name__,
            )
            payload["env"] = {}
        try:
            response = cls(**payload)
        except TypeError as exc:
            msg = "Invalid response payload from IPC server"
            raise RuntimeError(msg) from exc
        response.passthrough = passthrough
        return response


__all__ = [
    "Invocation",
    "PassthroughRequest",
    "PassthroughResult",
    "Response",
]
