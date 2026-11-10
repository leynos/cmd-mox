"""Execute the examples published in CmdMox's user-facing documents.

The executed examples are the behavioural contract: a reader who copies one
into a project should see the same outcome. The parser cases below pin the
rules that stop a new example from escaping that contract unnoticed.
"""

from __future__ import annotations

import json
import re
import typing as typ

import pytest

from cmd_mox.record import FixtureFile
from tests.helpers.documentation_examples import (
    DOCUMENTS,
    ROOT,
    conftest_for,
    describe_failure,
    documented_examples,
    parse_examples,
    run_example,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tests.helpers.documentation_examples import Example

EXAMPLES = documented_examples()
TESTED = [example for example in EXAMPLES if example.kind == "tested"]
FIXTURES = [example for example in EXAMPLES if example.kind == "fixture"]


@pytest.mark.parametrize("example", TESTED, ids=[example.name for example in TESTED])
def test_published_example_behaves_as_documented(
    example: Example, tmp_path: Path
) -> None:
    """Run one published example and require its outcome checks to pass."""
    result = run_example(example, tmp_path, conftest_for(example.path, EXAMPLES))
    assert result.returncode == 0, describe_failure(example, result)


@pytest.mark.parametrize(
    "example", FIXTURES, ids=[example.name for example in FIXTURES]
)
def test_published_fixture_loads(example: Example) -> None:
    """A published recording fixture loads through the current schema."""
    fixture = FixtureFile.from_dict(json.loads(example.code))
    assert fixture.recordings, f"{example.name}: fixture has no recordings"


@pytest.mark.parametrize("path", DOCUMENTS)
def test_every_published_document_exists(path: str) -> None:
    """Each gated document is present, so the gate cannot pass vacuously."""
    assert (ROOT / path).is_file(), f"{path} is registered but missing"


def test_readme_quick_start_is_gated() -> None:
    """The README's first example is executed, not merely published."""
    readme = {example.name for example in EXAMPLES if example.path == "README.md"}
    assert "readme-quick-start" in readme, "README quick start is not gated"


def test_readme_publishes_the_plugin_set_up() -> None:
    """The README's ``conftest.py`` instruction is what enables the plugin."""
    conftest = conftest_for("README.md", EXAMPLES)
    assert "cmd_mox.pytest_plugin" in conftest, "README conftest must enable it"


def test_a_document_without_set_up_inherits_the_readme_conftest() -> None:
    """A guide that omits ``conftest.py`` is tested with the README's."""
    readme = parse_examples(
        "README.md", "<!-- conftest-example: setup -->\n```python\nX = 1\n```\n"
    )
    assert conftest_for("docs/other.md", readme) == "X = 1\n", (
        "a document without its own conftest must inherit the README's"
    )


def test_shell_examples_are_published_but_not_executed() -> None:
    """A marked shell fence is accepted without joining the executed set."""
    text = "<!-- shell-example: install -->\n\n```shell\npip install cmd-mox\n```\n"
    assert not parse_examples("doc.md", text), "shell example was queued to run"


def test_fixture_examples_are_returned_for_loading() -> None:
    """A marked JSON fence is returned with its kind so the suite loads it."""
    text = '<!-- fixture-example: data -->\n```json\n{"a": 1}\n```\n'
    (example,) = parse_examples("doc.md", text)
    assert (example.kind, example.code) == ("fixture", '{"a": 1}\n'), (
        "fixture example must keep its kind and exact body"
    )


def test_illustrative_examples_are_published_but_not_executed() -> None:
    """An illustrative fence in any language is accepted without being run."""
    text = (
        "- `plumbum`: needs a package CmdMox does not depend on.\n"
        "<!-- illustrative-example: plumbum -->\n"
        "```python\nfrom plumbum import local\n```\n"
    )
    assert not parse_examples("doc.md", text), "illustrative example was queued"


def test_a_wrapped_bullet_states_its_reason() -> None:
    """A reason that wraps onto continuation lines still justifies exclusion."""
    text = (
        "- `build`:\n"
        "  needs a prepared runner and a checkout.\n"
        "<!-- illustrative-example: build -->\n"
        "```yaml\n- name: Build\n```\n"
    )
    assert not parse_examples("doc.md", text), "wrapped reason was not accepted"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "<!-- illustrative-example: unexplained -->\n```yaml\n- name: Build\n```\n",
            id="no-prose",
        ),
        pytest.param(
            "Building happens elsewhere.\n"
            "<!-- illustrative-example: build -->\n"
            "```yaml\n- name: Build\n```\n",
            id="name-only-inside-a-longer-word",
        ),
        pytest.param(
            "- `build`:\n<!-- illustrative-example: build -->\n"
            "```yaml\n- name: Build\n```\n",
            id="bullet-names-the-example-without-a-reason",
        ),
        pytest.param(
            "The build example needs a prepared runner.\n"
            "<!-- illustrative-example: build -->\n"
            "```yaml\n- name: Build\n```\n",
            id="reason-away-from-a-bullet",
        ),
    ],
)
def test_illustrative_examples_require_a_documented_reason(text: str) -> None:
    """An exclusion the document's prose does not name fails the gate."""
    with pytest.raises(AssertionError, match="has no documented exclusion reason"):
        parse_examples("doc.md", text)


def test_indented_markers_and_fences_are_recognized() -> None:
    """A tested fence nested in a list item keeps its Python, dedented."""
    text = (
        "  <!-- tested-example: nested -->\n"
        "  ```python\n"
        "  value = 1\n"
        "  assert value == 1\n"
        "  ```\n"
    )
    (example,) = parse_examples("doc.md", text)
    assert example.code == "value = 1\nassert value == 1\n", (
        "an indented tested example must be dedented into valid Python"
    )


def test_a_longer_fence_needs_an_equally_long_closer() -> None:
    """A four-backtick fence is not closed by a three-backtick line inside it."""
    text = (
        "- `workflow`: needs a prepared runner.\n"
        "<!-- illustrative-example: workflow -->\n"
        "````yaml\n"
        "```\n"
        "````\n"
    )
    assert not parse_examples("doc.md", text), "the inner fence closed the example"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param("def test_x():\n    assert 1\n", True, id="pytest-function"),
        pytest.param("assert 1\n", False, id="script"),
        pytest.param(
            "def helper():\n    def test_inner():\n        pass\nassert 1\n",
            False,
            id="nested-test-name-is-not-collected",
        ),
    ],
)
def test_pytest_modules_are_detected_by_top_level_tests(
    code: str, *, expected: bool
) -> None:
    """Only module-level ``test_*`` functions route an example through pytest."""
    (example,) = parse_examples(
        "doc.md", f"<!-- tested-example: probe -->\n```python\n{code}```\n"
    )
    assert example.is_pytest_module is expected, "pytest detection is wrong"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("```shell\npip install cmd-mox\n```\n", id="unmarked-fence"),
        pytest.param("~~~python\nassert True\n~~~\n", id="unmarked-tilde-fence"),
        pytest.param(
            "<!-- shell-example: sneaky -->\n```python\nprint(1)\n```\n",
            id="python-behind-shell-marker",
        ),
        pytest.param(
            "<!-- tested-example: wrong -->\n```shell\nls\n```\n",
            id="shell-behind-tested-marker",
        ),
        pytest.param(
            "<!-- fixture-example: wrong -->\n```python\nassert 1\n```\n",
            id="python-behind-fixture-marker",
        ),
    ],
)
def test_misdeclared_fences_are_rejected(text: str) -> None:
    """Every fence needs a marker whose kind matches its language."""
    with pytest.raises(AssertionError):
        parse_examples("doc.md", text)


@pytest.mark.parametrize(
    ("text", "diagnostic"),
    [
        pytest.param(
            "<!-- tested-example: gap -->\nProse.\n```python\nassert 1\n```\n",
            "doc.md:2: marker is not beside a fence",
            id="marker-separated-by-text",
        ),
        pytest.param(
            "Intro.\n<!-- tested-example: orphan -->\n",
            "doc.md: marker without a fence",
            id="marker-without-fence",
        ),
        pytest.param(
            "<!-- tested-example: open -->\n```python\nassert 1\n",
            "doc.md:2: unterminated fence",
            id="unterminated-fence",
        ),
        pytest.param(
            "<!-- tested-example: quiet -->\n```python\nprint(1)\n```\n",
            "doc.md:2: example has no outcome check",
            id="no-outcome-check",
        ),
        pytest.param(
            (
                "<!-- tested-example: fake -->\n"
                '```python\n# assert x\ns = "assert y"\n```\n'
            ),
            "doc.md:2: example has no outcome check",
            id="assert-only-in-comment-or-string",
        ),
    ],
)
def test_malformed_examples_report_their_location(text: str, diagnostic: str) -> None:
    """Placement and outcome errors name the document and the offending line."""
    with pytest.raises(AssertionError, match=re.escape(diagnostic)):
        parse_examples("doc.md", text)
