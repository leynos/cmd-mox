"""pytest-bdd steps that validate user-facing documentation."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from pytest_bdd import given, then

import cmd_mox
from tests.helpers.docs import extract_marked_block
from tests.helpers.documentation_examples import (
    DOCUMENTS,
    conftest_for,
    describe_failure,
    documented_examples,
    run_example,
)

if typ.TYPE_CHECKING:
    from tests.helpers.documentation_examples import Example

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"


@given("the CmdMox user's guide is available", target_fixture="users_guide_text")
def load_users_guide() -> str:
    """Load the user's guide markdown for assertions.

    Returns
    -------
    str
        The contents of the user's guide markdown file.
    """
    return (DOCS_ROOT / "users-guide.md").read_text(encoding="utf-8")


@then("the user's guide API reference lists all public cmd_mox symbols")
def assert_users_guide_api_reference_complete(users_guide_text: str) -> None:
    """Assert the public API reference mirrors ``cmd_mox.__all__``."""
    api_reference = extract_marked_block(users_guide_text, name="api-reference")

    missing = sorted(
        name for name in cmd_mox.__all__ if f"`{name}`" not in api_reference
    )
    assert not missing, f"Missing API reference entries: {', '.join(missing)}"


@given("the published CmdMox documents", target_fixture="published_documents")
def published_documents() -> tuple[str, ...]:
    """Return the documents whose examples readers are told they can copy.

    Returns
    -------
    tuple[str, ...]
        Repository-relative paths of the gated documents.
    """
    return DOCUMENTS


@then(
    "every code fence in them declares how it is checked",
    target_fixture="published_examples",
)
def assert_every_fence_is_declared(
    published_documents: tuple[str, ...],
) -> list[Example]:
    """Parse the documents; an unmarked or unjustified fence fails the step.

    Returns
    -------
    list[Example]
        The checked examples, for later steps.
    """
    examples = documented_examples(published_documents)
    assert examples, "the published documents hold no checked examples"
    return examples


@then("the README quick start passes when run as a reader would run it")
def assert_readme_quick_start_passes(
    published_examples: list[Example], tmp_path: Path
) -> None:
    """Run the README's first example with its documented ``conftest.py``."""
    (quick_start,) = (
        example
        for example in published_examples
        if example.name == "readme-quick-start"
    )
    result = run_example(
        quick_start, tmp_path, conftest_for(quick_start.path, published_examples)
    )
    assert result.returncode == 0, describe_failure(quick_start, result)
