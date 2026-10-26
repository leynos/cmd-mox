"""Tests for platform detection and skip helpers."""

from __future__ import annotations

import sys
import types
import typing as typ
from pathlib import Path

import pytest

from cmd_mox import platform

pytest_plugins = ("pytester",)

if typ.TYPE_CHECKING:  # pragma: no cover - used only for type hints
    import collections.abc as cabc

    from _pytest.pytester import Pytester, RunResult

    class MutmutConfig(typ.Protocol):
        """Expose the source paths used by mutmut's cached config."""

        source_paths: list[Path]


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
    request: pytest.FixtureRequest,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """Nested runs retain the invocation cwd and use pytester's ini file."""
    expected_ini = pytester.path / "tox.ini"
    expected_invocation_dir = Path(request.config.invocation_params.dir).resolve()
    package_root = Path(__file__).resolve().parent.parent
    test_file = pytester.makepyfile(
        f"""
        from pathlib import Path

        import cmd_mox

        expected_invocation_dir = Path({str(expected_invocation_dir)!r})
        package_root = Path({str(package_root)!r})

        def test_nested_roots(request):
            assert Path.cwd() == expected_invocation_dir
            assert (package_root / "cmd_mox").is_dir()
            assert Path(cmd_mox.__file__).resolve().parent == package_root / "cmd_mox"
            if expected_invocation_dir == package_root:
                assert Path("cmd_mox").is_dir()
            assert request.config.inipath == Path({str(expected_ini)!r})
        """
    )

    result = run_nested_pytester(test_path=test_file)
    result.assert_outcomes(passed=1, errors=0)


def test_nested_pytester_temporarily_anchors_mutmut_source_paths(
    pytester: Pytester,
    monkeypatch: pytest.MonkeyPatch,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """Anchor mutmut's cached source paths only during nested pytest."""
    package_root = Path(__file__).resolve().parent.parent
    mutmut_configuration = sys.modules.get("mutmut.configuration")
    if mutmut_configuration is None:
        mutmut_config = types.SimpleNamespace(source_paths=[Path("cmd_mox")])
        configuration_module = types.ModuleType("mutmut.configuration")
        configuration_module.__dict__["Config"] = types.SimpleNamespace(
            get=lambda: mutmut_config
        )
        monkeypatch.setitem(sys.modules, "mutmut.configuration", configuration_module)
    else:
        config_factory = vars(mutmut_configuration)["Config"]
        mutmut_config = config_factory.get()
        monkeypatch.setattr(mutmut_config, "source_paths", [Path("cmd_mox")])

    original_source_paths = list(mutmut_config.source_paths)

    def assert_anchored_source_paths(session: pytest.Session) -> None:
        del session
        assert mutmut_config.source_paths == [package_root / "cmd_mox"], (
            "mutmut source paths should be absolute during the nested run"
        )

    probe = types.SimpleNamespace(pytest_sessionstart=assert_anchored_source_paths)
    test_file = pytester.makepyfile("def test_nested():\n    assert True\n")
    result = run_nested_pytester(test_path=test_file, plugins=(probe,))
    result.assert_outcomes(passed=1, errors=0)
    assert mutmut_config.source_paths == original_source_paths, (
        "mutmut source paths should be restored after the nested run"
    )


@pytest.fixture(scope="module")
def relative_mutmut_source_paths() -> cabc.Iterator[MutmutConfig]:
    """Expose relative mutmut paths before a test's autouse fixtures run.

    Yields
    ------
    MutmutConfig
        The cached configuration with a relative package source path.
    """
    module_name = "mutmut.configuration"
    original_module = sys.modules.get(module_name)
    config_factory = (
        None if original_module is None else vars(original_module).get("Config")
    )
    is_fake_module = config_factory is None

    if is_fake_module:
        mutmut_config = types.SimpleNamespace(source_paths=[Path("cmd_mox")])
        configuration_module = types.ModuleType(module_name)
        configuration_module.__dict__["Config"] = types.SimpleNamespace(
            get=lambda: mutmut_config
        )
        sys.modules[module_name] = configuration_module
        original_source_paths = None
    else:
        mutmut_config = config_factory.get()
        original_source_paths = mutmut_config.source_paths
        mutmut_config.source_paths = [Path("cmd_mox")]

    try:
        yield mutmut_config
    finally:
        if original_source_paths is not None:
            mutmut_config.source_paths = original_source_paths
        if is_fake_module:
            if original_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = original_module


def test_mutmut_source_paths_stay_anchored_when_test_changes_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative_mutmut_source_paths: MutmutConfig,
) -> None:
    """Keep mutmut source paths valid through test-local cwd changes."""
    expected_source_paths = [Path(__file__).resolve().parent.parent / "cmd_mox"]
    assert relative_mutmut_source_paths.source_paths == expected_source_paths, (
        "mutmut source paths should be absolute during each test"
    )

    monkeypatch.chdir(tmp_path)

    assert relative_mutmut_source_paths.source_paths == expected_source_paths, (
        "changing cwd must not make mutmut source paths point into tmp_path"
    )
