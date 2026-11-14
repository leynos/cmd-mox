"""Contract test for what the mutmut sandbox must provide.

Mutmut copies ``source_paths``, ``also_copy``, and a fixed set of test
directories into ``mutants/`` before it runs the suite there. A test that
reads a repository file outside that set fails the mutation baseline even
though the source is healthy.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _also_copy() -> list[str]:
    """Return the paths ``[tool.mutmut]`` copies into the sandbox."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["mutmut"]["also_copy"]


def test_the_spelling_policy_is_copied_into_the_sandbox() -> None:
    """The typos rollout test reads the committed local policy from the root.

    Mutation runs from ``mutants/``, so an uncopied policy file turns that
    test into a ``FileNotFoundError`` in every mutation baseline.
    """
    assert "typos.local.toml" in _also_copy(), (
        "the mutation sandbox must copy the committed spelling policy"
    )
