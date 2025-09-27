"""Utilities for constructing synthetic pytest modules in plugin tests."""

from __future__ import annotations

import textwrap
import typing as t

PhaseLiteral: t.TypeAlias = t.Literal["RECORD", "REPLAY", "auto_fail"]

_MODULE_TMPL = """\
import pytest
from cmd_mox.controller import Phase
{extra_import}
pytest_plugins = ("cmd_mox.pytest_plugin",)

{shim_code}{decorator}def test_case(cmd_mox):
{test_body}
"""

_AUTO_FAIL_BODY = """\
    assert cmd_mox.phase is Phase.REPLAY
    cmd_mox.mock("never-called").returns(stdout="nope")
"""

_REPLAY_BODY = """\
    assert cmd_mox.phase is Phase.REPLAY
    cmd_mox.stub("tool").returns(stdout="ok")
    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
    assert res.stdout.strip() == "ok"
"""

_RECORD_BODY = """\
    assert cmd_mox.phase is Phase.RECORD
    cmd_mox.stub("tool").returns(stdout="ok")
    cmd_mox.replay()
    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
    assert res.stdout.strip() == "ok"
    cmd_mox.verify()
"""

_TEST_BODIES: dict[str, str] = {
    "RECORD": _RECORD_BODY,
    "REPLAY": _REPLAY_BODY,
    "auto_fail": _AUTO_FAIL_BODY,
}

_SHIM_HELPER = textwrap.dedent(
    """\
    def _shim_cmd_path(mox, name):
        sd = mox.environment.shim_dir
        assert sd is not None
        return sd / name


"""
)


def generate_lifecycle_test_module(
    decorator: str,
    expected_phase: PhaseLiteral,
    *,
    should_fail: bool,
) -> str:
    """Return a self-contained test module for lifecycle precedence cases."""
    body_key: str = expected_phase
    if expected_phase == "REPLAY" and should_fail:
        body_key = "auto_fail"

    uses_subprocess_helper = body_key != "auto_fail"
    extra_import = (
        "from cmd_mox.unittests.conftest import run_subprocess\n"
        if uses_subprocess_helper
        else ""
    )
    shim_code = _SHIM_HELPER if uses_subprocess_helper else ""

    body = textwrap.indent(textwrap.dedent(_TEST_BODIES[body_key]), "    ")
    decorator_block = f"{decorator.rstrip()}\n" if decorator else ""

    return _MODULE_TMPL.format(
        extra_import=extra_import,
        shim_code=shim_code,
        decorator=decorator_block,
        test_body=body,
    )


__all__ = ["PhaseLiteral", "generate_lifecycle_test_module"]
