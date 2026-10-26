"""Tests for platform detection and skip helpers."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox import platform

pytest_plugins = ("pytester",)

if typ.TYPE_CHECKING:  # pragma: no cover - used only for type hints
    import collections.abc as cabc

    from _pytest.pytester import Pytester, RunResult


def test_is_supported_true_for_windows() -> None:
    """Windows is now considered a supported platform."""
    assert platform.unsupported_reason("win32") is None, "Assertion failed"
    assert platform.is_supported("win32") is True, "Assertion failed"


def test_is_supported_true_for_linux() -> None:
    """Linux is currently supported."""
    assert platform.is_supported("linux") is True, "Assertion failed"


def test_override_env_forces_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests can force alternate platforms via the override environment variable."""
    monkeypatch.setattr(
        platform,
        "_UNSUPPORTED_PLATFORMS",
        (("win", "forced block"),),
    )
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")
    assert platform.is_supported() is False, "Assertion failed"
    assert platform.unsupported_reason() == "forced block", "Assertion failed"


def test_override_env_handles_unknown_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown overrides should fall back to the default platform logic."""
    monkeypatch.delenv(platform.PLATFORM_OVERRIDE_ENV, raising=False)
    unknown_platform = "unknown-os"
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, unknown_platform)

    assert platform.is_supported() is True, "Assertion failed"
    assert platform.unsupported_reason() is None, "Assertion failed"


def test_override_env_forces_supported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overrides should allow tests to simulate supported platforms as well."""
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "linux")
    assert platform.is_supported() is True, "Assertion failed"
    assert platform.unsupported_reason() is None, "Assertion failed"


def test_skip_if_unsupported_triggers_pytest_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling ``skip_if_unsupported`` respects the override matrix."""
    reason = "forced block"
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", reason),))
    with pytest.raises(pytest.skip.Exception) as excinfo:
        platform.skip_if_unsupported(platform="win32")
    assert str(excinfo.value) == reason, "Assertion failed"


def test_skip_if_unsupported_allows_custom_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provided reason should override the default skip message."""
    custom_reason = "custom skip message"
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "nope"),))
    with pytest.raises(pytest.skip.Exception) as excinfo:
        platform.skip_if_unsupported(platform="win32", reason=custom_reason)
    assert str(excinfo.value) == custom_reason, "Assertion failed"


def test_skip_if_unsupported_uses_custom_reason_with_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overrides should not stop callers from surfacing custom skip reasons."""
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "blocked"),))
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")
    custom_reason = "custom skip via override"

    with pytest.raises(pytest.skip.Exception) as excinfo:
        platform.skip_if_unsupported(reason=custom_reason)

    assert str(excinfo.value) == custom_reason, "Assertion failed"


def test_skip_if_unsupported_ignores_reason_on_supported_platform() -> None:
    """Providing a reason must not skip when the platform is supported."""
    try:
        platform.skip_if_unsupported(platform="linux", reason="custom skip")
    except pytest.skip.Exception as exc:  # pragma: no cover - indicates a bug
        pytest.fail(f"unexpected skip: {exc}")


def test_cmd_mox_fixture_auto_skips_on_unsupported_platform(
    pytester: Pytester,
    monkeypatch: pytest.MonkeyPatch,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """The pytest fixture should skip automatically when support is absent."""
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "blocked"),))
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")
    test_file = pytester.makepyfile(
        """
        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_auto_skip(cmd_mox):
            raise AssertionError("fixture should skip before running")
        """
    )

    result = run_nested_pytester("-rs", test_path=test_file)
    result.assert_outcomes(passed=0, skipped=1, errors=0, failed=0)
    result.stdout.fnmatch_lines(["*blocked*"])
    for output in (result.stdout, result.stderr):
        output.no_fnmatch_line("*FileNotFoundError*")
        output.no_fnmatch_line("*ERROR at setup*")


@pytest.mark.requires_unix_sockets
def test_cmd_mox_fixture_runs_on_supported_platform(
    pytester: Pytester,
    monkeypatch: pytest.MonkeyPatch,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """The fixture performs native IPC when its platform is supported."""
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "blocked"),))
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "linux")
    test_file = pytester.makepyfile(
        """
        import os
        import subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_supported(cmd_mox):
            cmd_mox.stub("hello").returns(stdout="works")
            shim_dir = cmd_mox.environment.shim_dir
            assert shim_dir is not None
            shim_name = "hello.cmd" if os.name == "nt" else "hello"
            result = subprocess.run(
                [str(shim_dir / shim_name)],
                capture_output=True,
                check=True,
                text=True,
            )
            assert result.stdout.strip() == "works"
        """
    )

    result = run_nested_pytester(test_path=test_file)
    result.assert_outcomes(passed=1, skipped=0, errors=0, failed=0)


def test_nested_pytester_anchors_cwd_and_configuration(
    pytester: Pytester,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """Nested runs use the invocation package root and pytester's ini file."""
    expected_ini = pytester.path / "tox.ini"
    test_file = pytester.makepyfile(
        f"""
        from pathlib import Path

        def test_nested_roots(request):
            assert Path("cmd_mox").is_dir()
            assert request.config.inipath == Path({str(expected_ini)!r})
        """
    )

    result = run_nested_pytester(test_path=test_file)
    result.assert_outcomes(passed=1, errors=0)
