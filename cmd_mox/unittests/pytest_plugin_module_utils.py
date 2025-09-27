"""Utilities for constructing synthetic pytest modules in plugin tests."""

from __future__ import annotations

import textwrap
import typing as t

PhaseLiteral: t.TypeAlias = t.Literal["RECORD", "REPLAY", "auto_fail"]

_UNKNOWN_PHASE_ERR = "Unknown phase"

_TEST_BODIES: dict[str, str] = {
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
    "auto_fail": """
        assert cmd_mox.phase is Phase.REPLAY
        cmd_mox.mock("never-called").returns(stdout="nope")
    """,
}


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
    ``"auto_fail"`` has the same effect regardless of ``expect_auto_fail``,
    allowing callers to assert the pure auto-fail module layout.
    """
    if expected_phase not in ("RECORD", "REPLAY", "auto_fail"):
        raise ValueError(_UNKNOWN_PHASE_ERR)

    body_key = (
        "auto_fail"
        if expected_phase == "REPLAY" and expect_auto_fail
        else expected_phase
    )

    uses_subprocess_helper = body_key != "auto_fail"
    decorator_block = (
        textwrap.dedent(decorator).rstrip("\n") + "\n" if decorator else ""
    )

    header_lines = [
        "import pytest",
        "from cmd_mox.controller import Phase",
    ]
    if uses_subprocess_helper:
        header_lines.append("from cmd_mox.unittests.conftest import run_subprocess")
    header_lines.append('pytest_plugins = ("cmd_mox.pytest_plugin",)')
    module = "\n".join(header_lines) + "\n\n"

    if uses_subprocess_helper:
        module += textwrap.dedent(
            """\
            def _shim_cmd_path(mox, name):
                sd = mox.environment.shim_dir
                assert sd is not None
                return sd / name


            """
        )

    module += f"{decorator_block}def test_case(cmd_mox):\n"
    module += textwrap.indent(textwrap.dedent(_TEST_BODIES[body_key]), "    ")

    return module


__all__ = ["PhaseLiteral", "generate_lifecycle_test_module"]
