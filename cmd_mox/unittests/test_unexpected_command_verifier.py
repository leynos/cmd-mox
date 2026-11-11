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
    ), "an unknown command must show stdin and list only non-stub expectations"


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
    ), "an empty stdin must be omitted from the actual-call section"


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
    ), "a registry of only stubs must explain the empty expectation list"


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
    ), "a mismatch must show the expectation, the masked actual call, and the reason"


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
    ), "mock overflow must report the observed count and the final call"


def test_mock_accepts_calls_up_to_its_limit() -> None:
    """A mock invoked exactly its expected number of times passes."""
    expectation = Expectation("deploy").with_args("--flag").times_called(2)
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}
    invocations = [
        _invocation("deploy", "--flag"),
        _invocation("deploy", "--flag"),
    ]

    UnexpectedCommandVerifier().verify(invocations, doubles)

    # Failure control: one call past the limit must still raise, proving the
    # passing case above is not vacuous.
    with pytest.raises(UnexpectedCommandError):
        UnexpectedCommandVerifier().verify(
            [*invocations, _invocation("deploy", "--flag")], doubles
        )


def test_spy_is_not_count_limited() -> None:
    """Spies record calls but are never rejected for exceeding a count."""
    expectation = Expectation("deploy").with_args("--flag").times_called(1)
    doubles = {"deploy": _double("deploy", DoubleKind.SPY, expectation)}
    invocations = [
        _invocation("deploy", "--flag"),
        _invocation("deploy", "--flag"),
    ]

    UnexpectedCommandVerifier().verify(invocations, doubles)

    # Failure control: the same over-budget calls must raise for a mock, so the
    # spy exemption is the only reason this test passes.
    mock_doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}
    with pytest.raises(UnexpectedCommandError):
        UnexpectedCommandVerifier().verify(invocations, mock_doubles)


def test_stub_is_skipped_even_when_its_expectation_does_not_match() -> None:
    """Stubs are not validated against their expectation."""
    expectation = Expectation("helper").with_args("--expected")
    doubles = {"helper": _double("helper", DoubleKind.STUB, expectation)}

    UnexpectedCommandVerifier().verify([_invocation("helper", "--other")], doubles)

    # Failure control: the same mismatch must raise for a mock, so the stub
    # exemption is what keeps the passing case above green.
    mock_doubles = {"helper": _double("helper", DoubleKind.MOCK, expectation)}
    with pytest.raises(UnexpectedCommandError):
        UnexpectedCommandVerifier().verify(
            [_invocation("helper", "--other")], mock_doubles
        )


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

    # Failure control: spending one command's budget twice must raise, so the
    # passing case above proves the budgets are independent rather than unused.
    with pytest.raises(UnexpectedCommandError):
        UnexpectedCommandVerifier().verify(
            [_invocation("first"), _invocation("first")], doubles
        )


def test_mock_counts_reset_between_verify_calls() -> None:
    """A fresh verification starts counting from zero."""
    expectation = Expectation("deploy").times_called(1)
    doubles = {"deploy": _double("deploy", DoubleKind.MOCK, expectation)}
    verifier = UnexpectedCommandVerifier()

    verifier.verify([_invocation("deploy")], doubles)
    verifier.verify([_invocation("deploy")], doubles)

    # Failure control: after the reset, two calls must still overflow, so the
    # second passing verify is not merely an unenforced count.
    with pytest.raises(UnexpectedCommandError):
        verifier.verify([_invocation("deploy"), _invocation("deploy")], doubles)


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
    ), "a mismatch must be reported ahead of the count overflow"
