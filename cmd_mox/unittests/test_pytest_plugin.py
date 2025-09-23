"""Unit tests for the pytest plugin."""

from __future__ import annotations

import dataclasses as dc
import textwrap
import typing as t

import pytest

from cmd_mox.controller import Phase
from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess

    from cmd_mox.controller import CmdMox


@dc.dataclass(slots=True, frozen=True)
class AutoLifecycleTestCase:
    """Test case data for auto-lifecycle configuration scenarios."""

    config_method: str
    ini_setting: str | None
    cli_args: tuple[str, ...]
    test_decorator: str
    expected_phase: t.Literal["RECORD", "REPLAY", "auto_fail"]
    should_fail: bool


pytest_plugins = ("cmd_mox.pytest_plugin", "pytester")


def test_fixture_basic(
    cmd_mox: CmdMox,
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Fixture yields a CmdMox instance and cleans up."""
    cmd_mox.stub("hello").returns(stdout="hi")
    assert cmd_mox.phase is Phase.REPLAY
    cmd_path = _shim_cmd_path(cmd_mox, "hello")
    result = run([str(cmd_path)])
    assert result.stdout.strip() == "hi"


@pytest.mark.parametrize(
    ("worker_id", "expected_fragment"),
    [
        ("gw99", "gw99"),
        (None, "main"),
    ],
)
def test_worker_prefixes(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    worker_id: str | None,
    expected_fragment: str,
) -> None:
    """Worker ID is reflected in the environment prefix; falls back to 'main'."""
    _run_prefix_scenario(
        pytester,
        monkeypatch,
        worker_id=worker_id,
        expected_fragment=expected_fragment,
    )


def _run_prefix_scenario(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    *,
    worker_id: str | None,
    expected_fragment: str,
) -> None:
    """Execute a minimal test module and assert the shim prefix fragment."""
    if worker_id is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker_id)

    test_module = textwrap.dedent(
        f"""
        import os
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        def test_prefix(cmd_mox):
            cmd_mox.stub('foo').returns(stdout='bar')
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, 'foo'))])
            assert res.stdout.strip() == 'bar'
            shim_dir = os.path.basename(cmd_mox.environment.shim_dir)
            assert '{expected_fragment}' in shim_dir
        """
    )
    pytester.makepyfile(test_module)
    result = pytester.runpytest("-s")
    result.assert_outcomes(passed=1)


def test_missing_invocation_fails_during_teardown(pytester: pytest.Pytester) -> None:
    """Verification failures should fail the test even without explicit calls."""
    test_file = pytester.makepyfile(
        """
        import pytest

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_missing_invocation(cmd_mox):
            cmd_mox.mock("hello").returns(stdout="hi")
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*UnfulfilledExpectationError*"])


def test_verification_error_suppressed_on_test_failure(
    pytester: pytest.Pytester,
) -> None:
    """Primary test failures should mask verification errors."""
    test_file = pytester.makepyfile(
        """
        import pytest

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_failure(cmd_mox):
            cmd_mox.mock("late").returns(stdout="ok")
            assert False
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*assert False*"])


def test_teardown_error_reports_failure(pytester: pytest.Pytester) -> None:
    """Cleanup errors should fail the test with a helpful message."""
    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.controller import CmdMox

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        @pytest.fixture(autouse=True)
        def break_exit(monkeypatch):
            def _boom(self, exc_type, exc, tb):
                raise OSError("kaboom")

            monkeypatch.setattr(CmdMox, "__exit__", _boom)

        def test_cleanup_error(cmd_mox):
            cmd_mox.stub("late").returns(stdout="ok")
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*cmd_mox fixture cleanup failed*"])


@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            AutoLifecycleTestCase(
                config_method="ini_disables",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=(),
                test_decorator="",
                expected_phase="RECORD",
                should_fail=False,
            ),
            id="ini-disables",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="cli_disables",
                ini_setting=None,
                cli_args=("--no-cmd-mox-auto-lifecycle",),
                test_decorator="",
                expected_phase="RECORD",
                should_fail=False,
            ),
            id="cli-disables",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="marker_overrides_ini",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=(),
                test_decorator="@pytest.mark.cmd_mox(auto_lifecycle=True)",
                expected_phase="auto_fail",
                should_fail=True,
            ),
            id="marker-overrides-ini",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="marker_overrides_cli",
                ini_setting=None,
                cli_args=("--no-cmd-mox-auto-lifecycle",),
                test_decorator="@pytest.mark.cmd_mox(auto_lifecycle=True)",
                expected_phase="REPLAY",
                should_fail=False,
            ),
            id="marker-overrides-cli",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="fixture_param_bool",
                ini_setting=None,
                cli_args=(),
                test_decorator=(
                    '@pytest.mark.parametrize("cmd_mox", [False], indirect=True)'
                ),
                expected_phase="RECORD",
                should_fail=False,
            ),
            id="fixture-param-bool",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="fixture_param_dict",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=(),
                test_decorator="\n".join(
                    [
                        "@pytest.mark.parametrize(",
                        '    "cmd_mox", [{"auto_lifecycle": True}], indirect=True',
                        ")",
                    ]
                ),
                expected_phase="REPLAY",
                should_fail=False,
            ),
            id="fixture-param-dict",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="cli_overrides_ini",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=("--cmd-mox-auto-lifecycle",),
                test_decorator="",
                expected_phase="REPLAY",
                should_fail=False,
            ),
            id="cli-overrides-ini",
        ),
    ],
)
def test_auto_lifecycle_configuration(
    pytester: pytest.Pytester,
    test_case: AutoLifecycleTestCase,
) -> None:
    """Exercise lifecycle precedence without duplicating module scaffolding."""
    if test_case.ini_setting:
        pytester.makeini(
            textwrap.dedent(
                f"""
                [pytest]
                {test_case.ini_setting}
                """
            )
        )

    module = _generate_lifecycle_test_module(
        test_case.test_decorator,
        test_case.expected_phase,
        should_fail=test_case.should_fail,
    )
    module = f"# scenario: {test_case.config_method}\n" + module
    test_file = pytester.makepyfile(**{f"test_{test_case.config_method}.py": module})

    run_kwargs: dict[str, t.Any] = {}
    if test_case.cli_args:
        run_kwargs["plugins"] = ("cmd_mox.pytest_plugin",)

    result = pytester.runpytest(*test_case.cli_args, str(test_file), **run_kwargs)

    if test_case.should_fail:
        result.assert_outcomes(passed=1, errors=1)
        result.stdout.fnmatch_lines(["*UnfulfilledExpectationError*"])
    else:
        result.assert_outcomes(passed=1)


def _generate_lifecycle_test_module(
    decorator: str, expected_phase: str, *, should_fail: bool
) -> str:
    """Return a self-contained test module for lifecycle precedence cases."""
    lines = []
    lines.extend(_build_module_imports(expected_phase))
    lines.extend(_build_module_setup(expected_phase))
    lines.extend(_build_decorator_section(decorator))
    lines.extend(_build_test_function(expected_phase, should_fail=should_fail))
    return textwrap.dedent("\n".join(lines))


def _build_module_imports(expected_phase: str) -> list[str]:
    """Return the import statements required for the generated module."""
    lines = ["import pytest", "from cmd_mox.controller import Phase"]
    if expected_phase != "auto_fail":
        lines.append("from cmd_mox.unittests.conftest import run_subprocess")
    return lines


def _build_module_setup(expected_phase: str) -> list[str]:
    """Return module-level setup such as plugin registration and helpers."""
    lines = ["", 'pytest_plugins = ("cmd_mox.pytest_plugin",)', ""]
    if expected_phase != "auto_fail":
        lines.extend(
            [
                "def _shim_cmd_path(mox, name):",
                "    sd = mox.environment.shim_dir",
                "    assert sd is not None",
                "    return sd / name",
                "",
            ]
        )
    return lines


def _build_decorator_section(decorator: str) -> list[str]:
    """Return any decorator lines preceding the generated test function."""
    if not decorator:
        return []
    lines = decorator.splitlines()
    lines.append("")
    return lines


def _build_test_function(expected_phase: str, *, should_fail: bool) -> list[str]:
    """Construct the test function definition and body for the scenario."""
    lines = ["def test_case(cmd_mox):"]
    match expected_phase:
        case "RECORD":
            lines.extend(_build_record_test_body())
        case "REPLAY" if not should_fail:
            lines.extend(_build_replay_test_body())
        case "auto_fail":
            lines.extend(_build_auto_fail_test_body())
        case _:  # pragma: no cover - defensive guard for unexpected parameters
            msg = f"Unsupported expected_phase: {expected_phase}"
            raise ValueError(msg)
    lines.append("")
    return lines


def _build_record_test_body() -> list[str]:
    """Return the assertions and expectations for record-phase scenarios."""
    return [
        "    assert cmd_mox.phase is Phase.RECORD",
        '    cmd_mox.stub("tool").returns(stdout="ok")',
        "    cmd_mox.replay()",
        '    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])',
        '    assert res.stdout.strip() == "ok"',
        "    cmd_mox.verify()",
    ]


def _build_replay_test_body() -> list[str]:
    """Return the test body used when replay begins automatically."""
    return [
        "    assert cmd_mox.phase is Phase.REPLAY",
        '    cmd_mox.stub("tool").returns(stdout="ok")',
        '    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])',
        '    assert res.stdout.strip() == "ok"',
    ]


def _build_auto_fail_test_body() -> list[str]:
    """Return the test body for scenarios expecting verification failure."""
    return [
        "    assert cmd_mox.phase is Phase.REPLAY",
        '    cmd_mox.mock("never-called").returns(stdout="nope")',
    ]


def test_build_module_imports_handles_auto_fail() -> None:
    """Auto-fail scenarios should omit the subprocess helper import."""
    assert _build_module_imports("auto_fail") == [
        "import pytest",
        "from cmd_mox.controller import Phase",
    ]


def test_build_module_imports_includes_helper_for_replay() -> None:
    """Replay scenarios should import the subprocess helper."""
    assert _build_module_imports("REPLAY") == [
        "import pytest",
        "from cmd_mox.controller import Phase",
        "from cmd_mox.unittests.conftest import run_subprocess",
    ]


def test_build_module_setup_includes_helper_when_needed() -> None:
    """Replay modules should provide the shim path helper."""
    assert _build_module_setup("REPLAY") == [
        "",
        'pytest_plugins = ("cmd_mox.pytest_plugin",)',
        "",
        "def _shim_cmd_path(mox, name):",
        "    sd = mox.environment.shim_dir",
        "    assert sd is not None",
        "    return sd / name",
        "",
    ]


def test_build_module_setup_auto_fail_only_registers_plugin() -> None:
    """Auto-fail modules do not need the shim helper."""
    assert _build_module_setup("auto_fail") == [
        "",
        'pytest_plugins = ("cmd_mox.pytest_plugin",)',
        "",
    ]


def test_build_decorator_section_appends_blank_line() -> None:
    """Decorators should retain their blank line separator."""
    assert _build_decorator_section("@pytest.mark.something") == [
        "@pytest.mark.something",
        "",
    ]


def test_build_decorator_section_empty_is_noop() -> None:
    """An empty decorator string should leave the module unchanged."""
    assert _build_decorator_section("") == []


@pytest.mark.parametrize(
    "case",
    [
        (
            "RECORD",
            False,
            [
                "def test_case(cmd_mox):",
                "    assert cmd_mox.phase is Phase.RECORD",
                '    cmd_mox.stub("tool").returns(stdout="ok")',
                "    cmd_mox.replay()",
                '    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])',
                '    assert res.stdout.strip() == "ok"',
                "    cmd_mox.verify()",
                "",
            ],
        ),
        (
            "REPLAY",
            False,
            [
                "def test_case(cmd_mox):",
                "    assert cmd_mox.phase is Phase.REPLAY",
                '    cmd_mox.stub("tool").returns(stdout="ok")',
                '    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])',
                '    assert res.stdout.strip() == "ok"',
                "",
            ],
        ),
        (
            "auto_fail",
            True,
            [
                "def test_case(cmd_mox):",
                "    assert cmd_mox.phase is Phase.REPLAY",
                '    cmd_mox.mock("never-called").returns(stdout="nope")',
                "",
            ],
        ),
    ],
)
def test_build_test_function(case: tuple[str, bool, list[str]]) -> None:
    """The test function builder should mirror the legacy code paths."""
    expected_phase, should_fail, expected_lines = case
    assert (
        _build_test_function(expected_phase, should_fail=should_fail) == expected_lines
    )


def test_build_test_function_raises_for_invalid_phase() -> None:
    """Unexpected lifecycle values should raise the defensive error."""
    with pytest.raises(ValueError, match="Unsupported expected_phase: UNKNOWN"):
        _build_test_function("UNKNOWN", should_fail=False)
