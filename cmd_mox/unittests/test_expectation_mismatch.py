"""Tests for deciding and describing expectation mismatches."""

from __future__ import annotations

import pytest

from cmd_mox import Any, Regex
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation


def test_explain_mismatch_reports_first_failing_position() -> None:
    """An earlier failing predicate outranks a later unequal literal."""
    expectation = Expectation(
        "git",
        match_args=[Regex(r"^ok$"), "clone"],
    )

    reason = expectation.explain_mismatch(Invocation("git", ["no", "pull"], "", {}))

    assert reason.startswith("arg[0]"), "positions are reported in order"
    assert "arg[1]" not in reason, "a later literal must not mask position 0"


def test_explain_mismatch_hides_predicate_failure_details_at_secret_positions() -> None:
    """A raising predicate must not echo its value or pattern at a secret."""

    def reject(value: str) -> bool:
        """Raise with the value so a leak would be visible in the reason."""
        raise ValueError(value)

    expectation = Expectation("deploy", match_args=["--token", reject])

    reason = expectation.explain_mismatch(
        Invocation("deploy", ["--token", "actual-secret"], "", {})
    )

    assert reason == "arg[1]='<redacted>' predicate raised ValueError"
    assert "actual-secret" not in reason


@pytest.mark.parametrize(
    ("expectation", "invocation", "expected"),
    [
        pytest.param(
            Expectation("git").with_args("status"),
            Invocation("git", ["push"], "", {}),
            "arguments ['push'] != ['status']",
            id="literal-args",
        ),
        pytest.param(
            Expectation("git").with_matching_args(lambda _: True),
            Invocation("git", ["a", "b"], "", {}),
            "expected 1 args but got 2",
            id="matcher-arity",
        ),
        pytest.param(
            Expectation("git").with_matching_args("status", Any()),
            Invocation("git", ["push", "origin"], "", {}),
            "arg[0]='push' != 'status'",
            id="literal-matcher",
        ),
        pytest.param(
            Expectation("git").with_stdin("expected"),
            Invocation("git", [], "actual", {}),
            "stdin does not match the expected value",
            id="literal-stdin",
        ),
        pytest.param(
            Expectation("git").with_stdin(lambda _: False),
            Invocation("git", [], "actual", {}),
            None,
            id="stdin-predicate",
        ),
        pytest.param(
            Expectation("git").with_matching_args(lambda _: False),
            Invocation("git", ["value"], "", {}),
            None,
            id="matcher-rejection",
        ),
        pytest.param(
            Expectation("git").with_env({"KEY": "value"}),
            Invocation("git", [], "", {}),
            None,
            id="env-constraint",
        ),
        pytest.param(
            Expectation("git"),
            Invocation("git", ["anything"], "anything", {}),
            None,
            id="unconstrained",
        ),
    ],
)
def test_definite_mismatch_reason_only_rejects_certain_mismatches(
    expectation: Expectation,
    invocation: Invocation,
    expected: str | None,
) -> None:
    """Only callback-free, definite mismatches produce an early reason."""
    assert expectation.definite_mismatch_reason(invocation) == expected


def _fail_on_call(value: str) -> bool:
    """Fail the test if the early check invokes this predicate."""
    pytest.fail(f"definite_mismatch_reason called a predicate with {value!r}")


def test_definite_mismatch_reason_never_calls_predicates() -> None:
    """Argument and stdin predicates stay untouched by the early check."""
    stdin_expectation = Expectation("git").with_stdin(_fail_on_call)
    assert (
        stdin_expectation.definite_mismatch_reason(Invocation("git", [], "data", {}))
        is None
    )

    args_expectation = Expectation("git").with_matching_args(_fail_on_call)
    assert (
        args_expectation.definite_mismatch_reason(Invocation("git", ["value"], "", {}))
        is None
    )


def test_definite_mismatch_reason_arity_check_never_calls_predicates() -> None:
    """An arity rejection is decided without calling the argument predicate."""
    expectation = Expectation("git").with_matching_args(_fail_on_call)

    reason = expectation.definite_mismatch_reason(Invocation("git", ["a", "b"], "", {}))

    assert reason == "expected 1 args but got 2"


def test_definite_mismatch_reason_skips_predicate_beside_matching_literal() -> None:
    """A matching literal does not justify evaluating the next predicate."""
    expectation = Expectation("git").with_matching_args("status", _fail_on_call)

    reason = expectation.definite_mismatch_reason(
        Invocation("git", ["status", "value"], "", {})
    )

    assert reason is None


def test_definite_mismatch_reason_redacts_arguments() -> None:
    """Rendered arguments hide sensitive option values."""
    expectation = Expectation("deploy").with_args("--expected")
    invocation = Invocation("deploy", ["--token", "supersecret"], "", {})

    reason = expectation.definite_mismatch_reason(invocation)

    assert reason is not None
    assert "supersecret" not in reason
    assert "<redacted>" in reason


def test_definite_mismatch_reason_masks_expected_values_at_actual_positions() -> None:
    """Expected literal arguments are masked where the actual call holds a secret."""
    expectation = Expectation("deploy").with_args("--option", "expected-secret")
    invocation = Invocation("deploy", ["--token", "actual-secret"], "", {})

    reason = expectation.definite_mismatch_reason(invocation)

    assert reason is not None
    assert "expected-secret" not in reason
    assert "actual-secret" not in reason


def test_definite_mismatch_reason_masks_actual_value_under_a_different_option() -> None:
    """The expected option name also marks the actual value as a credential."""
    expectation = Expectation("deploy").with_args("--token", "credential-value")
    invocation = Invocation("deploy", ["--option", "credential-value"], "", {})

    reason = expectation.definite_mismatch_reason(invocation)

    assert reason == "arguments ['--option', '<redacted>'] != ['--token', '<redacted>']"


def test_definite_mismatch_reason_redacts_masked_matcher_values() -> None:
    """A predicate option matcher must not unmask the expected token value."""
    expectation = Expectation("deploy").with_matching_args(Any(), "expected-secret")
    invocation = Invocation("deploy", ["--token", "actual-secret"], "", {})

    reason = expectation.definite_mismatch_reason(invocation)

    assert reason == "arg[1]='<redacted>' != '<redacted>'"


def test_definite_mismatch_reason_masks_inline_credential_matcher() -> None:
    """An inline credential matcher masks the bare value the call passed."""
    expectation = Expectation("deploy").with_matching_args("--token=supersecret")
    invocation = Invocation("deploy", ["supersecret"], "", {})

    reason = expectation.definite_mismatch_reason(invocation)

    assert reason == "arg[0]='<redacted>' != '--token=<redacted>'"
