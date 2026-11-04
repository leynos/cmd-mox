"""Transport-independent values used by command mocking and IPC.

Create :class:`Invocation` and :class:`Response` values for handlers and
recording. Existing imports from :mod:`cmd_mox.ipc` expose the same classes.

This module also owns the shared sensitive-environment classifier,
:func:`is_sensitive_recording_env_key`, and the masking helpers built on it:
:func:`mask_env_value` and :func:`mask_env_mapping`. Recording subset
filtering, argument redaction, and the diagnostic messages of the response
policy, verifiers, and spy assertions all consult the same vocabulary, so a
key is classified once and consistently everywhere.

Masking is a formatting concern. The helpers return new values and never
mutate the invocation or expectation data they are given; matching and
recording continue to compare and store raw values. The classifier is
deliberately coarse: a false positive hides one value from a diagnostic, while
a false negative discloses a credential.

Examples
--------
>>> invocation = Invocation("git", ["status"], "", {})
>>> invocation.apply(Response(stdout="ok"))
>>> invocation.stdout
'ok'
>>> mask_env_mapping({"DB_PWD": "hunter2", "PATH": "/bin"})
{'DB_PWD': '***', 'PATH': '/bin'}
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc

SENSITIVE_ENV_KEY_TOKENS: typ.Final[tuple[str, ...]] = (
    "secret",
    "token",
    "api_key",
    "password",
)
_SENSITIVE_ENV_KEY_TOKENS_CASEFOLDED: typ.Final[tuple[str, ...]] = tuple(
    token.casefold() for token in SENSITIVE_ENV_KEY_TOKENS
)
SENSITIVE_ENV_KEY_PATTERN: typ.Final[re.Pattern[str]] = re.compile(
    # ``AUTH_CONFIG`` covers registry credentials such as DOCKER_AUTH_CONFIG,
    # whose value is a JSON document holding an ``auth`` field rather than a
    # single credential. It is matched as a whole segment so that near-misses
    # such as AUTH_CONFIGURATION stay readable.
    r"(?i)(^|[_-])(?:"
    r"(?:API)?(?:KEY|TOKEN|SECRET|PASSWORD|PASSPHRASE|CREDENTIALS?|PASS(?:WORD)?|PWD)"
    r"|AUTH(?:ENTICATION)?[-_]CONFIG"
    r")(?=[_-]|=|:|\d|$)"
)

_REPR_FIELD_LIMIT: typ.Final[int] = 256

# The mask token used by the conflict, verifier, and spy-assertion messages.
# It matches what those messages have always printed, so adopting the shared
# helper leaves non-sensitive messages byte-identical. It is not universal:
# ``Invocation.__repr__`` keeps its own ``<redacted>`` token.
REDACTED_VALUE: typ.Final[str] = "***"


def is_sensitive_recording_env_key(key: str) -> bool:
    """Return whether *key* should be treated as secret-bearing in diagnostics.

    Returns
    -------
    bool
        Whether the key contains a sensitive token or matches a secret-bearing
        key segment such as ``GITHUB_KEY`` or ``DB_PWD``.
    """
    normalized_key = key.casefold()
    return any(
        token in normalized_key for token in _SENSITIVE_ENV_KEY_TOKENS_CASEFOLDED
    ) or bool(SENSITIVE_ENV_KEY_PATTERN.search(key))


def mask_env_value(key: str, value: str | None) -> str | None:
    """Return *value* masked when *key* appears sensitive.

    Parameters
    ----------
    key : str
        Environment key used to identify sensitive values.
    value : str or None
        Value to mask. ``None`` stays ``None`` so a missing variable is
        distinguishable from a hidden one.

    Returns
    -------
    str or None
        :data:`REDACTED_VALUE` for a sensitive key, otherwise *value*.

    Examples
    --------
    >>> mask_env_value("DB_PWD", "hunter2")
    '***'
    >>> mask_env_value("LOG_LEVEL", "debug")
    'debug'
    """
    if value is None:
        return None
    return REDACTED_VALUE if is_sensitive_recording_env_key(key) else value


def mask_env_mapping(
    mapping: cabc.Mapping[str, str | None],
) -> dict[str, str | None]:
    """Return a diagnostic-safe copy of *mapping*.

    A new ``dict`` is built in the input's insertion order so callers can
    ``repr()`` it without exposing secrets and without mutating raw invocation
    or expectation data.

    Parameters
    ----------
    mapping : collections.abc.Mapping
        Environment values to mask.

    Returns
    -------
    dict[str, str or None]
        A new mapping with sensitive values replaced by :data:`REDACTED_VALUE`.
    """
    return {key: mask_env_value(key, value) for key, value in mapping.items()}


def _shorten(text: str, limit: int = _REPR_FIELD_LIMIT) -> str:
    """Shorten long diagnostic text without changing its prefix.

    Returns
    -------
    str
        Text bounded to *limit* characters.
    """
    if limit <= 0:
        return ""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


@dc.dataclass(slots=True)
class Invocation:
    """Information reported by a shim to the command-mocking domain.

    Attributes
    ----------
    command : str
        Name associated with the shim invocation.
    args : list of str
        Command-line arguments passed after the executable name.
    stdin : str
        Standard input captured by the shim. When stdin is not a terminal, the
        shim reads it through EOF to populate this field, bounded by
        ``CMOX_IPC_TIMEOUT`` on every platform and for every stream kind. A
        capture that reaches the deadline discards partial input, skips IPC,
        writes the timeout diagnostic to stderr, and exits with status ``1``.
        Close or redirect stdin (for example, to ``subprocess.DEVNULL``) when
        the caller supplies no input.
    env : dict[str, str]
        Environment variables copied from the shim process.
    stdout : str
        Standard output from the configured response.
    stderr : str
        Standard error from the configured response.
    exit_code : int
        Exit status from the configured response.
    invocation_id : str or None
        Optional identifier used for client/server observability correlation
        and to match passthrough results. It is not returned in an ordinary
        ``Response``.
    """

    command: str
    args: list[str]
    stdin: str
    env: dict[str, str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    invocation_id: str | None = None

    def apply(self, resp: Response) -> None:
        """Copy stdout, stderr, and exit status from *resp* in place."""
        self.stdout, self.stderr, self.exit_code = (
            resp.stdout,
            resp.stderr,
            resp.exit_code,
        )

    def __repr__(self) -> str:
        """Return a bounded diagnostic representation with secrets redacted.

        Returns
        -------
        str
            A representation with sensitive environment values redacted.
        """
        safe_env = {
            key: ("<redacted>" if is_sensitive_recording_env_key(key) else value)
            for key, value in self.env.items()
        }
        data = {
            "command": self.command,
            "args": list(self.args),
            "stdin": _shorten(self.stdin),
            "stdout": _shorten(self.stdout),
            "stderr": _shorten(self.stderr),
            "exit_code": self.exit_code,
            "env": safe_env,
        }
        return f"Invocation({data!r})"


@dc.dataclass(slots=True)
class PassthroughRequest:
    """Instruction for a shim to execute the real command.

    Attributes
    ----------
    invocation_id : str
        Identifier used to correlate the request with its result.
    lookup_path : str
        Executable path selected for the real command.
    extra_env : dict[str, str]
        Additional environment values for the real command.
    timeout : float
        Maximum time allowed for the real command to finish.
    """

    invocation_id: str
    lookup_path: str
    extra_env: dict[str, str] = dc.field(default_factory=dict)
    timeout: float = 30.0


@dc.dataclass(slots=True)
class PassthroughResult:
    """Result reported by a shim after executing a real command.

    Attributes
    ----------
    invocation_id : str
        Identifier used to correlate the result with its request.
    stdout : str
        Standard output captured from the real command.
    stderr : str
        Standard error captured from the real command.
    exit_code : int
        Exit status returned by the real command.
    """

    invocation_id: str
    stdout: str
    stderr: str
    exit_code: int


@dc.dataclass(slots=True)
class Response:
    """Response returned for an invocation.

    Attributes
    ----------
    stdout : str
        Standard output supplied to the shim.
    stderr : str
        Standard error supplied to the shim.
    exit_code : int
        Exit status supplied to the shim.
    env : dict[str, str]
        Environment values to apply in the shim process.
    passthrough : PassthroughRequest or None
        Optional instruction to execute the real command.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    env: dict[str, str] = dc.field(default_factory=dict)
    passthrough: PassthroughRequest | None = None


__all__ = ["Invocation", "PassthroughRequest", "PassthroughResult", "Response"]
