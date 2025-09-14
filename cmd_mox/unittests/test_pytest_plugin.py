"""Unit tests for the pytest plugin."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess

    from cmd_mox.controller import CmdMox


pytest_plugins = ("cmd_mox.pytest_plugin", "pytester")


@pytest.mark.usefixtures("cmd_mox")
def test_fixture_basic(
    cmd_mox: CmdMox,
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Fixture yields a CmdMox instance and cleans up."""
    cmd_mox.stub("hello").returns(stdout="hi")
    cmd_mox.replay()
    cmd_path = _shim_cmd_path(cmd_mox, "hello")
    result = run([str(cmd_path)])
    assert result.stdout.strip() == "hi"
    cmd_mox.verify()


def test_worker_prefix(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker ID is included in the environment prefix."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw99")
    pytester.makepyfile(
        """
        import os
        from cmd_mox.unittests.conftest import run_subprocess
        import pytest

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_worker(cmd_mox):
            cmd_mox.stub('foo').returns(stdout='bar')
            cmd_mox.replay()
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, 'foo'))])
            assert res.stdout.strip() == 'bar'
            assert 'gw99' in os.path.basename(cmd_mox.environment.shim_dir)
            cmd_mox.verify()
        """
    )
    result = pytester.runpytest("-s")
    result.assert_outcomes(passed=1)


def test_default_prefix(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fall back to 'main' when no worker ID is present."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    pytester.makepyfile(
        """
        import os
        from cmd_mox.unittests.conftest import run_subprocess
        import pytest

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_default(cmd_mox):
            cmd_mox.stub('foo').returns(stdout='bar')
            cmd_mox.replay()
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, 'foo'))])
            assert res.stdout.strip() == 'bar'
            assert 'main' in os.path.basename(cmd_mox.environment.shim_dir)
            cmd_mox.verify()
        """
    )
    result = pytester.runpytest("-s")
    result.assert_outcomes(passed=1)
