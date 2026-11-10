"""Unit tests for command expectation environment handling."""

from __future__ import annotations

import os
import typing as typ

import pytest

from cmd_mox import CmdMox
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation, Response
from cmd_mox.unittests._env_helpers import require_shim_dir

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc
    import subprocess


@pytest.mark.requires_unix_sockets
def test_with_env_injection(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment variables provided via with_env() reach invocation.env."""
    monkeypatch.delenv("HELLO", raising=False)
    mox = CmdMox()
    host_observations: list[str | None] = []

    def handler(inv: Invocation) -> Response:
        """Echo the injected variable and record the host environment."""
        host_observations.append(os.environ.get("HELLO"))
        return Response(stdout=inv.env.get("HELLO", ""))

    mox.stub("env").with_env({"HELLO": "WORLD"}).runs(handler)
    with mox:
        mox.replay()
        path = require_shim_dir(mox.environment) / "env"
        result = run([str(path)], shell=False)
        mox.verify()

    assert result.stdout.strip() == "WORLD", "with_env() value reaches the handler"
    assert host_observations == [None], "handler observed a mutated host environment"
    assert "HELLO" not in os.environ


@pytest.mark.parametrize(
    ("payload", "exc_type", "match"),
    [
        pytest.param(
            {42: "value"},
            TypeError,
            "name must be str",
            id="non-string-key",
        ),
        pytest.param(
            {"": "value"},
            ValueError,
            "cannot be empty",
            id="empty-key",
        ),
        pytest.param(
            {"VAR": 7},
            TypeError,
            "value must be str",
            id="non-string-value",
        ),
    ],
)
def test_with_env_rejects_invalid_entries(
    payload: dict[typ.Any, typ.Any],
    exc_type: type[Exception],
    match: str,
) -> None:
    """with_env() should reject malformed environment entries.

    The parametrized payloads deliberately violate the ``dict[str, str]``
    contract to exercise the defensive validation inside ``with_env()``.
    """
    expectation = Expectation("cmd")
    with pytest.raises(exc_type, match=match):
        expectation.with_env(payload)
