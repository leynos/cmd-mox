"""Utilities for constructing synthetic pytest modules in plugin tests."""

from __future__ import annotations

import textwrap
import typing as t

PhaseLiteral: t.TypeAlias = t.Literal["RECORD", "REPLAY", "AUTO_FAIL"]

_UNKNOWN_PHASE_ERR = "Unknown phase: {phase}"

_TEST_BODIES: dict[PhaseLiteral, str] = {
    "RECORD": """
        assert cmd_mox.phase is Phase.RECORD
        cmd_mox.stub("tool").returns(stdout="ok")
        cmd_mox.replay()
        res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
        assert res.stdout.strip() == "ok"
        cmd_mox.verify()
    """,
    "REPLAY": """
        assert cmd_mox.phase is Phase.REPLAY
        cmd_mox.stub("tool").returns(stdout="ok")
        res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
        assert res.stdout.strip() == "ok"
    """,
    "AUTO_FAIL": """
        assert cmd_mox.phase is Phase.REPLAY
        cmd_mox.mock("never-called").returns(stdout="nope")
    """,
}


def _format_block(block: str, *, indent: int = 0) -> str:
    """Return a dedented block optionally indented by ``indent`` spaces."""
    normalized = textwrap.dedent(block).strip("\n")
    if not normalized:
        return ""

    normalized = f"{normalized}\n"
    if indent:
        normalized = textwrap.indent(normalized, " " * indent)
    return normalized


def _build_module_prefix(*, include_subprocess_helper: bool) -> str:
    """Compose the module header and optional shim helper."""
    header_lines = [
        "import pytest",
        "from cmd_mox.controller import Phase",
    ]
    if include_subprocess_helper:
        header_lines.append("from cmd_mox.unittests.conftest import run_subprocess")
    header_lines.append('pytest_plugins = ("cmd_mox.pytest_plugin",)')

    prefix = "\n".join(header_lines) + "\n\n"
    if include_subprocess_helper:
        prefix += _format_block(
            """\
            def _shim_cmd_path(mox, name):
                sd = mox.environment.shim_dir
                assert sd is not None
                return sd / name
            """
        )
        prefix += "\n"

    return prefix


def generate_lifecycle_test_module(
    decorator: str,
    expected_phase: PhaseLiteral,
    *,
    expect_auto_fail: bool,
) -> str:
    """Return a synthetic pytest module tuned to a lifecycle scenario.

    ``decorator`` is emitted verbatim (when provided) immediately above the
    generated test so callers can inject parametrization or marks without
    needing to post-process the module text. Setting ``expect_auto_fail`` to
    ``True`` swaps the REPLAY test body for the auto-fail variant and omits the
    subprocess shim helpers; this mirrors how the plugin behaves when a
    lifecycle override expects failure. Passing ``expected_phase`` as
    ``"AUTO_FAIL"`` has the same effect regardless of ``expect_auto_fail``,
    allowing callers to assert the pure auto-fail module layout.
    """
    if expected_phase not in ("RECORD", "REPLAY", "AUTO_FAIL"):
        raise ValueError(_UNKNOWN_PHASE_ERR.format(phase=expected_phase))

    body_key: PhaseLiteral = (
        "AUTO_FAIL"
        if expected_phase == "REPLAY" and expect_auto_fail
        else expected_phase
    )

    uses_subprocess_helper = body_key != "AUTO_FAIL"
    module = _build_module_prefix(include_subprocess_helper=uses_subprocess_helper)

    decorator_block = _format_block(decorator) if decorator else ""
    body_block = _format_block(_TEST_BODIES[body_key], indent=4)

    module += f"{decorator_block}def test_case(cmd_mox):\n"
    module += body_block

    return module


__all__ = ["PhaseLiteral", "generate_lifecycle_test_module"]
