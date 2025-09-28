"""Unit tests for the pytest plugin module utilities."""

from __future__ import annotations

import textwrap
import typing as t

import pytest

from cmd_mox.unittests import pytest_plugin_module_utils as plugin_utils

CaseType = tuple[plugin_utils.PhaseLiteral, bool, tuple[str, ...]]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            (
                "RECORD",
                False,
                (
                    "from cmd_mox.unittests.conftest import run_subprocess",
                    "def _shim_cmd_path",
                    "cmd_mox.phase is Phase.RECORD",
                    "cmd_mox.verify()",
                ),
            ),
            id="record",
        ),
        pytest.param(
            (
                "REPLAY",
                False,
                (
                    "from cmd_mox.unittests.conftest import run_subprocess",
                    "def _shim_cmd_path",
                    "cmd_mox.phase is Phase.REPLAY",
                    'cmd_mox.stub("tool")',
                ),
            ),
            id="replay",
        ),
    ],
)
def test_generate_module_includes_expected_snippets(case: CaseType) -> None:
    """Generated modules should include the imports and body for each phase."""
    expected_phase, expect_auto_fail, expected_snippets = case
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator="",
        expected_phase=expected_phase,
        expect_auto_fail=expect_auto_fail,
    )

    for snippet in expected_snippets:
        assert snippet in module_text


def _assert_auto_fail_module_properties(
    expected_phase: plugin_utils.PhaseLiteral, *, expect_auto_fail: bool
) -> None:
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator="",
        expected_phase=expected_phase,
        expect_auto_fail=expect_auto_fail,
    )

    assert 'cmd_mox.mock("never-called").returns(stdout="nope")' in module_text
    assert "from cmd_mox.unittests.conftest import run_subprocess" not in module_text
    assert "def _shim_cmd_path" not in module_text


def test_generate_module_overrides_replay_body_when_failures_expected() -> None:
    """REPLAY scenarios expecting failure should use the auto-fail body."""
    _assert_auto_fail_module_properties("REPLAY", expect_auto_fail=True)


def test_generate_module_forces_auto_fail_body_without_helpers() -> None:
    """Pure auto-fail modules should omit shim helpers and include failure body."""
    _assert_auto_fail_module_properties("auto_fail", expect_auto_fail=False)


def test_generate_module_includes_decorators_with_trailing_newline() -> None:
    """Decorators should appear immediately above the generated test function."""
    decorator = "@pytest.mark.foo()"
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator=decorator,
        expected_phase="RECORD",
        expect_auto_fail=False,
    )

    expected_block = textwrap.dedent(
        """\
        @pytest.mark.foo()
        def test_case(cmd_mox):
        """
    )
    assert expected_block in module_text


def test_generate_module_includes_decorators_with_leading_trailing_whitespace() -> None:
    """Decorator formatting should be resilient to surrounding whitespace."""
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator="   @pytest.mark.some_marker   ",
        expected_phase="RECORD",
        expect_auto_fail=False,
    )
    assert "@pytest.mark.some_marker" in module_text

    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator="\n@pytest.mark.some_marker\n",
        expected_phase="RECORD",
        expect_auto_fail=False,
    )
    assert "@pytest.mark.some_marker" in module_text


def test_generate_module_preserves_multiline_decorators() -> None:
    """Multi-line decorators should be dedented and placed flush with the test."""
    decorator = """
        @pytest.mark.parametrize(
            "flag",
            [
                True,
                False,
            ],
        )
    """
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator=decorator,
        expected_phase="REPLAY",
        expect_auto_fail=False,
    )

    expected_block = textwrap.dedent(
        """\
        @pytest.mark.parametrize(
            "flag",
            [
                True,
                False,
            ],
        )
        def test_case(cmd_mox):
        """
    )
    assert expected_block in module_text


def test_generate_module_rejects_unknown_phases() -> None:
    """An invalid phase should raise an explicit error for callers."""
    with pytest.raises(ValueError, match=r"Unknown phase: INVALID"):
        plugin_utils.generate_lifecycle_test_module(
            decorator="",
            expected_phase=t.cast("plugin_utils.PhaseLiteral", "INVALID"),
            expect_auto_fail=False,
        )
