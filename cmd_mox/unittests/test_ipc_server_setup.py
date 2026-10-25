"""Regression tests for IPC server request-handler setup."""

import socket
import time
from pathlib import Path

import pytest

from cmd_mox.ipc import _observability
from cmd_mox.ipc import server as ipc_server_module
from cmd_mox.ipc.server import IPCServer

pytestmark = pytest.mark.requires_unix_sockets


def test_ipc_server_reports_timeout_when_deadline_expires_before_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired setup deadline is reported by the handler's read path."""
    socket_path = tmp_path / "ipc.sock"

    with _observability.capture_events() as events, IPCServer(socket_path):
        monkeypatch.setattr(
            ipc_server_module,
            "_compute_deadline",
            lambda _timeout: time.monotonic() - 1.0,
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as incomplete:
            incomplete.settimeout(1.0)
            incomplete.connect(str(socket_path))
            deadline = time.monotonic() + 1.0
            timeout_event = None
            while time.monotonic() < deadline:
                timeout_event = next(
                    (
                        event
                        for event in events
                        if (event.operation, event.outcome)
                        == ("ipc.server.connection", "receive_timeout")
                    ),
                    None,
                )
                if timeout_event is not None:
                    break
                time.sleep(0.01)

    assert timeout_event is not None, (
        "Expired deadline did not emit a receive timeout event"
    )
    assert timeout_event.error_category == "TimeoutError", (
        "Expired receive deadline reported the wrong error category"
    )
