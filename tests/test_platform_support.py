"""Tests for platform detection and skip helpers."""

from __future__ import annotations

import typing as t

import pytest

import cmd_mox.platform as platform

if t.TYPE_CHECKING:  # pragma: no cover - used only for type hints
    from _pytest.pytester import Pytester


def test_is_supported_true_for_windows() -> None:
    """Windows is now considered a supported platform."""
    assert platform.unsupported_reason("win32") is None
    assert platform.is_supported("win32") is True


def test_is_supported_true_for_linux() -> None:
    """Linux is currently supported."""
    assert platform.is_supported("linux") is True


def test_override_env_forces_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests can force alternate platforms via the override environment variable."""
    monkeypatch.setattr(
        platform,
        "_UNSUPPORTED_PLATFORMS",
        (("win", "forced block"),),
    )
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")
    assert platform.is_supported() is False
    assert platform.unsupported_reason() == "forced block"


def test_override_env_handles_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown overrides should fall back to the default platform logic."""
    monkeypatch.delenv(platform.PLATFORM_OVERRIDE_ENV, raising=False)
    unknown_platform = "unknown-os"
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, unknown_platform)

    assert platform.is_supported() is True
    assert platform.unsupported_reason() is None


def test_override_env_forces_supported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overrides should allow tests to simulate supported platforms as well."""
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "linux")
    assert platform.is_supported() is True
    assert platform.unsupported_reason() is None


def test_skip_if_unsupported_triggers_pytest_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling ``skip_if_unsupported`` respects the override matrix."""
    reason = "forced block"
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", reason),))
    with pytest.raises(pytest.skip.Exception) as excinfo:
        platform.skip_if_unsupported(platform="win32")
    assert str(excinfo.value) == reason


def test_skip_if_unsupported_allows_custom_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provided reason should override the default skip message."""
    custom_reason = "custom skip message"
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "nope"),))
    with pytest.raises(pytest.skip.Exception) as excinfo:
        platform.skip_if_unsupported(platform="win32", reason=custom_reason)
    assert str(excinfo.value) == custom_reason


def test_skip_if_unsupported_uses_custom_reason_with_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overrides should not stop callers from surfacing custom skip reasons."""
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "blocked"),))
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")
    custom_reason = "custom skip via override"

    with pytest.raises(pytest.skip.Exception) as excinfo:
        platform.skip_if_unsupported(reason=custom_reason)

    assert str(excinfo.value) == custom_reason


def test_skip_if_unsupported_ignores_reason_on_supported_platform() -> None:
    """Providing a reason must not skip when the platform is supported."""
    try:
        platform.skip_if_unsupported(platform="linux", reason="custom skip")
    except pytest.skip.Exception as exc:  # pragma: no cover - indicates a bug
        pytest.fail(f"unexpected skip: {exc}")


def test_skip_if_unsupported_noop_on_supported_platform() -> None:
    """On supported platforms the helper should return quietly."""
    platform.skip_if_unsupported(platform="linux")


def test_cmd_mox_fixture_auto_skips_on_unsupported_platform(
    pytester: Pytester, monkeypatch: pytest.MonkeyPatch
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

    result = pytester.runpytest(str(test_file), "-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*blocked*"])
