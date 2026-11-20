"""Utilities for managing IPC Unix domain sockets."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import logging
import pathlib
import socket
import time
import typing as typ

from ._deadline import _remaining_time
from ._limits import (
    DEFAULT_READ_CHUNK_SIZE,
    MessageTooLargeError,
    ReadBounds,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

logger = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class ReadOptions(ReadBounds):
    """Tunables for :func:`read_until_eof`.

    Inherits the validated byte budget and receive chunk size from
    :class:`~cmd_mox.ipc._limits.ReadBounds`, and adds the deadline arithmetic
    that lets each transport share its own clock.

    Parameters
    ----------
    chunk_size:
        Upper bound on bytes requested per receive.
    max_bytes:
        Maximum number of payload bytes accepted.
    remaining_time:
        Callable returning the positive time left before a deadline, raising
        :class:`TimeoutError` once it has passed. Injecting it lets a caller
        share the deadline arithmetic, and its clock, with the rest of the
        connection.
    """

    remaining_time: cabc.Callable[[float], float] = _remaining_time


def read_until_eof(
    sock: socket.socket,
    deadline: float,
    options: ReadOptions,
) -> bytes:
    """Read *sock* to EOF, refusing a message larger than the byte budget.

    The read is bounded in both directions. Each receive is limited to the
    remaining byte budget plus one, so the call at the limit becomes a
    one-byte EOF probe: a payload of exactly ``max_bytes`` followed by EOF
    succeeds, while a single further byte proves the message is oversized.
    Bytes accumulate in one resizable buffer rather than one object per
    receive, so a peer that dribbles minimal fragments cannot inflate the
    retained storage the way a fragment-per-``recv`` list would. An oversized
    message drops its buffered prefix before raising, so the caller never
    retains more than ``max_bytes + 1`` bytes.

    Parameters
    ----------
    sock:
        Connected socket to read from.
    deadline:
        :func:`time.monotonic` timestamp bounding the whole read.
    options:
        Byte budget, receive chunk size, and deadline arithmetic.

    Returns
    -------
    bytes
        Every payload byte received before the peer closed the connection.

    Raises
    ------
    MessageTooLargeError
        If the peer sends more than ``options.max_bytes`` bytes.
    TimeoutError
        If the deadline expires before the peer closes the connection.
    OSError
        If the socket fails while receiving.
    """  # ruff: ignore[docstring-extraneous-exception] - both propagate from the socket unchanged
    buffer = bytearray()
    received = 0
    while True:
        sock.settimeout(options.remaining_time(deadline))
        request_size = min(options.chunk_size, options.max_bytes - received + 1)
        chunk = sock.recv(request_size)
        if not chunk:
            return bytes(buffer)
        received += len(chunk)
        if received > options.max_bytes:
            # The message is already unusable, so retaining the prefix would
            # defeat the point of the limit.
            buffer.clear()
            raise MessageTooLargeError(received=received, limit=options.max_bytes)
        buffer.extend(chunk)


def cleanup_stale_socket(socket_path: pathlib.Path) -> None:
    """Remove a pre-existing socket when no server is listening.

    Only two probe failures prove that nothing is listening: a refused
    connection, and a path that no longer exists. Every other :class:`OSError`
    — a permission denial, most importantly — means the probe could not reach a
    socket that may well be live, so it propagates untouched rather than
    deleting a socket this process merely cannot connect to.

    Raises
    ------
    RuntimeError
        If an active server is still listening on *socket_path*.
    """
    socket_path = pathlib.Path(socket_path)
    address = str(socket_path)
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe:
        try:
            probe.connect(address)
        except (ConnectionRefusedError, FileNotFoundError):
            pass
        else:
            msg = f"Socket {socket_path} is still in use"
            raise RuntimeError(msg)

    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError as exc:  # pragma: no cover - unlikely race
            logger.warning("Could not unlink stale socket %s: %s", socket_path, exc)


def _try_socket_connection(address: str, timeout: float) -> bool:
    """Attempt to connect to *address* within *timeout* seconds.

    Returns
    -------
    bool
        ``True`` when the connection succeeded, ``False`` otherwise.
    """
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe:
        probe.settimeout(timeout)
        try:
            probe.connect(address)
        except OSError:
            return False
    return True


def _poll_socket_until_ready(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll a Unix domain socket until it accepts connections within timeout.

    Raises
    ------
    RuntimeError
        If the socket does not accept connections before *timeout* elapses.
    """
    deadline = time.monotonic() + timeout
    wait_time = 0.001
    address = str(socket_path)

    while True:
        if (remaining := deadline - time.monotonic()) <= 0:
            break

        if _try_socket_connection(address, min(wait_time, remaining)):
            return

        if (remaining := deadline - time.monotonic()) <= 0:
            break

        time.sleep(min(wait_time, remaining))
        wait_time = min(wait_time * 1.5, 0.1)

    msg = f"Socket {socket_path} not accepting connections within timeout"
    raise RuntimeError(msg)


def wait_for_socket(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll for *socket_path* readiness within *timeout* seconds."""
    _poll_socket_until_ready(pathlib.Path(socket_path), timeout)


__all__ = [
    "DEFAULT_READ_CHUNK_SIZE",
    "ReadOptions",
    "cleanup_stale_socket",
    "read_until_eof",
    "wait_for_socket",
]
