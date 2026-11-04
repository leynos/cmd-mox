"""Tests for shim failure stream cleanup helpers."""

from __future__ import annotations

import os

import pytest

from cmd_mox._shim_failures import _redirect_descriptor_to_null


@pytest.mark.parametrize(
    ("opened_descriptor", "duplicate_error", "expected_duplicate"),
    [(8, None, (8, 1)), (1, None, None), (8, OSError, (8, 1))],
)
def test_redirect_descriptor_to_null_closes_temporary_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    opened_descriptor: int,
    duplicate_error: type[OSError] | None,
    expected_duplicate: tuple[int, int] | None,
) -> None:
    """Close the temporary descriptor after redirection when it is distinct."""
    open_calls: list[tuple[str, int]] = []
    duplicate_calls: list[tuple[int, int]] = []
    close_calls: list[int] = []

    def fake_open(path: str, flags: int) -> int:
        open_calls.append((path, flags))
        return opened_descriptor

    def fake_dup2(source: int, destination: int) -> None:
        duplicate_calls.append((source, destination))
        if duplicate_error is not None:
            raise duplicate_error

    def fake_close(descriptor: int) -> None:
        close_calls.append(descriptor)

    with monkeypatch.context() as scoped:
        scoped.setattr("cmd_mox._shim_failures.os.open", fake_open)
        scoped.setattr("cmd_mox._shim_failures.os.dup2", fake_dup2)
        scoped.setattr("cmd_mox._shim_failures.os.close", fake_close)

        _redirect_descriptor_to_null(1)

    assert open_calls == [(os.devnull, os.O_WRONLY)], (
        "The null device must be opened for writing"
    )
    expected_duplicates = [] if expected_duplicate is None else [expected_duplicate]
    assert duplicate_calls == expected_duplicates, (
        "The null descriptor must replace the requested descriptor when distinct"
    )
    expected_closes = [] if opened_descriptor == 1 else [opened_descriptor]
    assert close_calls == expected_closes, (
        "Only a distinct temporary descriptor should be closed"
    )
