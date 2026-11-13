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


_HELPER_NAMES = ("is_supported", "unsupported_reason", "skip_if_unsupported")
_PLATFORM_WARNING = r"received the deprecated 'platform' argument"
_OVERRIDE_WARNING = (
    rf"consulted the deprecated {platform.PLATFORM_OVERRIDE_ENV} environment"
)


@pytest.fixture(autouse=True)
def clear_platform_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from an inherited platform override."""
    monkeypatch.delenv(platform.PLATFORM_OVERRIDE_ENV, raising=False)


def _call_helper(helper_name: str, platform_name: str | None = None) -> object:
    """Invoke a public platform helper, passing *platform_name* when given."""
    helper = getattr(platform, helper_name)
    if helper_name == "skip_if_unsupported":
        return helper(platform=platform_name)
    return helper(platform_name)


def _assert_supported_platform_does_not_skip() -> None:
    """Fail rather than skip when a supported platform is evaluated."""
    try:
        platform.skip_if_unsupported(platform="linux", reason="custom skip")
    except pytest.skip.Exception as exc:  # pragma: no cover - indicates a bug
        pytest.fail(f"unexpected skip: {exc}")


def _deprecation_warnings(record: pytest.WarningsRecorder) -> list[typ.Any]:
    """Return the deprecation warnings captured by *record*."""
    return [
        warning
        for warning in record.list
        if issubclass(warning.category, DeprecationWarning)
    ]


def _assert_single_deprecation(
    record: pytest.WarningsRecorder, helper_name: str
) -> str:
    """Assert exactly one deprecation warning names *helper_name*."""
    assert len(record) == 1, f"expected one warning, got {len(record)}"
    warning = record[0]
    assert issubclass(warning.category, DeprecationWarning), "wrong category"
    message = str(warning.message)
    assert helper_name in message, "warning should name the public helper"
    assert "deprecated" in message, "warning should state the deprecation"
    assert "support classification" in message, "warning should bound the effect"
    assert "shim generation" in message, "warning should exclude shim changes"
    assert "IPC transport" in message, "warning should exclude transport changes"
    assert Path(warning.filename).name == Path(__file__).name, (
        "warning should be attributed to the caller"
    )
    return message


def _assert_override_classification(*, supported: bool, reason: str | None) -> None:
    """Assert both helpers classify the deprecated override consistently."""
    with pytest.warns(DeprecationWarning, match=_OVERRIDE_WARNING):
        assert platform.is_supported() is supported, (
            "is_supported disagreed with the override classification"
        )
    with pytest.warns(DeprecationWarning, match=_OVERRIDE_WARNING):
        assert platform.unsupported_reason() == reason, (
            "unsupported_reason disagreed with the override classification"
        )


def _assert_platform_skip(expected_reason: str, *, reason: str | None = None) -> None:
    """Assert an unsupported platform skips with *expected_reason*."""
    with (
        pytest.warns(DeprecationWarning, match=_PLATFORM_WARNING),
        pytest.raises(pytest.skip.Exception) as excinfo,
    ):
        platform.skip_if_unsupported(platform="win32", reason=reason)
    assert str(excinfo.value) == expected_reason, "unexpected skip reason"


def test_is_supported_true_for_windows() -> None:
    """Windows is a supported platform."""
    with pytest.warns(DeprecationWarning, match=_PLATFORM_WARNING):
        assert platform.unsupported_reason("win32") is None, "Assertion failed"
    with pytest.warns(DeprecationWarning, match=_PLATFORM_WARNING):
        assert platform.is_supported("win32") is True, "Assertion failed"


def test_is_supported_true_for_linux() -> None:
    """Linux is a supported platform."""
    with pytest.warns(DeprecationWarning, match=_PLATFORM_WARNING):
        assert platform.is_supported("linux") is True, "Assertion failed"


def test_override_env_forces_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deprecated override still classifies an alternate platform."""
    monkeypatch.setattr(
        platform,
        "_UNSUPPORTED_PLATFORMS",
        (("win", "forced block"),),
    )
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")
    _assert_override_classification(supported=False, reason="forced block")


def test_override_env_handles_unknown_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognised override matches no table prefix and is supported."""
    unknown_platform = "unknown-os"
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, unknown_platform)

    _assert_override_classification(supported=True, reason=None)


def test_override_env_forces_supported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deprecated override still classifies supported platforms."""
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "linux")
    _assert_override_classification(supported=True, reason=None)


def test_skip_if_unsupported_triggers_pytest_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling ``skip_if_unsupported`` respects the classification table."""
    reason = "forced block"
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", reason),))
    _assert_platform_skip(reason)


def test_skip_if_unsupported_allows_custom_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provided reason should override the default skip message."""
    custom_reason = "custom skip message"
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "nope"),))
    _assert_platform_skip(custom_reason, reason=custom_reason)


def test_skip_if_unsupported_uses_custom_reason_with_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overrides should not stop callers from surfacing custom skip reasons."""
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "blocked"),))
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")
    custom_reason = "custom skip via override"

    with (
        pytest.warns(DeprecationWarning, match=_OVERRIDE_WARNING),
        pytest.raises(pytest.skip.Exception) as excinfo,
    ):
        platform.skip_if_unsupported(reason=custom_reason)

    assert str(excinfo.value) == custom_reason, "Assertion failed"


def test_skip_if_unsupported_ignores_reason_on_supported_platform() -> None:
    """Providing a reason must not skip when the platform is supported."""
    with pytest.warns(DeprecationWarning, match=_PLATFORM_WARNING):
        _assert_supported_platform_does_not_skip()


@pytest.mark.parametrize("helper_name", _HELPER_NAMES)
def test_explicit_platform_warns_once(
    helper_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit platform argument emits exactly one deprecation warning."""
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", ())

    with pytest.warns(DeprecationWarning, match=_PLATFORM_WARNING) as record:
        _call_helper(helper_name, "linux")

    message = _assert_single_deprecation(record, helper_name)
    assert "'platform'" in message, "warning should identify the argument"
    assert platform.PLATFORM_OVERRIDE_ENV not in message, (
        "the override was never consulted"
    )


@pytest.mark.parametrize("helper_name", _HELPER_NAMES)
def test_override_env_warns_once(
    helper_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consulted override emits exactly one deprecation warning."""
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "linux")

    with pytest.warns(DeprecationWarning, match=_OVERRIDE_WARNING) as record:
        _call_helper(helper_name)

    message = _assert_single_deprecation(record, helper_name)
    assert platform.PLATFORM_OVERRIDE_ENV in message, (
        "warning should identify the environment variable"
    )


@pytest.mark.parametrize("helper_name", _HELPER_NAMES)
def test_no_warning_without_deprecated_inputs(
    helper_name: str, recwarn: pytest.WarningsRecorder
) -> None:
    """Omitting both deprecated inputs emits no deprecation warning."""
    _call_helper(helper_name)

    assert not _deprecation_warnings(recwarn), "no deprecation warning was expected"


@pytest.mark.parametrize("helper_name", _HELPER_NAMES)
def test_empty_override_emits_no_warning(
    helper_name: str,
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
) -> None:
    """An empty override is not consulted and emits no warning."""
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "")

    _call_helper(helper_name)

    assert not _deprecation_warnings(recwarn), "an empty override should not warn"


def test_explicit_platform_masks_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truthy explicit platform decides the result and only it warns."""
    monkeypatch.setattr(
        platform,
        "_UNSUPPORTED_PLATFORMS",
        (("win", "blocked on windows"), ("lin", "blocked on linux")),
    )
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")

    with pytest.warns(DeprecationWarning, match=_PLATFORM_WARNING) as record:
        assert platform.unsupported_reason("linux") == "blocked on linux", (
            "the explicit platform should decide the classification"
        )

    message = _assert_single_deprecation(record, "unsupported_reason")
    assert "'platform'" in message, "the argument warning should fire"
    assert platform.PLATFORM_OVERRIDE_ENV not in message, (
        "a masked override should not warn"
    )


def test_skip_if_unsupported_override_skip_uses_table_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consulted override can still drive a skip with the table reason."""
    monkeypatch.setattr(platform, "_UNSUPPORTED_PLATFORMS", (("win", "blocked"),))
    monkeypatch.setenv(platform.PLATFORM_OVERRIDE_ENV, "win32")

    with (
        pytest.warns(DeprecationWarning, match=_OVERRIDE_WARNING) as record,
        pytest.raises(pytest.skip.Exception) as excinfo,
    ):
        platform.skip_if_unsupported()

    _assert_single_deprecation(record, "skip_if_unsupported")
    assert str(excinfo.value) == "blocked", "the table reason should propagate"


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
    # The nested run can also report warnings from ambient coverage tooling, so
    # assert our warning's presence rather than an exact warning total.
    result.assert_outcomes(passed=0, skipped=1, errors=0, failed=0)
    result.stdout.fnmatch_lines(["*DeprecationWarning*", "*blocked*"])
    for output in (result.stdout, result.stderr):
        output.no_fnmatch_line("*FileNotFoundError*")
        output.no_fnmatch_line("*ERROR at setup*")


@pytest.mark.requires_unix_sockets
def test_cmd_mox_fixture_runs_on_supported_platform(
    pytester: Pytester,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """The fixture performs native IPC when its platform is supported."""
    # The classification table is empty, so the host is supported as-is.
    test_file = pytester.makepyfile(
        """
        import os
        import subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_supported(cmd_mox):
            cmd_mox.stub("hello").returns(stdout="works")
            shim_dir = cmd_mox.environment.shim_dir
            assert shim_dir is not None, "the fixture did not provide a shim directory"
            shim_name = "hello.cmd" if os.name == "nt" else "hello"
            result = subprocess.run(
                [str(shim_dir / shim_name)],
                capture_output=True,
                check=True,
                text=True,
            )
            assert result.stdout.strip() == "works", (
                "the shim did not produce the expected stub output"
            )
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
            assert Path.cwd() == expected_invocation_dir, (
                "the nested run did not retain the outer invocation directory"
            )
            assert (package_root / "cmd_mox").is_dir(), (
                "the expected package directory is absent from the checkout root"
            )
            assert Path(cmd_mox.__file__).resolve().parent == (
                package_root / "cmd_mox"
            ), (
                "cmd_mox was imported from outside the checkout package root"
            )
            if expected_invocation_dir == package_root:
                assert Path("cmd_mox").is_dir(), (
                    "the process cwd does not contain the checkout package"
                )
            assert request.config.inipath == Path({str(expected_ini)!r}), (
                "the nested run did not use pytester's isolated ini file"
            )
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
    with monkeypatch.context() as test_scope:
        mutmut_configuration = sys.modules.get("mutmut.configuration")
        if mutmut_configuration is None:
            mutmut_config = types.SimpleNamespace(source_paths=[Path("cmd_mox")])
            configuration_module = types.ModuleType("mutmut.configuration")
            configuration_module.__dict__["Config"] = types.SimpleNamespace(
                get=lambda: mutmut_config
            )
            test_scope.setitem(
                sys.modules, "mutmut.configuration", configuration_module
            )
        else:
            config_factory = vars(mutmut_configuration)["Config"]
            mutmut_config = config_factory.get()
            test_scope.setattr(mutmut_config, "source_paths", [Path("cmd_mox")])

        original_source_paths = list(mutmut_config.source_paths)

        def assert_anchored_source_paths(session: pytest.Session) -> None:
            """Assert mutmut source paths are absolute for the nested session."""
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


def test_nested_pytester_anchors_each_mutmut_source_path(
    pytester: Pytester,
    monkeypatch: pytest.MonkeyPatch,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """Anchor every relative path and leave an absolute path unchanged."""
    package_root = Path(__file__).resolve().parent.parent
    absolute_path = package_root / "examples"
    configured_paths = [Path("cmd_mox"), Path("tests"), absolute_path]
    with monkeypatch.context() as test_scope:
        mutmut_config = types.SimpleNamespace(source_paths=list(configured_paths))
        configuration_module = types.ModuleType("mutmut.configuration")
        configuration_module.__dict__["Config"] = types.SimpleNamespace(
            get=lambda: mutmut_config
        )
        test_scope.setitem(sys.modules, "mutmut.configuration", configuration_module)
        original_source_paths = list(mutmut_config.source_paths)

        def assert_each_path_is_anchored(session: pytest.Session) -> None:
            """Assert relative paths are anchored and the absolute one is kept."""
            del session
            assert mutmut_config.source_paths == [
                package_root / "cmd_mox",
                package_root / "tests",
                absolute_path,
            ], "each relative path should be anchored and absolute paths kept"

        probe = types.SimpleNamespace(pytest_sessionstart=assert_each_path_is_anchored)
        test_file = pytester.makepyfile("def test_nested():\n    assert True\n")
        result = run_nested_pytester(test_path=test_file, plugins=(probe,))
        result.assert_outcomes(passed=1, errors=0)
        assert mutmut_config.source_paths == original_source_paths, (
            "the original source paths should be restored after the nested run"
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
