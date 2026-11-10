"""Unit tests for :mod:`cmd_mox.controller` - environment variable handling."""

from __future__ import annotations

import os
import threading
import typing as typ

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.unittests._env_helpers import require_shim_dir

pytestmark = pytest.mark.requires_unix_sockets

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc
    import subprocess

    from cmd_mox.test_doubles import CommandDouble


def test_mock_with_env_static_response(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mocks with env overrides should verify when caller omits variables."""
    key = "EXPECT_ENV"
    with CmdMox() as mox:
        mox.mock("envcmd").with_env({key: "VALUE"}).returns(stdout="ok")
        mox.replay()

        cmd_path = require_shim_dir(mox.environment) / "envcmd"
        result = run([str(cmd_path)])

        assert result.stdout.strip() == "ok"

        mox.verify()


def test_mock_with_env_rejects_non_string_key() -> None:
    """with_env() should reject non-string keys when used via the DSL."""
    mox = CmdMox()

    with pytest.raises(TypeError, match="Environment variable name must be str"):
        mox.mock("envcmd").with_env({42: "value"})  # type: ignore[arg-type, ty:invalid-argument-type]


def test_response_for_regular_preserves_handler_env_override() -> None:
    """Handler-provided env entries should not be clobbered by expectation env."""

    def handler(inv: Invocation) -> Response:
        """Return a response that sets its own value for the expectation's key."""
        response = Response(stdout="ok")
        response.env["EXPECT_ENV"] = "handler"
        return response

    mox = CmdMox()
    double = mox.stub("envcmd").with_env({"EXPECT_ENV": "expected"}).runs(handler)
    invocation = Invocation(command="envcmd", args=[], stdin="", env={})

    resp = mox._response_for_regular(double, invocation)

    assert invocation.env["EXPECT_ENV"] == "expected"
    assert resp.env["EXPECT_ENV"] == "handler"


def test_response_for_regular_rejects_conflicting_env() -> None:
    """Invocations providing conflicting env should raise an error."""
    key = "EXPECT_ENV"
    mox = CmdMox()
    double = mox.mock("envcmd").with_env({key: "EXPECTED"}).returns(stdout="ok")
    invocation = Invocation(command="envcmd", args=[], stdin="", env={key: "DIFF"})

    with pytest.raises(UnexpectedCommandError, match="conflicting environment"):
        mox._response_for_regular(double, invocation)


def test_response_for_regular_masks_conflicting_secret_env() -> None:
    """The regular path propagates the masked conflict message."""
    key = "DB_PWD"
    provided_value = "invocation-secret"
    mox = CmdMox()
    double = mox.mock("envcmd").with_env({key: "expected-secret"}).returns(stdout="ok")
    invocation = Invocation(
        command="envcmd", args=[], stdin="", env={key: provided_value}
    )

    with pytest.raises(UnexpectedCommandError, match="conflicting environment") as exc:
        mox._response_for_regular(double, invocation)

    message = str(exc.value)
    assert f"{key}='***'" in message, "Sensitive conflict was not masked"
    assert provided_value not in message, "Invocation value leaked into the message"
    assert invocation.env == {key: provided_value}, "Conflict mutated the invocation"


def test_response_for_regular_applies_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_response_for_regular exposes overrides through invocation.env only."""
    key = "SOME_VAR"
    monkeypatch.delenv(key, raising=False)
    mox = CmdMox()
    host_observations: list[str | None] = []

    def handler(invocation: Invocation) -> Response:
        """Return stdout echoing the variable as the handler sees it."""
        host_observations.append(os.environ.get(key))
        return Response(stdout=invocation.env.get(key, ""))

    dbl = mox.stub("demo").with_env({key: "VAL"}).runs(handler)
    inv = Invocation(command="demo", args=[], stdin="", env={})

    assert key not in os.environ
    resp = mox._response_for_regular(dbl, inv)
    assert resp.stdout == "VAL"
    assert host_observations == [None], "handler observed a mutated host environment"
    assert key not in os.environ
    assert resp.env == {key: "VAL"}
    assert inv.env[key] == "VAL"


def test_response_for_regular_applies_env_to_static_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment overrides apply when returning a canned response."""
    key = "STATIC_VAR"
    monkeypatch.delenv(key, raising=False)
    mox = CmdMox()
    dbl = mox.stub("demo").with_env({key: "VAL"}).returns(stdout="ok")
    inv = Invocation(command="demo", args=[], stdin="", env={})

    assert key not in os.environ
    resp = mox._response_for_regular(dbl, inv)
    assert resp.stdout == "ok"
    assert resp.env == {key: "VAL"}
    assert key not in os.environ
    assert inv.env[key] == "VAL"


def test_concurrent_handlers_leave_host_environment_untouched(  # ruff: ignore[too-many-locals,too-many-statements] - the regression needs the full two-thread harness in one scope
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent handler calls must not restore a stale os.environ snapshot."""
    shared_key = "CONCURRENT_SHARED"
    a_key = "CONCURRENT_A_ONLY"
    b_key = "CONCURRENT_B_ONLY"
    caller_a_key = "CONCURRENT_CALLER_A"
    caller_b_key = "CONCURRENT_CALLER_B"
    host_key = "CONCURRENT_HOST"
    for key in (shared_key, a_key, b_key, caller_a_key, caller_b_key):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(host_key, "BEFORE")

    barrier = threading.Barrier(2, timeout=10)
    release_a = threading.Event()
    release_b = threading.Event()
    entered = {"A": threading.Event(), "B": threading.Event()}
    observations: dict[str, dict[str, str | None]] = {}
    responses: dict[str, Response] = {}
    errors: list[BaseException] = []

    def make_handler(
        label: str, own_key: str, release: threading.Event
    ) -> cabc.Callable[[Invocation], Response]:
        """Build a handler that records host state and waits for release."""

        def handler(invocation: Invocation) -> Response:
            """Record host state, synchronise with the peer, then respond."""
            observations[label] = {
                "shared": invocation.env.get(shared_key),
                "own": invocation.env.get(own_key),
                "host_shared": os.environ.get(shared_key),
                "host_own": os.environ.get(own_key),
                "host_key": os.environ.get(host_key),
            }
            entered[label].set()
            barrier.wait()
            release.wait(timeout=10)
            return Response(stdout=label)

        return handler

    def run_worker(label: str, double: CommandDouble, invocation: Invocation) -> None:
        """Dispatch one invocation and collect any failure for the main thread."""
        try:
            responses[label] = mox._response_for_regular(double, invocation)
        except BaseException as exc:  # ruff: ignore[blind-except] - surfaced on the main thread
            errors.append(exc)

    mox = CmdMox()
    double_a = (
        mox
        .stub("concurrent-a")
        .with_env({shared_key: "SHARED", a_key: "A"})
        .runs(make_handler("A", a_key, release_a))
    )
    double_b = (
        mox
        .stub("concurrent-b")
        .with_env({shared_key: "SHARED", b_key: "B"})
        .runs(make_handler("B", b_key, release_b))
    )
    invocation_a = Invocation(
        command="concurrent-a", args=[], stdin="", env={caller_a_key: "CALLER_A"}
    )
    invocation_b = Invocation(
        command="concurrent-b", args=[], stdin="", env={caller_b_key: "CALLER_B"}
    )

    thread_a = threading.Thread(
        target=run_worker, args=("A", double_a, invocation_a), daemon=True
    )
    thread_b = threading.Thread(
        target=run_worker, args=("B", double_b, invocation_b), daemon=True
    )
    try:
        thread_a.start()
        thread_b.start()
        assert entered["A"].wait(timeout=10), "handler A did not start"
        assert entered["B"].wait(timeout=10), "handler B did not start"
        os.environ[host_key] = "AFTER"
        release_a.set()
        thread_a.join(timeout=10)
        assert not thread_a.is_alive(), "handler A did not finish"
        release_b.set()
        thread_b.join(timeout=10)
        assert not thread_b.is_alive(), "handler B did not finish"
    finally:
        release_a.set()
        release_b.set()
        barrier.abort()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

    assert not errors, errors
    assert observations["A"] == {
        "shared": "SHARED",
        "own": "A",
        "host_shared": None,
        "host_own": None,
        "host_key": "BEFORE",
    }
    assert observations["B"] == {
        "shared": "SHARED",
        "own": "B",
        "host_shared": None,
        "host_own": None,
        "host_key": "BEFORE",
    }
    assert invocation_a.env == {
        caller_a_key: "CALLER_A",
        shared_key: "SHARED",
        a_key: "A",
    }
    assert invocation_b.env == {
        caller_b_key: "CALLER_B",
        shared_key: "SHARED",
        b_key: "B",
    }
    assert responses["A"].env == {shared_key: "SHARED", a_key: "A"}
    assert responses["B"].env == {shared_key: "SHARED", b_key: "B"}
    assert os.environ.get(host_key) == "AFTER"
    for key in (shared_key, a_key, b_key, caller_a_key, caller_b_key):
        assert key not in os.environ


def test_raising_handler_leaves_host_environment_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler exception propagates without touching the host environment."""
    key = "RAISING_HANDLER"
    monkeypatch.delenv(key, raising=False)
    original_env = os.environ.copy()
    mox = CmdMox()
    observed: list[str | None] = []

    def handler(invocation: Invocation) -> Response:
        """Record the merged override, then fail."""
        observed.append(invocation.env.get(key))
        raise RuntimeError("boom")

    dbl = mox.stub("demo").with_env({key: "VAL"}).runs(handler)
    inv = Invocation(command="demo", args=[], stdin="", env={})

    with pytest.raises(RuntimeError, match="boom"):
        mox._response_for_regular(dbl, inv)

    assert observed == ["VAL"]
    assert inv.env[key] == "VAL"
    assert os.environ == original_env


@pytest.mark.parametrize("call_replay_before_exception", [True, False])
def test_cmdmox_environment_cleanup_on_exception(
    *,
    call_replay_before_exception: bool,
) -> None:
    """Environment is cleaned even if an error occurs before or after replay."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("fail").returns(stdout="fail")
    mox.__enter__()
    if call_replay_before_exception:
        mox.replay()

    # Environment should differ while the manager is active
    assert os.environ["PATH"] != original_path

    def _boom() -> None:
        """Raise RuntimeError to simulate a failure inside the active environment."""
        raise RuntimeError

    try:
        _boom()
    except RuntimeError:
        pass
    finally:
        if call_replay_before_exception:
            mox.verify()
        mox.__exit__(None, None, None)

    # Ensure PATH is fully restored
    assert os.environ["PATH"] == original_path


def test_context_manager_restores_env_on_exception(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Context manager restores environment even if an exception occurs."""

    class CustomError(Exception):
        """Exception used to trigger cleanup."""

    def run_with_error() -> None:
        """Run the stubbed command inside the context manager, then raise."""
        with mox:
            mox.stub("boom").returns(stdout="oops")
            mox.replay()
            cmd_path = require_shim_dir(mox.environment) / "boom"
            run([str(cmd_path)])
            raise CustomError

    original_env = os.environ.copy()
    mox = CmdMox()
    with pytest.raises(CustomError):
        run_with_error()

    assert os.environ == original_env
