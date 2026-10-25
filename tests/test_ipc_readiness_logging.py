"""Tests for IPC readiness-probe connection logging."""

from __future__ import annotations

import contextlib
import logging
import threading
import typing as typ

import pytest

from cmd_mox.ipc import IPCServer, _observability, _server_core

if typ.TYPE_CHECKING:
    from pathlib import Path

_CONNECTION_CLOSED_MESSAGE = "IPC connection closed before request was received"


class _ConnectionClosedObserver(logging.Handler):
    """Signal when the IPC server logs a readiness-probe connection close."""

    def __init__(self) -> None:
        super().__init__()
        self.connection_closed_logged = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name != "cmd_mox.ipc._server_core" or record.levelno != logging.INFO:
            return
        if record.getMessage() == _CONNECTION_CLOSED_MESSAGE:
            self.connection_closed_logged.set()


def _assert_connection_close_is_logged_quietly(
    records: list[logging.LogRecord],
) -> None:
    """Assert empty reads have one quiet INFO outcome."""
    server_records = [
        record for record in records if record.name == "cmd_mox.ipc._server_core"
    ]
    assert any(
        record.getMessage() == _CONNECTION_CLOSED_MESSAGE
        and record.levelno == logging.INFO
        for record in server_records
    ), "An empty read was not logged as a connection closure"
    assert not any(
        "malformed JSON" in record.getMessage() for record in server_records
    ), "An empty read was logged as malformed JSON"
    assert all(
        record.levelno < logging.ERROR and record.exc_info is None
        for record in server_records
    ), "An empty read emitted an error or exception record"


@pytest.mark.requires_unix_sockets
def test_readiness_probe_logs_connection_close_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The startup probe should not be reported as malformed JSON."""
    logger_name = "cmd_mox.ipc._server_core"
    caplog.set_level(logging.INFO, logger=logger_name)
    observer = _ConnectionClosedObserver()
    root_logger = logging.getLogger()
    with contextlib.ExitStack() as cleanup:
        root_logger.addHandler(observer)
        cleanup.callback(root_logger.removeHandler, observer)
        with (
            _observability.capture_events() as events,
            IPCServer(tmp_path / "ipc.sock", timeout=2),
        ):
            assert observer.connection_closed_logged.wait(timeout=2), (
                "The readiness probe did not reach the empty-read handler"
            )

    _assert_connection_close_is_logged_quietly(caplog.records)
    [event] = [
        item
        for item in events
        if item.operation == "ipc.server.connection"
        and item.outcome == "receive_closed"
        and item.error_category == "ConnectionClosed"
    ]
    assert event.correlation_id is None, "Readiness probe must not carry correlation"


def test_decode_payload_logs_connection_close_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty request should be logged as a quiet disconnect."""
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc._server_core")

    result = _server_core._decode_payload(b"")

    assert result is None, "An empty read must decode to None"
    _assert_connection_close_is_logged_quietly(caplog.records)
