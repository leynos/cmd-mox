"""Snapshot coverage for environment masking in diagnostics.

The semantic assertions elsewhere pin *what* must be hidden. These snapshots
pin the exact rendered shape, so an accidental change to spacing, quoting, or
section order is visible in review. Every input is a fixed synthetic mapping;
nothing is drawn from the inherited process environment.
"""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.domain import Invocation, mask_env_mapping
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.response_policy import apply_expectation_env
from cmd_mox.verifiers import UnexpectedCommandVerifier

pytestmark = pytest.mark.requires_unix_sockets

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

#: Fixed synthetic values. They are intentionally not credential-shaped so a
#: reviewer can see exactly which positions were masked.
_PROVIDED_VALUE = "provided-value"
_RECORDED_VALUE = "recorded-value"
_EXPECTED_VALUE = "expected-value"


def _conflict_message() -> str:
    """Return the message for a mixed sensitive and benign conflict.

    Returns
    -------
    str
        The rendered ``UnexpectedCommandError`` message.
    """
    double = (
        CmdMox()
        .stub("deploy")
        .with_env({"DB_PWD": _EXPECTED_VALUE, "LOG_LEVEL": "info"})
    )
    invocation = Invocation(
        command="deploy",
        args=[],
        stdin="",
        env={"DB_PWD": _PROVIDED_VALUE, "LOG_LEVEL": "debug"},
    )

    with pytest.raises(UnexpectedCommandError) as error:
        apply_expectation_env(double, invocation)

    return str(error.value)


def _verifier_message() -> str:
    """Return the verifier's rendered mismatch sections.

    Returns
    -------
    str
        The rendered ``UnexpectedCommandError`` message.
    """
    double = (
        CmdMox()
        .mock("deploy")
        .with_args("--flag")
        .with_env({"DOCKER_AUTH_CONFIG": _EXPECTED_VALUE})
    )
    invocation = Invocation(
        command="deploy",
        args=["--flag"],
        stdin="",
        env={"DOCKER_AUTH_CONFIG": _RECORDED_VALUE},
    )

    with pytest.raises(UnexpectedCommandError) as error:
        UnexpectedCommandVerifier().verify([invocation], {"deploy": double})

    return str(error.value)


def _spy_message() -> str:
    """Return the spy assertion's rendered env comparison.

    Returns
    -------
    str
        The rendered ``AssertionError`` message.
    """
    double = CmdMox().spy("deploy")
    double.invocations.append(
        Invocation(
            command="deploy",
            args=[],
            stdin="",
            env={"DB_PWD": _RECORDED_VALUE, "LOG_LEVEL": "debug"},
        )
    )

    with pytest.raises(AssertionError) as error:
        double.assert_called_with(env={"DB_PWD": _EXPECTED_VALUE, "LOG_LEVEL": "debug"})

    return str(error.value)


def test_conflict_message_snapshot(snapshot: SnapshotAssertion) -> None:
    """The conflict message hides the secret and keeps the benign value."""
    message = _conflict_message()

    assert message == snapshot
    assert _PROVIDED_VALUE not in message, "Invocation value leaked"
    assert _EXPECTED_VALUE not in message, "Expected value leaked"
    assert "LOG_LEVEL='debug'" in message, "Benign value was hidden"


def test_verifier_message_snapshot(snapshot: SnapshotAssertion) -> None:
    """The verifier sections hide both values and keep the reason."""
    message = _verifier_message()

    assert message == snapshot
    assert _RECORDED_VALUE not in message, "Recorded value leaked"
    assert _EXPECTED_VALUE not in message, "Expected value leaked"


def test_spy_message_snapshot(snapshot: SnapshotAssertion) -> None:
    """The spy assertion hides both values and records raw data untouched."""
    message = _spy_message()

    assert message == snapshot
    assert _RECORDED_VALUE not in message, "Recorded value leaked"
    assert _EXPECTED_VALUE not in message, "Expected value leaked"
    assert mask_env_mapping({"DB_PWD": _RECORDED_VALUE}) == {"DB_PWD": "***"}, (
        "The helper no longer masks what the message masks"
    )
