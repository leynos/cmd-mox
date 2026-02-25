"""Fixture file data models for Record Mode persistence.

These models represent the v1.0 fixture JSON schema defined in the design
specification (Section 9.3).  The ``FixtureFile`` class handles serialization,
deserialization, and file I/O.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import importlib.metadata
import json
import sys
import typing as t

if t.TYPE_CHECKING:
    from pathlib import Path

from .scrubber import ScrubbingRule

_SCHEMA_VERSION: t.Final[str] = "1.0"


def _cmdmox_version() -> str:
    """Return the installed cmd-mox version, or ``"unknown"``."""
    try:
        return importlib.metadata.version("cmd-mox")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@dc.dataclass(slots=True)
class RecordedInvocation:
    """A single recorded command invocation within a fixture."""

    sequence: int
    command: str
    args: list[str]
    stdin: str
    env_subset: dict[str, str]
    stdout: str
    stderr: str
    exit_code: int
    timestamp: str
    duration_ms: int

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serializable mapping matching the v1.0 schema."""
        return {
            "sequence": self.sequence,
            "command": self.command,
            "args": list(self.args),
            "stdin": self.stdin,
            "env_subset": dict(self.env_subset),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> RecordedInvocation:
        """Construct from a JSON-compatible mapping."""
        return cls(
            sequence=int(data["sequence"]),
            command=str(data["command"]),
            args=[str(a) for a in data["args"]],
            stdin=str(data.get("stdin", "")),
            env_subset={str(k): str(v) for k, v in data.get("env_subset", {}).items()},
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            exit_code=int(data.get("exit_code", 0)),
            timestamp=str(data["timestamp"]),
            duration_ms=int(data.get("duration_ms", 0)),
        )


@dc.dataclass(slots=True)
class FixtureMetadata:
    """Metadata captured alongside fixture recordings."""

    created_at: str
    cmdmox_version: str
    platform: str
    python_version: str
    test_module: str | None = None
    test_function: str | None = None

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serializable mapping."""
        d: dict[str, t.Any] = {
            "created_at": self.created_at,
            "cmdmox_version": self.cmdmox_version,
            "platform": self.platform,
            "python_version": self.python_version,
        }
        if self.test_module is not None:
            d["test_module"] = self.test_module
        if self.test_function is not None:
            d["test_function"] = self.test_function
        return d

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> FixtureMetadata:
        """Construct from a JSON-compatible mapping."""
        return cls(
            created_at=str(data["created_at"]),
            cmdmox_version=str(data["cmdmox_version"]),
            platform=str(data["platform"]),
            python_version=str(data["python_version"]),
            test_module=data.get("test_module"),
            test_function=data.get("test_function"),
        )

    @classmethod
    def create(
        cls,
        *,
        test_module: str | None = None,
        test_function: str | None = None,
    ) -> FixtureMetadata:
        """Auto-populate metadata from the current runtime environment."""
        return cls(
            created_at=dt.datetime.now(dt.UTC).isoformat(),
            cmdmox_version=_cmdmox_version(),
            platform=sys.platform,
            python_version=sys.version,
            test_module=test_module,
            test_function=test_function,
        )


@dc.dataclass(slots=True)
class FixtureFile:
    """A complete fixture file with metadata, recordings, and scrubbing rules."""

    version: str
    metadata: FixtureMetadata
    recordings: list[RecordedInvocation]
    scrubbing_rules: list[ScrubbingRule]

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serializable mapping matching the v1.0 schema."""
        return {
            "version": self.version,
            "metadata": self.metadata.to_dict(),
            "recordings": [r.to_dict() for r in self.recordings],
            "scrubbing_rules": [r.to_dict() for r in self.scrubbing_rules],
        }

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> FixtureFile:
        """Construct from a JSON-compatible mapping."""
        return cls(
            version=str(data["version"]),
            metadata=FixtureMetadata.from_dict(data["metadata"]),
            recordings=[
                RecordedInvocation.from_dict(r) for r in data.get("recordings", [])
            ],
            scrubbing_rules=[
                ScrubbingRule.from_dict(r) for r in data.get("scrubbing_rules", [])
            ],
        )

    def save(self, path: Path) -> None:
        """Write this fixture to *path* as JSON, creating directories as needed."""
        from pathlib import Path as _Path

        resolved = _Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> FixtureFile:
        """Load a fixture from a JSON file at *path*."""
        from pathlib import Path as _Path

        data = json.loads(_Path(path).read_text())
        return cls.from_dict(data)
