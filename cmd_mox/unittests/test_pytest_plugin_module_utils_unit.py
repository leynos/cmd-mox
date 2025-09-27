"""Unit tests for the pytest plugin module utilities."""

from __future__ import annotations

import textwrap

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
    expected_phase, should_fail, expected_snippets = case
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator="",
        expected_phase=expected_phase,
        should_fail=should_fail,
    )

    for snippet in expected_snippets:
        assert snippet in module_text


def test_generate_module_overrides_replay_body_when_failures_expected() -> None:
    """REPLAY scenarios expecting failure should use the auto-fail body."""
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator="",
        expected_phase="REPLAY",
        should_fail=True,
    )

    assert 'cmd_mox.mock("never-called").returns(stdout="nope")' in module_text
    assert "from cmd_mox.unittests.conftest import run_subprocess" not in module_text


def test_generate_module_includes_decorators_with_trailing_newline() -> None:
    """Decorators should appear immediately above the generated test function."""
    decorator = "@pytest.mark.foo()"
    module_text = plugin_utils.generate_lifecycle_test_module(
        decorator=decorator,
        expected_phase="RECORD",
        should_fail=False,
    )

    expected_block = textwrap.dedent(
        """\
        @pytest.mark.foo()
        def test_case(cmd_mox):
        """
    )
    assert expected_block in module_text
