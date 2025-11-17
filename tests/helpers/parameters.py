"""Parameter object helpers for pytest-bdd step implementations."""

from __future__ import annotations

import dataclasses as dc


@dc.dataclass(slots=True)
class EnvVar:
    """Encapsulates an environment variable key-value pair."""

    name: str
    value: str


@dc.dataclass(slots=True)
class CommandOutput:
    """Encapsulates command output streams and exit code."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dc.dataclass(slots=True)
class CommandInputs:
    """Encapsulates command execution inputs."""

    args: str = ""
    stdin: str = ""


_PLACEHOLDER_TOKENS: dict[str, str] = {
    "<space>": " ",
    "<SPACE>": " ",
    "<caret>": "^",
    "<CARET>": "^",
    "<dq>": '"',
    "<DQ>": '"',
}


def decode_placeholders(value: str) -> str:
    """Expand user-facing placeholder tokens embedded in feature files."""
    result = value
    for token, replacement in _PLACEHOLDER_TOKENS.items():
        result = result.replace(token, replacement)
    return result
