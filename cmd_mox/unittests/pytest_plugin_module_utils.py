"""Utilities for constructing synthetic pytest modules in plugin tests."""

from __future__ import annotations

import textwrap
import typing as t

PhaseLiteral: t.TypeAlias = t.Literal["RECORD", "REPLAY", "auto_fail"]


def generate_lifecycle_test_module(
    decorator: str,
    expected_phase: PhaseLiteral,
    *,
    should_fail: bool,
) -> str:
    """Return a self-contained test module for lifecycle precedence cases."""
    lines: list[str] = []
    lines.extend(_build_module_imports(expected_phase))
    lines.extend(_build_module_setup(expected_phase))
    lines.extend(_build_decorator_section(decorator))
    lines.extend(_build_test_function(expected_phase, should_fail=should_fail))
    return textwrap.dedent("\n".join(lines))


def _build_module_imports(expected_phase: PhaseLiteral) -> list[str]:
    """Return the import statements required for the generated module."""
    lines = ["import pytest", "from cmd_mox.controller import Phase"]
    if expected_phase != "auto_fail":
        lines.append("from cmd_mox.unittests.conftest import run_subprocess")
    return lines


def _build_module_setup(expected_phase: PhaseLiteral) -> list[str]:
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


def _build_test_function(
    expected_phase: PhaseLiteral,
    *,
    should_fail: bool,
) -> list[str]:
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


__all__ = [
    "PhaseLiteral",
    "_build_auto_fail_test_body",
    "_build_decorator_section",
    "_build_module_imports",
    "_build_module_setup",
    "_build_record_test_body",
    "_build_replay_test_body",
    "_build_test_function",
    "generate_lifecycle_test_module",
]
