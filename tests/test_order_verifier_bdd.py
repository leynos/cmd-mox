"""Behavioural tests exercising the order verifier in isolation."""

import shlex
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenario, then, when

from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.verifiers import OrderVerifier

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@pytest.fixture
def ordered_expectations() -> list[Expectation]:
    """Collect expectations that must appear in order."""
    return []


@pytest.fixture
def unordered_expectations() -> list[Expectation]:
    """Collect expectations that are not part of the ordered sequence."""
    return []


@pytest.fixture
def journal() -> list[Invocation]:
    """Accumulate recorded invocations for verification."""
    return []


@pytest.fixture
def verification_context() -> dict[str, Exception | None]:
    """Store verification outcomes for assertions."""
    return {}


@given(
    parsers.cfparse('an ordered expectation for command "{command}" with args "{args}"')
)
def create_ordered_expectation(
    command: str, args: str, ordered_expectations: list[Expectation]
) -> None:
    """Add an ordered expectation for *command* with parsed *args*."""
    expectation = Expectation(command)
    if args:
        expectation.with_args(*shlex.split(args))
    expectation.in_order()
    ordered_expectations.append(expectation)


@given(
    parsers.cfparse(
        'an unordered expectation for command "{command}" with args "{args}"'
    )
)
def create_unordered_expectation(
    command: str, args: str, unordered_expectations: list[Expectation]
) -> None:
    """Record an unordered expectation to mirror setup complexity."""
    expectation = Expectation(command)
    if args:
        expectation.with_args(*shlex.split(args))
    unordered_expectations.append(expectation)


@given(
    parsers.cfparse('the journal contains invocation "{command}" with args "{args}"')
)
def add_invocation(command: str, args: str, journal: list[Invocation]) -> None:
    """Append an invocation for *command* with parsed *args*."""
    invocation = Invocation(
        command=command,
        args=list(shlex.split(args)) if args else [],
        stdin="",
        env={},
    )
    journal.append(invocation)


@when("I validate ordered expectations")
def validate_order(
    ordered_expectations: list[Expectation],
    journal: list[Invocation],
    verification_context: dict[str, Exception | None],
) -> None:
    """Run the order verifier and capture any raised error."""
    verifier = OrderVerifier(ordered_expectations)
    try:
        verifier.verify(journal)
    except Exception as exc:  # noqa: BLE001 - capturing for assertion
        verification_context["error"] = exc
    else:
        verification_context["error"] = None


@then("the ordered verification should succeed")
def assert_order_success(verification_context: dict[str, Exception | None]) -> None:
    """Assert that the verification completed without raising."""
    assert verification_context.get("error") is None


@scenario(
    str(FEATURES_DIR / "order_verifier.feature"),
    "unordered invocation of matching command is ignored",
)
def test_order_verifier_ignores_unordered_invocation() -> None:
    """Order verifier ignores invocations satisfied by unordered expectations."""
    pass
