"""Direct tests for :class:`cmd_mox.verifiers.UnexpectedCommandVerifier`.

The controller runs this verifier first, so a full ``CmdMox.verify()`` can
reach it, but only through a Unix socket. These tests drive the verifier
directly and pin the complete diagnostic text for each failure mode.
"""

from __future__ import annotations

import typing as typ
from types import SimpleNamespace

import pytest

from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.test_doubles import CommandDouble, DoubleKind
from cmd_mox.verifiers import UnexpectedCommandVerifier


def _double(
    name: str,
    kind: DoubleKind,
    expectation: Expectation,
) -> CommandDouble:
    """Return a double stand-in carrying *expectation* and *kind*."""
    return typ.cast(
        "CommandDouble",
        SimpleNamespace(name=name, kind=kind, expectation=expectation),
    )


def _invocation(
    command: str,
    *args: str,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> Invocation:
    """Return an invocation of *command* with the supplied call details."""
    return Invocation(
        command=command,
        args=list(args),
        stdin=stdin,
        env={} if env is None else env,
    )


def test_unregistered_command_reports_stdin_and_excludes_stubs() -> None:
    """An unknown command lists only non-stub expectations and shows stdin."""
    doubles = {
        "zeta": _double("zeta", DoubleKind.MOCK, Expectation("zeta")),
        "alpha": _double("alpha", DoubleKind.SPY, Expectation("alpha")),
        "helper": _double("helper", DoubleKind.STUB, Expectation("helper")),
    }
    invocation = _invocation("ghost", "--x", stdin="payload")

    with pytest.raises(UnexpectedCommandError) as excinfo:
        UnexpectedCommandVerifier().verify([invocation], doubles)

    assert str(excinfo.value) == (
        "Unexpected command invocation.\n"
        "\n"
        "Actual call:\n"
        "  ghost('--x')\n"
        "  stdin='payload'\n"
        "\n"
        "Registered expectations:\n"
        "  'alpha', 'zeta'"
    )


def test_unregistered_command_omits_stdin_when_empty() -> None:
    """An empty stdin is not rendered in the actual-call section."""
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, Expectation("deploy"))}
    invocation = _invocation("ghost")

    with pytest.raises(UnexpectedCommandError) as excinfo:
        UnexpectedCommandVerifier().verify([invocation], doubles)

    assert str(excinfo.value) == (
        "Unexpected command invocation.\n"
        "\n"
        "Actual call:\n"
        "  ghost()\n"
        "\n"
        "Registered expectations:\n"
        "  'deploy'"
    )


def test_unregistered_command_reports_when_only_stubs_are_registered() -> None:
    """A registry of only stubs explains why no expectations are listed."""
    doubles = {"helper": _double("helper", DoubleKind.STUB, Expectation("helper"))}

    with pytest.raises(UnexpectedCommandError) as excinfo:
        UnexpectedCommandVerifier().verify([_invocation("ghost")], doubles)

    assert str(excinfo.value) == (
        "Unexpected command invocation.\n"
        "\n"
        "Actual call:\n"
        "  ghost()\n"
        "\n"
        "Registered expectations:\n"
        "  (none: stubs are excluded from expected commands)"
    )


def test_expectation_mismatch_reports_env_and_stdin() -> None:
    """A mismatch shows the expectation, the focused actual call, and reason."""
    expectation = (
        Expectation("deploy")
        .with_args("--flag")
        .with_stdin("payload")
        .with_env({"TOKEN": "classified"})
    )
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}
    invocation = _invocation(
        "deploy",
        "--other",
        stdin="wrong",
        env={"TOKEN": "classified", "PATH": "/bin"},
    )

    with pytest.raises(UnexpectedCommandError) as excinfo:
        UnexpectedCommandVerifier().verify([invocation], doubles)

    assert str(excinfo.value) == (
        "Unexpected command invocation.\n"
        "\n"
        "Expected:\n"
        "  deploy('--flag')\n"
        "  stdin='payload'\n"
        "  env={'TOKEN': '***'}\n"
        "\n"
        "Actual:\n"
        "  deploy('--other')\n"
        "  stdin='wrong'\n"
        "  env={'TOKEN': '***'}\n"
        "\n"
        "Reason:\n"
        "  arguments ['--other'] != ['--flag']"
    )


def test_mock_overflow_reports_observed_count_and_last_call() -> None:
    """A mock called past its limit reports the count and the final call."""
    expectation = (
        Expectation("deploy")
        .with_args("--flag")
        .with_stdin("payload")
        .with_env({"TOKEN": "classified"})
        .times_called(1)
    )
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}
    invocations = [
        _invocation("deploy", "--flag", stdin="payload", env={"TOKEN": "classified"}),
        _invocation("deploy", "--flag", stdin="payload", env={"TOKEN": "classified"}),
    ]

    with pytest.raises(UnexpectedCommandError) as excinfo:
        UnexpectedCommandVerifier().verify(invocations, doubles)

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
        "  2\n"
        "\n"
        "Last call:\n"
        "  deploy('--flag')\n"
        "  stdin='payload'\n"
        "  env={'TOKEN': '***'}"
    )


def test_mock_accepts_calls_up_to_its_limit() -> None:
    """A mock invoked exactly its expected number of times passes."""
    expectation = Expectation("deploy").with_args("--flag").times_called(2)
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}
    invocations = [
        _invocation("deploy", "--flag"),
        _invocation("deploy", "--flag"),
    ]

    UnexpectedCommandVerifier().verify(invocations, doubles)


def test_spy_is_not_count_limited() -> None:
    """Spies record calls but are never rejected for exceeding a count."""
    expectation = Expectation("deploy").with_args("--flag").times_called(1)
    doubles = {"deploy": _double("deploy", DoubleKind.SPY, expectation)}
    invocations = [
        _invocation("deploy", "--flag"),
        _invocation("deploy", "--flag"),
    ]

    UnexpectedCommandVerifier().verify(invocations, doubles)


def test_stub_is_skipped_even_when_its_expectation_does_not_match() -> None:
    """Stubs are not validated against their expectation."""
    expectation = Expectation("helper").with_args("--expected")
    doubles = {"helper": _double("helper", DoubleKind.STUB, expectation)}

    UnexpectedCommandVerifier().verify([_invocation("helper", "--other")], doubles)


def test_mock_counts_are_tracked_per_command() -> None:
    """Each mock has an independent call budget."""
    doubles = {
        "first": _double(
            "first", DoubleKind.MOCK, Expectation("first").times_called(1)
        ),
        "second": _double(
            "second", DoubleKind.MOCK, Expectation("second").times_called(1)
        ),
    }
    invocations = [_invocation("first"), _invocation("second")]

    UnexpectedCommandVerifier().verify(invocations, doubles)


def test_mock_counts_reset_between_verify_calls() -> None:
    """A fresh verification starts counting from zero."""
    expectation = Expectation("deploy").times_called(1)
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}
    verifier = UnexpectedCommandVerifier()

    verifier.verify([_invocation("deploy")], doubles)
    verifier.verify([_invocation("deploy")], doubles)


def test_mismatch_is_reported_before_the_count_limit() -> None:
    """A mismatch wins over overflow once the call limit is already spent."""
    expectation = Expectation("deploy").with_args("--flag").times_called(1)
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}

    with pytest.raises(UnexpectedCommandError) as excinfo:
        UnexpectedCommandVerifier().verify(
            [
                # The first call matches and exhausts the mock's budget.
                _invocation("deploy", "--flag"),
                # The second call would overflow, but it does not match, so the
                # mismatch must be reported instead of the count violation.
                _invocation("deploy", "--other"),
            ],
            doubles,
        )

    assert str(excinfo.value) == (
        "Unexpected command invocation.\n"
        "\n"
        "Expected:\n"
        "  deploy('--flag')\n"
        "\n"
        "Actual:\n"
        "  deploy('--other')\n"
        "\n"
        "Reason:\n"
        "  arguments ['--other'] != ['--flag']"
    )
