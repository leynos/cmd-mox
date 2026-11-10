"""Parse, validate, and run the examples published in CmdMox's documentation.

The gate follows the model used by the cuprum project: every fenced block in a
published document carries a marker comment that declares how the suite treats
it.

``tested-example``
    A Python fence the suite executes. A fence that defines ``test_*``
    functions runs under pytest with the CmdMox plugin enabled, exactly as a
    reader's suite would run it; any other fence runs as a script. Either way
    it must contain an ``assert`` so a passing run proves an outcome.
``conftest-example``
    A Python fence showing how to enable the plugin. The suite writes it as
    the ``conftest.py`` beside every pytest-style example from the same
    document, so the published set-up instruction is itself exercised.
``fixture-example``
    A JSON fence holding a recording fixture. The suite loads it with
    ``FixtureFile.from_dict`` so the published schema cannot drift from the
    code.
``shell-example``
    A shell fence (an installation command, for example) published for
    copying and never run.
``illustrative-example``
    A fence in any language whose dependencies or side effects make execution
    unsuitable. The document must say why, in a bullet that names it.

An unmarked fence fails the gate, so a new example cannot silently escape
testing.
"""

from __future__ import annotations

import ast
import dataclasses as dc
import os
import re
import subprocess
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    "README.md",
    "docs/users-guide.md",
    "docs/v0-3-0-migration-guide.md",
)
MARKER = re.compile(
    r"^\s*<!-- (tested|conftest|fixture|illustrative|shell)-example: "
    r"([a-z0-9-]+) -->\s*$"
)
# Each executed or validated kind accepts exactly one fence language.
LANGUAGES = {
    "tested": "python",
    "conftest": "python",
    "fixture": "json",
    "shell": "shell",
}
# Kinds whose bodies the suite runs, writes, or loads.
CHECKED = frozenset({"tested", "conftest", "fixture"})
ILLUSTRATIVE = "illustrative"
# Both CommonMark fence characters open a block, so neither can hide an
# example from marker validation.
FENCE_DELIMITERS = ("```", "~~~")
# Variables that would leak the outer pytest session into a child run.
_INHERITED_PREFIXES = ("PYTEST_", "CMOX_", "CMD_MOX_")
# A child pytest run that collects nothing exits with this status.
_NO_TESTS_COLLECTED = 5


class ExampleGateError(AssertionError):
    """Raised when a published document breaks the example-marking rules."""


def _fail(message: str) -> typ.NoReturn:
    """Raise :class:`ExampleGateError` carrying ``message``.

    Raises
    ------
    ExampleGateError
        Always.
    """
    raise ExampleGateError(message)


def _require(condition: object, message: str) -> None:
    """Fail the gate with ``message`` unless ``condition`` holds."""
    if not condition:
        _fail(message)


@dc.dataclass(frozen=True, slots=True)
class Example:
    """One marked example: its document, name, kind, and fenced body."""

    path: str
    name: str
    kind: str
    code: str

    @property
    def is_pytest_module(self) -> bool:
        """Whether the example defines module-level pytest tests."""
        tree = ast.parse(self.code)
        return any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
            for node in tree.body
        )


def _has_outcome_assertion(code: str) -> bool:
    """Return whether ``code`` contains an executable ``assert`` statement."""
    return any(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(code)))


def _dedent_body(lines: list[str], indent: str) -> str:
    """Return fenced lines with up to the fence's indentation removed."""
    width = len(indent)
    dedented = (line[min(width, len(line) - len(line.lstrip(" "))) :] for line in lines)
    return "\n".join(dedented) + "\n"


def _opening_delimiter(opening: str) -> str:
    """Return the full backtick or tilde run that opens a fence."""
    marker = opening[0]
    return marker * (len(opening) - len(opening.lstrip(marker)))


def _indent_columns(line: str) -> int:
    """Return the width of ``line``'s leading whitespace in CommonMark columns."""
    columns = 0
    for char in line:
        match char:
            case " ":
                columns += 1
            case "\t":
                columns += 4 - columns % 4
            case _:
                break
    return columns


def _is_closing_fence(line: str, delimiter: str, indent: str) -> bool:
    """Return whether ``line`` closes a fence opened by ``delimiter``.

    CommonMark lets a closer use a longer run than the opener, and lets it sit
    up to three columns beyond its container's indentation; anything deeper is
    indented code and therefore example content.

    Returns
    -------
    bool
        Whether ``line`` terminates the fence.
    """
    stripped = line.strip()
    if len(stripped) < len(delimiter) or set(stripped) != {delimiter[0]}:
        return False
    return _indent_columns(line) <= _indent_columns(indent) + 3


def _check_opening(path: str, number: int, opening: str, kind: str) -> str:
    """Validate a fence's opening line for ``kind``.

    Returns
    -------
    str
        The backtick or tilde run that opened the fence.
    """
    delimiter = _opening_delimiter(opening)
    if kind == ILLUSTRATIVE:
        _require(
            opening[len(delimiter) :].strip(),
            f"{path}:{number}: illustrative fence must declare a language",
        )
    else:
        _require(
            opening == f"{delimiter}{LANGUAGES[kind]}",
            f"{path}:{number}: expected {LANGUAGES[kind]} fence for {kind} example",
        )
    return delimiter


def _consume_fence(
    path: str, lines: list[str], index: int, marker: tuple[str, str]
) -> tuple[int, Example | None]:
    """Validate the marked fence opening at ``index`` and consume it.

    Returns
    -------
    tuple[int, Example | None]
        The index of the first line after the closing fence, and the example
        the suite must check, if the fence holds one.
    """
    kind, name = marker
    raw_opening = lines[index]
    indent = raw_opening[: len(raw_opening) - len(raw_opening.lstrip())]
    delimiter = _check_opening(path, index + 1, raw_opening.strip(), kind)
    start = index + 1
    end = next(
        (
            number
            for number in range(start, len(lines))
            if _is_closing_fence(lines[number], delimiter, indent)
        ),
        None,
    )
    if end is None:
        _fail(f"{path}:{start}: unterminated fence")
    if kind not in CHECKED:
        return end + 1, None
    code = _dedent_body(lines[start:end], indent)
    if kind == "tested":
        _require(
            _has_outcome_assertion(code),
            f"{path}:{start}: example has no outcome check",
        )
    return end + 1, Example(path=path, name=name, kind=kind, code=code)


def _names_example(prose: str, name: str) -> bool:
    """Return whether ``prose`` mentions ``name`` as a whole token."""
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", prose) is not None


def _exclusion_bullets(prose: str) -> list[str]:
    """Return each Markdown bullet, with wrapped continuation lines joined."""
    bullets: list[str] = []
    current: list[str] = []
    for line in prose.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            if current:
                bullets.append(" ".join(current))
            current = [stripped]
        elif current and line.startswith((" ", "\t")):
            current.append(stripped)
        elif current:
            bullets.append(" ".join(current))
            current = []
    if current:
        bullets.append(" ".join(current))
    return bullets


def _exclusion_reason(bullet: str, name: str) -> str:
    """Return a bullet's text without its code spans or the example name."""
    text = re.sub(r"`[^`]*`", "", bullet[2:])
    return re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", "", text)


def _states_exclusion(prose: str, name: str) -> bool:
    """Return whether a bullet names ``name`` and says why it is excluded."""
    return any(
        _names_example(bullet, name)
        and re.search(r"[A-Za-z]{3,}", _exclusion_reason(bullet, name))
        for bullet in _exclusion_bullets(prose)
    )


def parse_examples(path: str, text: str) -> list[Example]:
    """Return a document's checked examples, rejecting uncovered fences.

    Parameters
    ----------
    path : str
        Document path used in diagnostics.
    text : str
        Markdown source of the document.

    Returns
    -------
    list[Example]
        The tested, conftest, and fixture examples, in document order.
    """
    examples: list[Example] = []
    prose: list[str] = []
    illustrative: list[tuple[str, int]] = []
    lines = text.splitlines()
    pending: tuple[str, str] | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = MARKER.fullmatch(line)
        if marker is not None:
            _require(pending is None, f"{path}:{index + 1}: marker without a fence")
            pending = (marker.group(1), marker.group(2))
            if pending[0] == ILLUSTRATIVE:
                illustrative.append((pending[1], index + 1))
        elif line.lstrip().startswith(FENCE_DELIMITERS):
            if pending is None:
                _fail(f"{path}:{index + 1}: unmarked fence")
            index, found = _consume_fence(path, lines, index, pending)
            if found is not None:
                examples.append(found)
            pending = None
            continue
        else:
            _require(
                pending is None or not line,
                f"{path}:{index + 1}: marker is not beside a fence",
            )
            prose.append(line)
        index += 1
    _require(pending is None, f"{path}: marker without a fence")
    prose_text = "\n".join(prose)
    for name, line_number in illustrative:
        _require(
            _states_exclusion(prose_text, name),
            f"{path}:{line_number}: illustrative example {name!r} has no "
            "documented exclusion reason",
        )
    return examples


def documented_examples(documents: cabc.Iterable[str] = DOCUMENTS) -> list[Example]:
    """Load every checked example across the published documents.

    Returns
    -------
    list[Example]
        Examples from every document, with names unique across the set.
    """
    examples: list[Example] = []
    for path in documents:
        text = (ROOT / path).read_text(encoding="utf-8")
        examples.extend(parse_examples(path, text))
    names = [example.name for example in examples]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    _require(not duplicates, f"duplicate examples: {duplicates}")
    return examples


def conftest_for(path: str, examples: cabc.Sequence[Example]) -> str:
    """Return the ``conftest.py`` a pytest-style example from ``path`` uses.

    A document that publishes its own set-up instruction is tested against
    it; one that does not inherits the README's, which every reader meets
    first.

    Returns
    -------
    str
        The body of the governing ``conftest-example``.

    Raises
    ------
    ExampleGateError
        If no document in the chain publishes a ``conftest-example``.
    """
    candidates = [example for example in examples if example.kind == "conftest"]
    for source in (path, "README.md"):
        found = [example.code for example in candidates if example.path == source]
        if found:
            _require(len(found) == 1, f"{source}: more than one conftest example")
            return found[0]
    msg = f"{path}: no conftest example governs its pytest examples"
    raise ExampleGateError(msg)


def _child_environment() -> dict[str, str]:
    """Return a clean environment that can import this checkout's ``cmd_mox``."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_INHERITED_PREFIXES)
    }
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(ROOT) if not existing else os.pathsep.join((str(ROOT), existing))
    )
    return env


def run_example(
    example: Example, workdir: Path, conftest: str
) -> subprocess.CompletedProcess[str]:
    """Run a tested example in a fresh interpreter rooted at ``workdir``.

    Parameters
    ----------
    example : Example
        A ``tested`` example.
    workdir : Path
        Empty directory that becomes the example's working directory.
    conftest : str
        Published ``conftest.py`` body that enables the plugin for a
        pytest-style example.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished child process, with its output captured.
    """
    module = workdir / f"test_{example.name.replace('-', '_')}.py"
    module.write_text(example.code, encoding="utf-8")
    if example.is_pytest_module:
        (workdir / "conftest.py").write_text(conftest, encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            module.name,
        ]
    else:
        command = [sys.executable, module.name]
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and generated file
        command,
        cwd=workdir,
        env=_child_environment(),
        # A shim reads open stdin to EOF, so an inherited pipe would stall it.
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def describe_failure(example: Example, result: subprocess.CompletedProcess[str]) -> str:
    """Return a diagnostic naming the example and the child's output."""
    detail = (
        "no tests were collected"
        if result.returncode == _NO_TESTS_COLLECTED
        else f"exit status {result.returncode}"
    )
    return (
        f"{example.path}: example {example.name!r} failed ({detail})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
