"""Read GitHub Actions workflows fallibly, and name what they reach.

Generic: nothing here knows about CodeScene. The loader, the trigger
grammar, the reading of local ``uses:`` references, and the one
filesystem call live together so that a contract about any subject can
use them. Every reading except ``read_workflow_tree`` is pure over
supplied documents, which is what lets a contract ask what a rule makes
of a workflow this repository does not contain: the real files use one
spelling of everything and cannot tell a working reader from a broken
one.
"""

from __future__ import annotations

import typing as typ

import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

#: A parsed workflow or action document. Keys are ``object`` rather than
#: ``str`` because a resolving YAML loader keys an unquoted ``on:`` as the
#: boolean ``True``; the loader here keeps strings, but the readings
#: accept documents built either way.
type Document = dict[object, object]

#: The workflow directory, relative to the repository root.
WORKFLOW_DIRECTORY: typ.Final[str] = ".github/workflows/"

#: The file names GitHub accepts for a local action's metadata.
ACTION_FILES: typ.Final[tuple[str, ...]] = ("action.yml", "action.yaml")


class WorkflowReadingError(RuntimeError):
    """Raised when a reading cannot give a trustworthy answer.

    Every rule built on these readings is a refusal, and a refusal over
    an empty or misread subject set is satisfied by any repository. So a
    reading that cannot answer raises this rather than returning nothing.
    """


class _UniqueKeyLoader(yaml.BaseLoader):
    """``yaml.BaseLoader`` refusing a mapping that declares a key twice.

    PyYAML keeps the last of two equal keys and says nothing, so a job
    declaring ``runs-on`` twice parses into a document holding only the
    second value while GitHub may run the first. Refusing the document is
    the only reading that cannot be wrong about which half runs.
    """

    @typ.override
    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[object, object]:
        """Construct one mapping, refusing a key already seen in it.

        Returns
        -------
        dict[object, object]
            The constructed mapping.

        Raises
        ------
        WorkflowReadingError
            If a key appears twice in the mapping.
        """
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                msg = f"found duplicate key {key!r}{key_node.start_mark}"
                raise WorkflowReadingError(msg)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_document(text: str) -> Document:
    r"""Parse a workflow, keeping every scalar as a string.

    Parameters
    ----------
    text : str
        The document's YAML.

    Returns
    -------
    Document
        Its top-level mapping.

    Raises
    ------
    WorkflowReadingError
        If the text is not YAML, declares a key twice, or is not a mapping.

    Examples
    --------
    >>> load_document("on:\n  push:\n")
    {'on': {'push': ''}}
    """
    try:
        parsed = yaml.load(text, Loader=_UniqueKeyLoader)  # ruff: ignore[unsafe-yaml-load] - BaseLoader constructs no objects
    except yaml.YAMLError as error:
        raise WorkflowReadingError(str(error)) from error
    if not isinstance(parsed, dict):
        msg = f"a workflow must parse to a mapping, not {type(parsed).__name__}"
        raise WorkflowReadingError(msg)
    return typ.cast("Document", parsed)


def _trigger_value(document: Document) -> object:
    """Return ``on``, read under the string key and the boolean ``True``.

    Returns
    -------
    object
        The declaration, or ``None`` when there is none.

    Raises
    ------
    WorkflowReadingError
        If both keys are declared, since GitHub merges them and a reader
        choosing one would be blind to the other.
    """
    if "on" in document and True in document:
        msg = "the document declares its triggers under both 'on' and True"
        raise WorkflowReadingError(msg)
    return document.get("on", document.get(True))


def triggers(document: Document) -> dict[str, object]:
    r"""Return a workflow's triggers, mapped to their filters.

    ``on:`` may be a scalar, a sequence, or a mapping, and a reader that
    handles only the mapping reads ``on: [push, pull_request]`` as one
    trigger named after the whole list.

    Parameters
    ----------
    document : Document
        A parsed workflow.

    Returns
    -------
    dict[str, object]
        Trigger name to its filter mapping (``""`` when it has none).

    Raises
    ------
    WorkflowReadingError
        If ``on`` is absent, doubly declared, or of no recognized form.

    Examples
    --------
    >>> triggers(load_document("on: [push, pull_request]\n"))
    {'push': '', 'pull_request': ''}
    """
    match _trigger_value(document):
        case str() as name:
            return {name: ""}
        case list() as names:
            return {str(name): "" for name in names}
        case dict() as mapping:
            return {str(name): value for name, value in mapping.items()}
        case other:
            msg = f"unrecognized trigger declaration {other!r}"
            raise WorkflowReadingError(msg)


def filter_names(value: object) -> list[str]:
    """Return a trigger filter's entries, spelled as a list or a scalar.

    Returns
    -------
    list[str]
        The entries, in declaration order.
    """
    match value:
        case list():
            return [str(entry) for entry in value]
        case str():
            return [value]
        case _:
            return []


def jobs(document: Document) -> dict[str, dict[object, object]]:
    """Return a workflow's jobs that are mappings, by name.

    Returns
    -------
    dict[str, dict[object, object]]
        Job name to job, in declaration order.
    """
    declared = document.get("jobs")
    if not isinstance(declared, dict):
        return {}
    return {str(name): job for name, job in declared.items() if isinstance(job, dict)}


def steps(container: dict[object, object]) -> list[dict[object, object]]:
    """Return the mapping steps of a job or of a composite action's ``runs``.

    Returns
    -------
    list[dict[object, object]]
        The steps, in declaration order.
    """
    declared = container.get("steps")
    if not isinstance(declared, list):
        return []
    return [step for step in declared if isinstance(step, dict)]


def all_steps(document: Document) -> list[dict[object, object]]:
    """Return every step of a workflow's jobs or of a composite action.

    Returns
    -------
    list[dict[object, object]]
        The steps, flattened in declaration order.
    """
    runs = document.get("runs")
    found = steps(runs) if isinstance(runs, dict) else []
    for job in jobs(document).values():
        found += steps(job)
    return found


def uses_references(document: Document) -> list[str]:
    """Return every ``uses:`` value of a document's jobs and steps.

    Returns
    -------
    list[str]
        Job-level calls then step-level references, in declaration order.
    """
    callers = [*jobs(document).values(), *all_steps(document)]
    return [str(item["uses"]) for item in callers if "uses" in item]


class Scalar(typ.NamedTuple):
    """One key or value of a parsed document, with the path reaching it."""

    path: str
    text: str
    is_key: bool


def scalars(value: object, where: str = "") -> cabc.Iterator[Scalar]:
    """Yield every key and scalar value of a parsed document.

    Keys are yielded as well as values, because a mapping key such as an
    ``env`` variable name or a ``workflow_call`` secret declaration is as
    much a part of what runs as the value beside it.

    Yields
    ------
    Scalar
        Each key and value, with its path.
    """
    match value:
        case dict():
            for key, child in value.items():
                path = f"{where}.{key}" if where else str(key)
                yield Scalar(path, str(key), is_key=True)
                yield from scalars(child, path)
        case list():
            for index, child in enumerate(value):
                yield from scalars(child, f"{where}[{index}]")
        case _:
            yield Scalar(where, str(value), is_key=False)


def _read_one(path: Path) -> Document:
    """Return one parsed document, naming the file on failure.

    Returns
    -------
    Document
        The parsed document.

    Raises
    ------
    WorkflowReadingError
        If the file cannot be read or parsed.
    """
    try:
        return load_document(path.read_text(encoding="utf-8"))
    except (OSError, WorkflowReadingError) as error:
        msg = f"{path} is not a readable workflow document: {error}"
        raise WorkflowReadingError(msg) from error


def _is_yaml(path: Path) -> bool:
    """Return whether a path names a YAML file, whatever the suffix's case.

    Returns
    -------
    bool
        True for a file ending ``.yml`` or ``.yaml`` in any case.
    """
    return path.is_file() and path.suffix.lower() in {".yml", ".yaml"}


def read_workflow_tree(root: Path) -> dict[str, Document]:
    """Return every workflow and local action under a repository root.

    The only filesystem access here. Workflows are read under both
    suffixes in any case, since GitHub runs ``ci.YML``; local actions are
    read from each ``action.yml`` below ``.github``.

    Parameters
    ----------
    root : Path
        The repository root.

    Returns
    -------
    dict[str, Document]
        Repository-relative POSIX path to parsed document.

    Raises
    ------
    WorkflowReadingError
        If no workflow is found, or one cannot be read.
    """
    workflows = sorted(
        path for path in (root / WORKFLOW_DIRECTORY).iterdir() if _is_yaml(path)
    )
    if not workflows:
        msg = f"no workflow was read under {root}; the reader is broken"
        raise WorkflowReadingError(msg)
    actions = sorted(
        path
        for name in ACTION_FILES
        for path in (root / ".github").rglob(name)
        if _is_yaml(path)
    )
    return {
        path.relative_to(root).as_posix(): _read_one(path)
        for path in [*workflows, *actions]
    }
