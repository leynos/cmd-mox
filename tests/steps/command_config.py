# ruff: noqa: S101
"""pytest-bdd steps that configure command doubles and behaviours."""

from __future__ import annotations

import os
import shlex
import typing as t

from pytest_bdd import given, parsers

from cmd_mox.comparators import Any, Contains, IsA, Predicate, Regex, StartsWith
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Response
from tests.helpers.parameters import CommandOutput, EnvVar, decode_placeholders

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    import pytest

    from cmd_mox.controller import CmdMox
    from cmd_mox.ipc import Invocation


@given(parsers.cfparse('the command "{cmd}" is stubbed to return "{text}"'))
def stub_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a stubbed command."""
    mox.stub(cmd).returns(stdout=text)


def _stub_command_full_impl(mox: CmdMox, cmd: str, output: CommandOutput) -> None:
    """Configure a stubbed command using aggregated output parameters."""
    mox.stub(cmd).returns(
        stdout=output.stdout,
        stderr=output.stderr,
        exit_code=output.exit_code,
    )


@given(
    parsers.cfparse(
        'the command "{cmd}" is stubbed to return stdout "{stdout}" '
        'stderr "{stderr}" exit code {code:d}'
    )
)
def stub_command_full(
    mox: CmdMox, cmd: str, stdout: str, stderr: str, code: int
) -> None:
    """Configure a stubbed command with explicit streams and exit code."""
    output = CommandOutput(stdout=stdout, stderr=stderr, exit_code=code)
    _stub_command_full_impl(mox, cmd, output)


@given(parsers.cfparse('the command "{cmd}" is stubbed to run a handler'))
def stub_runs(mox: CmdMox, cmd: str) -> None:
    """Configure a stub with a dynamic handler."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        assert invocation.command == cmd
        return ("handled", "", 0)

    mox.stub(cmd).runs(handler)


@given(parsers.cfparse('the command "{cmd}" is mocked to return "{text}"'))
def mock_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a mocked command."""
    mox.mock(cmd).returns(stdout=text)


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked to return "{text}" with comparator args'
    )
)
def mock_with_comparator_args(mox: CmdMox, cmd: str, text: str) -> None:
    """Mock command using various comparators for argument matching."""
    mox.mock(cmd).with_matching_args(
        Any(),
        IsA(int),
        Regex(r"^foo\d+$"),
        Contains("bar"),
        StartsWith("baz"),
        Predicate(str.isupper),
    ).returns(stdout=text)


@given(parsers.cfparse('the matcher list for "{cmd}" disappears during matching'))
def matcher_list_disappears(
    monkeypatch: pytest.MonkeyPatch, mox: CmdMox, cmd: str
) -> None:
    """Simulate matchers being cleared mid-validation."""
    expectation = mox.mocks[cmd].expectation
    original_validate = Expectation._validate_matchers

    def tampered(self: Expectation, args: list[str]) -> bool:
        if self is expectation:
            self.match_args = None
        return original_validate(self, args)

    monkeypatch.setattr(Expectation, "_validate_matchers", tampered)


@given(
    parsers.re(
        r'the command "(?P<cmd>[^"]+)" is mocked to return "(?P<text>[^"]+)" '
        r"times (?P<count>\d+)"
    )
)
def mock_command_times(mox: CmdMox, cmd: str, text: str, count: str) -> None:
    """Configure a mocked command with an expected call count using times()."""
    expectation = mox.mock(cmd).returns(stdout=text)
    expectation.times(int(count))


@given(
    parsers.re(
        r'the command "(?P<cmd>[^"]+)" is mocked to return "(?P<text>[^"]+)" '
        r"times called (?P<count>\d+)"
    )
)
def mock_command_times_called(mox: CmdMox, cmd: str, text: str, count: str) -> None:
    """Configure a mocked command with an expected call count using times_called()."""
    expectation = mox.mock(cmd).returns(stdout=text)
    expectation.times_called(int(count))


@given(parsers.cfparse('the command "{cmd}" is spied to return "{text}"'))
def spy_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a spied command that returns canned stdout."""
    mox.spy(cmd).returns(stdout=text)


@given(parsers.cfparse('the command "{cmd}" is spied to passthrough'))
def spy_passthrough(mox: CmdMox, cmd: str) -> None:
    """Configure a command spy that forwards to the real executable."""
    mox.spy(cmd).passthrough()


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked with args "{args}" returning "{text}" in order'
    )
)
def mock_with_args_in_order(mox: CmdMox, cmd: str, args: str, text: str) -> None:
    """Configure an ordered mock with arguments."""
    decoded = decode_placeholders(args)
    mox.mock(cmd).with_args(*shlex.split(decoded)).returns(stdout=text).in_order()


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked with args "{args}" returning "{text}" any order'
    )
)
def mock_with_args_any_order(mox: CmdMox, cmd: str, args: str, text: str) -> None:
    """Configure an unordered mock with arguments."""
    decoded = decode_placeholders(args)
    mox.mock(cmd).with_args(*shlex.split(decoded)).returns(stdout=text).any_order()


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked with args "{args}" returning "{text}"'
    )
)
def mock_with_args_default_order(mox: CmdMox, cmd: str, args: str, text: str) -> None:
    """Configure a mock with arguments using default ordering."""
    decoded = decode_placeholders(args)
    mox.mock(cmd).with_args(*shlex.split(decoded)).returns(stdout=text)


@given(parsers.cfparse('the command "{cmd}" is stubbed with env var "{var}"="{val}"'))
def stub_with_env(mox: CmdMox, cmd: str, var: str, val: str) -> None:
    """Stub command that outputs an injected env variable."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        return (os.environ.get(var, ""), "", 0)

    mox.stub(cmd).with_env({var: val}).runs(handler)


def _mock_with_env_returns_impl(mox: CmdMox, cmd: str, env: EnvVar, text: str) -> None:
    """Configure a mock that injects environment variables before returning."""
    mox.mock(cmd).with_env({env.name: env.value}).returns(stdout=text)


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked with env var "{var}"="{val}" returning "{text}"'
    )
)
def mock_with_env_returns(mox: CmdMox, cmd: str, var: str, val: str, text: str) -> None:
    """Mock command returning a canned response with injected environment."""
    env = EnvVar(name=var, value=val)
    _mock_with_env_returns_impl(mox, cmd, env, text)


@given(parsers.cfparse('the command "{cmd}" seeds shim env var "{var}"="{val}"'))
def stub_seeds_shim_env(mox: CmdMox, cmd: str, var: str, val: str) -> None:
    """Stub command that injects an environment override for future shims."""

    def handler(_: Invocation) -> Response:
        return Response(env={var: val})

    mox.stub(cmd).runs(handler)


def _stub_expect_and_seed_env_impl(
    mox: CmdMox,
    cmd: str,
    expected: EnvVar,
    seed: EnvVar,
) -> None:
    """Stub that validates one env var before seeding another."""

    def handler(invocation: Invocation) -> Response:
        actual = invocation.env.get(expected.name)
        if actual != expected.value:
            msg = (
                "expected shim env "
                f"{expected.name!r} to equal {expected.value!r} but got {actual!r}"
            )
            raise AssertionError(msg)
        return Response(env={seed.name: seed.value})

    mox.stub(cmd).runs(handler)


@given(
    parsers.cfparse(
        'the command "{cmd}" expects shim env var "{expected}"="{value}" '
        'and seeds "{var}"="{val}"'
    )
)
def stub_expect_and_seed_env(
    mox: CmdMox, cmd: str, expected: str, value: str, var: str, val: str
) -> None:
    """Stub that validates an inherited env var before injecting another."""
    expected_env = EnvVar(name=expected, value=value)
    seed_env = EnvVar(name=var, value=val)
    _stub_expect_and_seed_env_impl(mox, cmd, expected_env, seed_env)


def _stub_records_merged_env_impl(
    mox: CmdMox,
    cmd: str,
    first: EnvVar,
    second: EnvVar,
) -> None:
    """Stub that asserts merged shim environment values."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        actual_first = invocation.env.get(first.name)
        actual_second = invocation.env.get(second.name)
        if actual_first != first.value:
            msg = (
                "expected shim env "
                f"{first.name!r} to equal {first.value!r} but got {actual_first!r}"
            )
            raise AssertionError(msg)
        if actual_second != second.value:
            msg = (
                "expected shim env "
                f"{second.name!r} to equal {second.value!r} but got {actual_second!r}"
            )
            raise AssertionError(msg)
        return (f"{actual_first}+{actual_second}", "", 0)

    mox.stub(cmd).runs(handler)


@given(
    parsers.cfparse(
        'the command "{cmd}" records shim env vars "{first}"="{first_val}" '
        'and "{second}"="{second_val}"'
    )
)
def stub_records_merged_env(
    mox: CmdMox, cmd: str, first: str, first_val: str, second: str, second_val: str
) -> None:
    """Stub that asserts merged shim environment values."""
    first_env = EnvVar(name=first, value=first_val)
    second_env = EnvVar(name=second, value=second_val)
    _stub_records_merged_env_impl(mox, cmd, first_env, second_env)


@given(parsers.cfparse('the command "{cmd}" requires env var "{var}"="{val}"'))
def command_requires_env(mox: CmdMox, cmd: str, var: str, val: str) -> None:
    """Attach an environment requirement to an existing double."""
    for collection in (mox.mocks, mox.stubs, mox.spies):
        double = collection.get(cmd)
        if double is not None:
            double.expectation.with_env({var: val})
            return

    msg = f"Command {cmd!r} has not been registered"
    raise AssertionError(msg)
