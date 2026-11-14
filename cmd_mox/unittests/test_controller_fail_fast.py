"""Unit tests for fail-fast mock mismatch handling."""

from __future__ import annotations

import contextlib
import subprocess
import typing as typ

import pytest

from cmd_mox import Any, Predicate, Regex
from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.unittests._env_helpers import require_shim_dir

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox.test_doubles import CommandDouble

pytestmark = pytest.mark.requires_unix_sockets


@contextlib.contextmanager
def _running_controller(
    *, fail_fast: bool = False, max_journal_entries: int | None = None
) -> cabc.Iterator[CmdMox]:
    """Yield a replaying controller and tear its environment down afterwards."""
    mox = CmdMox(
        fail_fast=fail_fast,
        verify_on_exit=False,
        max_journal_entries=max_journal_entries,
    )
    mox.__enter__()
    try:
        mox.replay()
        yield mox
    finally:
        if mox._entered:
            mox.__exit__(None, None, None)


def _shim(mox: CmdMox, name: str) -> str:
    """Return the shim path for *name*."""
    return str(require_shim_dir(mox.environment) / name)


def _run_shim(
    command: cabc.Sequence[str],
    *,
    check: bool,
    stdin: str | None,
) -> subprocess.CompletedProcess[str]:
    """Run *command* capturing text output, feeding *stdin* when given."""
    options: dict[str, typ.Any] = (
        {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}
    )
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a shim path prepared by the harness
        command,
        capture_output=True,
        text=True,
        check=check,
        **options,
    )


def _run_ok(
    mox: CmdMox,
    name: str,
    *args: str,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke a shim expecting success."""
    return _run_shim([_shim(mox, name), *args], check=True, stdin=stdin)


def _run_failing(
    mox: CmdMox,
    name: str,
    *args: str,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke a shim with ``check=False`` to observe a rejection response."""
    return _run_shim([_shim(mox, name), *args], check=False, stdin=stdin)


def _configure_mismatch(mock: CommandDouble, kind: str) -> None:
    """Apply the expectation that makes *kind* a definite mismatch."""
    match kind:
        case "args":
            mock.with_args("status")
        case "arity":
            mock.with_matching_args(Any())
        case "stdin":
            mock.with_args("status").with_stdin("expected")
        case _:  # pragma: no cover - invalid parametrization
            msg = f"unknown mismatch kind: {kind}"
            raise AssertionError(msg)


def test_default_path_defers_mismatch_to_verify() -> None:
    """Without fail-fast, a mismatched mock still answers with its response."""
    with _running_controller() as mox:
        mox.mock("git").with_args("status").returns(stdout="clean")
        result = _run_ok(mox, "git", "push")
        assert result.returncode == 0
        assert result.stdout.strip() == "clean"
        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    message = str(excinfo.value)
    assert "Unexpected command invocation." in message
    assert "git('status')" in message
    assert "git('push')" in message


def test_fail_fast_answers_mismatch_with_failure() -> None:
    """Fail-fast rejects a definite mismatch and records it for verification."""
    with _running_controller(fail_fast=True) as mox:
        mock = mox.mock("git").with_args("status").returns(stdout="clean")
        result = _run_failing(mox, "git", "push")

        assert result.returncode == 1
        assert result.stdout == ""
        assert "No mock expectation matches: git push" in result.stderr
        assert "arguments ['push'] != ['status']" in result.stderr
        assert len(mock.invocations) == 1
        assert [inv.command for inv in mox.journal] == ["git"]

        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    message = str(excinfo.value)
    assert "Unexpected command invocation." in message
    assert "git('status')" in message
    assert "git('push')" in message
    assert "arguments ['push'] != ['status']" in message


def test_fail_fast_does_not_run_handler() -> None:
    """A rejected call never reaches the configured handler."""
    handler_calls: list[Invocation] = []

    def handler(invocation: Invocation) -> Response:
        """Record the invocation and return a response."""
        handler_calls.append(invocation)
        return Response(stdout="ran")

    with _running_controller(fail_fast=True) as mox:
        mox.mock("git").with_args("status").runs(handler)
        result = _run_failing(mox, "git", "push")
        assert result.returncode == 1
        assert handler_calls == []
        with pytest.raises(UnexpectedCommandError):
            mox.verify()


@pytest.mark.parametrize(
    ("kind", "args", "stdin", "fragment"),
    [
        pytest.param(
            "args",
            ("push",),
            None,
            "arguments ['push'] != ['status']",
            id="literal-args",
        ),
        pytest.param(
            "arity",
            ("a", "b"),
            None,
            "expected 1 args but got 2",
            id="matcher-arity",
        ),
        pytest.param(
            "stdin",
            ("status",),
            "secret",
            "stdin does not match the expected value",
            id="literal-stdin",
        ),
    ],
)
def test_fail_fast_rejects_definite_mismatches(
    kind: str,
    args: tuple[str, ...],
    stdin: str | None,
    fragment: str,
) -> None:
    """Literal args, matcher arity, and literal stdin are definite mismatches."""
    with _running_controller(fail_fast=True) as mox:
        mock = mox.mock("git").returns(stdout="ok")
        _configure_mismatch(mock, kind)
        result = _run_failing(mox, "git", *args, stdin=stdin)

        assert result.returncode == 1
        assert fragment in result.stderr
        assert len(mock.invocations) == 1

        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    assert fragment in str(excinfo.value)


@pytest.mark.parametrize(
    "matcher",
    [
        pytest.param(Predicate(lambda value: value == "ok"), id="predicate"),
        pytest.param(Regex(r"^ok$"), id="regex"),
    ],
)
def test_fail_fast_defers_callback_matchers(
    matcher: cabc.Callable[[str], bool],
) -> None:
    """Predicate and regex rejections stay deferred to verification."""
    with _running_controller(fail_fast=True) as mox:
        mox.mock("tool").with_matching_args(matcher).returns(stdout="ran")
        result = _run_ok(mox, "tool", "no")
        assert result.returncode == 0
        assert result.stdout.strip() == "ran"
        with pytest.raises(UnexpectedCommandError):
            mox.verify()


def test_fail_fast_defers_excess_calls() -> None:
    """A call beyond the expected count is answered, then reported by verify."""
    with _running_controller(fail_fast=True) as mox:
        mox.mock("tool").returns(stdout="ok")
        first = _run_ok(mox, "tool")
        second = _run_ok(mox, "tool")
        assert first.returncode == 0
        assert second.returncode == 0
        assert second.stdout.strip() == "ok"
        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    assert "Unexpected additional invocation." in str(excinfo.value)


def test_fail_fast_defers_ordering_failures() -> None:
    """Out-of-order calls are answered, then reported by verify."""
    with _running_controller(fail_fast=True) as mox:
        mox.mock("first").with_args("a").returns(stdout="one").in_order()
        mox.mock("second").with_args("b").returns(stdout="two").in_order()
        _run_ok(mox, "second", "b")
        _run_ok(mox, "first", "a")
        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    assert "Ordered expectation violated." in str(excinfo.value)


def test_fail_fast_allows_matching_calls() -> None:
    """A matching call still receives the configured response and verifies."""
    with _running_controller(fail_fast=True) as mox:
        mox.mock("git").with_args("status").returns(stdout="clean")
        result = _run_ok(mox, "git", "status")
        assert result.returncode == 0
        assert result.stdout.strip() == "clean"
        mox.verify()


def test_fail_fast_applies_expectation_environment() -> None:
    """Environment constraints are ignored by the early check but still applied."""
    with _running_controller(fail_fast=True) as mox:
        mox.mock("envcmd").with_args("status").with_env({"HELLO": "WORLD"}).returns(
            stdout="done"
        )
        result = _run_ok(mox, "envcmd", "status")
        assert result.returncode == 0
        assert result.stdout.strip() == "done"
        mox.verify()

    assert mox.journal[0].env["HELLO"] == "WORLD"


def test_fail_fast_redacts_sensitive_arguments() -> None:
    """The rejection diagnostic and verification error hide sensitive option values."""
    with _running_controller(fail_fast=True) as mox:
        mox.mock("deploy").with_args("--expected").returns(stdout="ok")
        result = _run_failing(mox, "deploy", "--token", "supersecret")

        assert result.returncode == 1
        assert "supersecret" not in result.stderr
        assert "--token <redacted>" in result.stderr

        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    message = str(excinfo.value)
    reason = message.split("Reason:", 1)[1]
    assert "supersecret" not in message
    assert "<redacted>" in reason
    assert "<redacted>" in message.split("Actual:", 1)[1]


def test_fail_fast_verification_error_never_echoes_stdin() -> None:
    """The fail-fast verification error omits expected and actual stdin content."""
    with _running_controller(fail_fast=True) as mox:
        mox.mock("git").with_args("status").with_stdin("expected-secret").returns(
            stdout="ok"
        )
        _run_failing(mox, "git", "status", stdin="actual-secret")

        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    message = str(excinfo.value)
    assert "expected-secret" not in message
    assert "actual-secret" not in message


def test_fail_fast_rejection_survives_journal_eviction() -> None:
    """A bounded journal cannot hide a rejected call from verification."""
    with _running_controller(fail_fast=True, max_journal_entries=1) as mox:
        mox.mock("git").with_args("status").returns(stdout="clean")
        mox.stub("ok").returns(stdout="ok")
        _run_failing(mox, "git", "push")
        _run_ok(mox, "ok")

        assert [inv.command for inv in mox.journal] == ["ok"]
        with pytest.raises(UnexpectedCommandError) as excinfo:
            mox.verify()

    assert "git('push')" in str(excinfo.value)


def test_fail_fast_leaves_stubs_and_spies_unchanged() -> None:
    """Stubs and spies keep their permissive behaviour under fail-fast."""
    with _running_controller(fail_fast=True) as mox:
        mox.stub("stubcmd").with_args("expected").returns(stdout="stub")
        mox.spy("spycmd").returns(stdout="spy")
        stub_result = _run_ok(mox, "stubcmd", "other")
        spy_result = _run_ok(mox, "spycmd", "other")
        assert stub_result.stdout.strip() == "stub"
        assert spy_result.stdout.strip() == "spy"
        mox.verify()


def test_fail_fast_is_keyword_only() -> None:
    """The fail-fast option cannot be passed positionally."""
    positional = False
    with pytest.raises(TypeError):
        CmdMox(positional)  # type: ignore[call-arg, ty:too-many-positional-arguments]


def _run_body_failure() -> None:
    """Run a rejected shim inside a controller whose body raises."""
    mox = CmdMox(fail_fast=True)
    mox.mock("git").with_args("status").returns(stdout="clean")
    with mox:
        mox.replay()
        _run_ok(mox, "git", "push")


def test_body_failure_remains_primary() -> None:
    """A body failure is not replaced by the verification error it triggers."""
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _run_body_failure()

    assert excinfo.value.returncode == 1
