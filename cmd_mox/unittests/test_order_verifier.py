"""Tests for the ordered expectation verifier."""

from __future__ import annotations

import pytest

from cmd_mox.errors import UnexpectedCommandError, UnfulfilledExpectationError
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.verifiers import OrderVerifier


def _invocation(
    command: str,
    *args: str,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> Invocation:
    """Create an invocation for *command* with *args* and call details.

    Returns
    -------
    Invocation
        The invocation containing the command, arguments, stdin, and env.
    """
    return Invocation(
        command=command,
        args=list(args),
        stdin=stdin,
        env={} if env is None else env,
    )


def test_order_verifier_ignores_unordered_invocations_with_same_command() -> None:
    """Unordered expectations sharing a command name should not trip ordering."""
    fetch = Expectation("git").with_args("fetch").in_order()

    journal = [
        _invocation("git", "status"),
        _invocation("git", "fetch"),
    ]

    OrderVerifier([fetch]).verify(journal)

    # Failure control: the ordered expectation must still fail when absent, so
    # the passing case above is not vacuous.
    with pytest.raises(UnfulfilledExpectationError):
        OrderVerifier([fetch]).verify([_invocation("git", "status")])


def test_order_verifier_flags_out_of_order_matches() -> None:
    """An invocation matching a later ordered expectation should fail fast."""
    first = Expectation("git").with_args("fetch").in_order()
    second = Expectation("git").with_args("status").in_order()

    journal = [
        _invocation("git", "status"),
        _invocation("git", "fetch"),
    ]

    with pytest.raises(UnexpectedCommandError):
        OrderVerifier([first, second]).verify(journal)


def test_order_verifier_reports_missing_expectations_with_numbering() -> None:
    """Unobserved ordered expectations are listed from the first gap onward."""
    fetch = Expectation("git").with_args("fetch").in_order()
    status = Expectation("git").with_args("status").in_order()
    push = Expectation("git").with_args("push").in_order()

    journal = [_invocation("git", "fetch")]

    with pytest.raises(UnfulfilledExpectationError) as excinfo:
        OrderVerifier([fetch, status, push]).verify(journal)

    assert str(excinfo.value) == (
        "Ordered expectations not satisfied.\n"
        "\n"
        "Expected order:\n"
        "  1. git('fetch')\n"
        "  2. git('status')\n"
        "  3. git('push')\n"
        "\n"
        "Observed order:\n"
        "  1. git('fetch')\n"
        "\n"
        "Missing:\n"
        "  2. git('status')\n"
        "  3. git('push')"
    ), "unobserved ordered expectations must be numbered from the first gap"


@pytest.mark.parametrize(
    ("ordered_args", "journal_args", "expected"),
    [
        (
            (("fetch",), ("status",)),
            (("status",), ("fetch",)),
            (
                "Ordered expectation violated.\n"
                "\n"
                "Expected order:\n"
                "  1. git('fetch')\n"
                "  2. git('status')\n"
                "\n"
                "Observed order:\n"
                "  1. git('status')\n"
                "  2. git('fetch')\n"
                "\n"
                "First mismatch:\n"
                "  position 1\n"
                "  expected:\n"
                "    git('fetch')\n"
                "  actual:\n"
                "    git('status')\n"
                "\n"
                "Reason:\n"
                "  arguments ['status'] != ['fetch']"
            ),
        ),
        (
            (("fetch",), ("status",), ("push",)),
            (("fetch",), ("push",), ("status",)),
            (
                "Ordered expectation violated.\n"
                "\n"
                "Expected order:\n"
                "  1. git('fetch')\n"
                "  2. git('status')\n"
                "  3. git('push')\n"
                "\n"
                "Observed order:\n"
                "  1. git('fetch')\n"
                "  2. git('push')\n"
                "  3. git('status')\n"
                "\n"
                "First mismatch:\n"
                "  position 2\n"
                "  expected:\n"
                "    git('status')\n"
                "  actual:\n"
                "    git('push')\n"
                "\n"
                "Reason:\n"
                "  arguments ['push'] != ['status']"
            ),
        ),
    ],
    ids=["first-position", "after-matching-prefix"],
)
def test_order_verifier_reports_the_first_positional_violation(
    ordered_args: tuple[tuple[str, ...], ...],
    journal_args: tuple[tuple[str, ...], ...],
    expected: str,
) -> None:
    """The first violating position is reported, including a matching prefix."""
    ordered = [Expectation("git").with_args(*args).in_order() for args in ordered_args]
    journal = [_invocation("git", *args) for args in journal_args]

    with pytest.raises(UnexpectedCommandError) as excinfo:
        OrderVerifier(ordered).verify(journal)

    assert str(excinfo.value) == expected, (
        "the first violating position must be reported with its reason"
    )


def test_order_verifier_renders_env_and_stdin_for_the_mismatch() -> None:
    """The mismatch section uses the expectation's env keys and stdin."""
    first = (
        Expectation("deploy")
        .with_args("--first")
        .with_stdin("payload")
        .with_env({"TOKEN": "classified"})
        .in_order()
    )
    second = Expectation("deploy").with_args("--second").in_order()

    journal = [
        _invocation(
            "deploy",
            "--second",
            stdin="wrong",
            env={"TOKEN": "classified", "PATH": "/bin"},
        ),
        _invocation(
            "deploy",
            "--first",
            stdin="payload",
            env={"TOKEN": "classified"},
        ),
    ]

    with pytest.raises(UnexpectedCommandError) as excinfo:
        OrderVerifier([first, second]).verify(journal)

    assert str(excinfo.value) == (
        "Ordered expectation violated.\n"
        "\n"
        "Expected order:\n"
        "  1. deploy('--first')\n"
        "     stdin='payload'\n"
        "     env={'TOKEN': '***'}\n"
        "  2. deploy('--second')\n"
        "\n"
        "Observed order:\n"
        "  1. deploy('--second')\n"
        "  2. deploy('--first')\n"
        "\n"
        "First mismatch:\n"
        "  position 1\n"
        "  expected:\n"
        "    deploy('--first')\n"
        "    stdin='payload'\n"
        "    env={'TOKEN': '***'}\n"
        "  actual:\n"
        "    deploy('--second')\n"
        "    stdin='wrong'\n"
        "    env={'TOKEN': '***'}\n"
        "\n"
        "Reason:\n"
        "  arguments ['--second'] != ['--first']"
    ), "the mismatch section must render the expectation's env keys and stdin"


def test_order_verifier_reports_extra_invocations_without_stdin() -> None:
    """Invocations beyond the ordered sequence are listed without stdin."""
    expectation = Expectation("deploy").in_order()
    journal = [
        _invocation("deploy", "--one"),
        _invocation("deploy", "--two", stdin="payload"),
    ]

    with pytest.raises(UnexpectedCommandError) as excinfo:
        OrderVerifier([expectation]).verify(journal)

    assert str(excinfo.value) == (
        "Unexpected additional invocation.\n"
        "\n"
        "Expected order:\n"
        "  1. deploy()\n"
        "\n"
        "Observed order:\n"
        "  1. deploy('--one')\n"
        "  2. deploy('--two')\n"
        "\n"
        "Unexpected calls:\n"
        "  deploy('--two')"
    ), "invocations beyond the ordered sequence must be listed without stdin"


def test_order_verifier_expands_expected_counts() -> None:
    """An expectation marked for two calls occupies two ordered slots."""
    fetch = Expectation("git").with_args("fetch").times_called(2).in_order()
    status = Expectation("git").with_args("status").in_order()

    journal = [
        _invocation("git", "fetch"),
        _invocation("git", "fetch"),
        _invocation("git", "status"),
    ]

    OrderVerifier([fetch, status]).verify(journal)

    # Failure control: one fetch short must leave an unmet ordered slot.
    with pytest.raises(UnfulfilledExpectationError):
        OrderVerifier([fetch, status]).verify([
            _invocation("git", "fetch"),
            _invocation("git", "status"),
        ])


def test_order_verifier_with_no_ordered_expectations_accepts_anything() -> None:
    """An empty ordered sequence short-circuits verification."""
    OrderVerifier([]).verify([_invocation("deploy", "--anything")])

    # Failure control: the same journal must fail once an ordered expectation
    # is present, so the empty sequence is what makes the case above pass.
    ordered = [Expectation("deploy").with_args("--expected").in_order()]
    with pytest.raises(UnfulfilledExpectationError):
        OrderVerifier(ordered).verify([_invocation("deploy", "--anything")])


def test_order_verifier_treats_zero_count_as_no_ordered_slots() -> None:
    """An ordered expectation for zero calls contributes no slots."""
    never = Expectation("git").with_args("fetch").times_called(0).in_order()

    OrderVerifier([never]).verify([_invocation("git", "status")])

    # Failure control: raising the count to one must make the same journal fail.
    once = Expectation("git").with_args("fetch").times_called(1).in_order()
    with pytest.raises(UnfulfilledExpectationError):
        OrderVerifier([once]).verify([_invocation("git", "status")])
