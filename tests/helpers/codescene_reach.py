"""What puts CodeScene in reach of a pull request, as readings.

CV-005 keeps CodeScene off the pull-request surface by every road: the
upload action, the ``cs-coverage`` command, the ``CS_ACCESS_TOKEN``
secret, and the service's host. Each reading here is pure over one
parsed document and reads every key and value in it, case-folded, rather
than the scopes a breach is expected in: a workflow-level
``defaults.run.shell``, a ``workflow_call`` secret declaration, and a
reusable-workflow input all reach a process, and a reading that
enumerated scopes left the rest as a way round the rule. Comments are not
read, because the parser discards them, so prose explaining the policy
is not mistaken for a breach of it.
"""

from __future__ import annotations

import re
import typing as typ

from tests.helpers.workflow_reading import jobs, scalars, uses_references

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document, Scalar

#: The shared action that talks to CodeScene, matched on its path.
UPLOAD_ACTION: typ.Final[str] = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage"
)

#: The coverage generator every lane may run.
COVERAGE_ACTION: typ.Final[str] = (
    "leynos/shared-actions/.github/actions/generate-coverage"
)

#: The secret no pull-request lane may reach, under any spelling.
#: Secret names are case-insensitive in expressions, so it is matched
#: case-folded.
CREDENTIAL: typ.Final[str] = "CS_ACCESS_TOKEN"

#: The command no pull-request lane may run.
CLI_COMMAND: typ.Final[str] = "cs-coverage"

#: The service itself. DNS names are case-insensitive.
CODESCENE_HOST: typ.Final[str] = "codescene.io"

#: The repository variable this adoption retires, with the input it fed.
RETIRED_NAMES: typ.Final[tuple[str, ...]] = (
    "CODESCENE_CLI_SHA256",
    "installer-checksum",
    "archive-checksum",
)

_EXPRESSION: typ.Final[re.Pattern[str]] = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)

#: The whole ``secrets`` context rather than one named member of it:
#: ``toJSON(secrets)`` or ``secrets[format(...)]`` hands over the token
#: without naming it.
_WHOLE_SECRETS: typ.Final[re.Pattern[str]] = re.compile(
    r"\bsecrets\b(?!\s*\.\s*\w)", re.IGNORECASE
)


def normalized(text: object) -> str:
    """Return text with runs of whitespace collapsed to single spaces.

    Returns
    -------
    str
        The normalized text.

    Examples
    --------
    >>> normalized("a  &&  b")
    'a && b'
    """
    return " ".join(str(text).split())


def _expressions(scalar: Scalar) -> list[str]:
    """Return the expression text in one value; an ``if:`` is all expression.

    Returns
    -------
    list[str]
        The bodies of the value's expressions.
    """
    if scalar.is_key:
        return []
    if scalar.path.rsplit(".", 1)[-1] == "if":
        return [scalar.text]
    return _EXPRESSION.findall(scalar.text)


def _mentions(document: Document, needle: str) -> list[str]:
    """Return the path of every key or value naming ``needle``, case-folded.

    Returns
    -------
    list[str]
        The paths.
    """
    folded = needle.casefold()
    return [
        scalar.path for scalar in scalars(document) if folded in scalar.text.casefold()
    ]


def token_sites(document: Document) -> list[str]:
    r"""Return every place a document puts the token in reach of a process.

    Three roads: naming the secret anywhere (an ``env`` key, an
    expression, a ``secrets:`` forward, a ``workflow_call`` declaration);
    reading the whole ``secrets`` context; and ``secrets: inherit``, which
    names nothing and forwards everything.

    Parameters
    ----------
    document : Document
        A parsed workflow or action.

    Returns
    -------
    list[str]
        The path of each site.

    Examples
    --------
    >>> from tests.helpers.workflow_reading import load_document
    >>> body = "jobs:\n  a:\n    uses: x/y@z\n    secrets: inherit\n"
    >>> token_sites(load_document(body))
    ['jobs.a.secrets: inherit']
    """
    named = _mentions(document, CREDENTIAL)
    whole = [
        f"{scalar.path}: whole secrets context"
        for scalar in scalars(document)
        if any(_WHOLE_SECRETS.search(text) for text in _expressions(scalar))
    ]
    inherited = [
        f"jobs.{name}.secrets: inherit"
        for name, job in jobs(document).items()
        if str(job.get("secrets", "")).strip() == "inherit"
    ]
    return [*named, *whole, *inherited]


def host_contacts(document: Document) -> list[str]:
    """Return every key or value in a document naming the CodeScene host.

    Returns
    -------
    list[str]
        The path of each.
    """
    return _mentions(document, CODESCENE_HOST)


def cli_invocations(document: Document) -> list[str]:
    """Return every key or value in a document naming the ``cs-coverage`` tool.

    A prohibition may be a substring test: a false match fails loudly,
    and the command can hide inside a list (``echo x && cs-coverage``) or
    behind a wrapper (``sudo -u runner cs-coverage``).

    Returns
    -------
    list[str]
        The path of each.
    """
    return _mentions(document, CLI_COMMAND)


def upload_references(document: Document) -> list[str]:
    """Return every ``uses:`` reference to the CodeScene upload action.

    Returns
    -------
    list[str]
        The references, case-folded comparison, as written.
    """
    folded = UPLOAD_ACTION.casefold()
    return [ref for ref in uses_references(document) if folded in ref.casefold()]


def retired_names(document: Document) -> list[str]:
    """Return every key or value naming the retired checksum plumbing.

    ``installer-checksum`` is rejected when non-empty from the pinned
    uploader, and ``archive-checksum`` is not a rename of it: it digests
    the action's CLI manifest archive, while ``CODESCENE_CLI_SHA256`` held
    the installer script's digest. The action pins the CLI through its own
    manifest now.

    Returns
    -------
    list[str]
        ``path (name)`` for each.
    """
    return [
        f"{path} ({name})"
        for name in RETIRED_NAMES
        for path in _mentions(document, name)
    ]


def pull_request_breaches(surface: dict[str, Document]) -> list[str]:
    """Return every CodeScene contact on the pull-request surface.

    Parameters
    ----------
    surface : dict[str, Document]
        Every document a pull request can run.

    Returns
    -------
    list[str]
        ``document: clause: site`` for each breach.
    """
    clauses = {
        "upload action": upload_references,
        "cs-coverage": cli_invocations,
        "token": token_sites,
        "host": host_contacts,
    }
    return [
        f"{name}: {clause}: {site}"
        for name, document in surface.items()
        for clause, reading in clauses.items()
        for site in reading(document)
    ]
