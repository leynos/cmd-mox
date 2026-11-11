"""Direct tests for :class:`cmd_mox.verifiers.CountVerifier`.

The controller runs the count check after the unexpected-command and order
checks, so a full ``CmdMox.verify()`` can mask these branches. These tests call
the verifier directly and pin the complete diagnostic text, which is the
contract users paste into bug reports.
"""

from __future__ import annotations

import pytest

from cmd_mox.errors import UnexpectedCommandError, UnfulfilledExpectationError
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.verifiers import CountVerifier


def _invocation(
    *args: str,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> Invocation:
    """Return an invocation of ``deploy`` with the supplied call details."""
    return Invocation(
        command="deploy",
        args=list(args),
        stdin=stdin,
        env={} if env is None else env,
    )


def test_count_verifier_accepts_exact_counts_and_ignores_extra_keys() -> None:
    """Exact counts pass, and invocations for unregistered commands are ignored."""
    expectation = Expectation("deploy").with_args("--flag").times_called(2)
    calls = [_invocation("--flag"), _invocation("--flag")]

    CountVerifier().verify(
        {"deploy": expectation},
        {"deploy": calls, "unregistered": [_invocation("--other")]},
    )

    # Failure control: the same setup must fail when the count is short, so a
    # verifier that never raises cannot pass this test vacuously.
    with pytest.raises(UnfulfilledExpectationError):
        CountVerifier().verify({"deploy": expectation}, {"deploy": calls[:1]})


def test_count_verifier_reports_absent_command_as_unfulfilled() -> None:
    """A command with no recorded invocations reports the full expectation."""
    expectation = (
        Expectation("deploy")
        .with_args("--flag")
        .with_stdin("payload")
        .with_env({"TOKEN": "classified"})
        .times_called(2)
    )

    with pytest.raises(UnfulfilledExpectationError) as excinfo:
        CountVerifier().verify({"deploy": expectation}, {})

    assert str(excinfo.value) == (
        "Unfulfilled expectation.\n"
        "\n"
        "Expected:\n"
        "  deploy('--flag')\n"
        "  expected calls=2\n"
        "  stdin='payload'\n"
        "  env={'TOKEN': '***'}\n"
        "\n"
        "Observed calls:\n"
        "  0 (expected 2)\n"
        "\n"
        "Recorded invocations:\n"
        "  (none)"
    ), "an absent command must report the full expectation and zero calls"


def test_count_verifier_reports_partial_calls_with_env_and_stdin() -> None:
    """Recorded calls are described with the focused env and stdin."""
    expectation = (
        Expectation("deploy")
        .with_args("--flag")
        .with_stdin("payload")
        .with_env({"TOKEN": "classified"})
        .times_called(3)
    )
    calls = [
        _invocation(
            "--flag",
            stdin="payload",
            env={"TOKEN": "classified", "PATH": "/bin"},
        )
    ]

    with pytest.raises(UnfulfilledExpectationError) as excinfo:
        CountVerifier().verify({"deploy": expectation}, {"deploy": calls})

    assert str(excinfo.value) == (
        "Unfulfilled expectation.\n"
        "\n"
        "Expected:\n"
        "  deploy('--flag')\n"
        "  expected calls=3\n"
        "  stdin='payload'\n"
        "  env={'TOKEN': '***'}\n"
        "\n"
        "Observed calls:\n"
        "  1 (expected 3)\n"
        "\n"
        "Recorded invocations:\n"
        "  deploy('--flag')\n"
        "  stdin='payload'\n"
        "  env={'TOKEN': '***'}"
    ), "partial calls must report the observed count and the focused call details"


def test_count_verifier_reports_overflow_with_the_last_call() -> None:
    """Overflow reports the observed count and the final recorded call."""
    expectation = (
        Expectation("deploy")
        .with_args("--flag")
        .with_stdin("payload")
        .with_env({"TOKEN": "classified"})
        .times_called(1)
    )
    calls = [
        _invocation("one", stdin="payload", env={"TOKEN": "classified"}),
        _invocation("two", stdin="payload", env={"TOKEN": "classified"}),
        _invocation("three", stdin="payload", env={"TOKEN": "classified"}),
    ]

    with pytest.raises(UnexpectedCommandError) as excinfo:
        CountVerifier().verify({"deploy": expectation}, {"deploy": calls})

    assert str(excinfo.value) == (
        "Unexpected additional invocation.\n"
        "\n"
        "Expected:\n"
        "  deploy('--flag')\n"
        "  expected calls=1\n"
        "  stdin='payload'\n"
        "  env={'TOKEN': '***'}\n"
        "\n"
        "Observed calls:\n"
        "  3 (expected 1)\n"
        "\n"
        "Last call:\n"
        "  deploy('three')\n"
        "  stdin='payload'\n"
        "  env={'TOKEN': '***'}"
    ), "overflow must report the observed count and the final recorded call"


def test_count_verifier_reports_the_first_failing_expectation() -> None:
    """Expectations are checked in mapping order and the first failure wins."""
    first = Expectation("first").times_called(2)
    second = Expectation("second").times_called(2)

    with pytest.raises(UnfulfilledExpectationError) as excinfo:
        CountVerifier().verify(
            {"first": first, "second": second},
            {"first": [], "second": []},
        )

    message = str(excinfo.value)
    assert "first()" in message, "the first failing expectation must be reported"
    assert "second()" not in message, "later expectations must not be reported"
