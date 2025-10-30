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
