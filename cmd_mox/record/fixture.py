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

from .scrubber import ScrubbingRule, ScrubbingRuleDict

_SCHEMA_VERSION: t.Final[str] = "1.0"

# ---------------------------------------------------------------------------
# Schema version parsing and migration
# ---------------------------------------------------------------------------

type _MigrationFn = t.Callable[[dict[str, t.Any]], dict[str, t.Any]]


def _parse_version(version_str: str) -> tuple[int, int]:
    """Parse a ``"major.minor"`` version string into a comparable tuple.

    Parameters
    ----------
    version_str : str
        A version string in ``"major.minor"`` format (e.g. ``"1.0"``).

    Returns
    -------
    tuple[int, int]
        A ``(major, minor)`` tuple suitable for comparison.

    Raises
    ------
    ValueError
        If the string cannot be parsed as two dot-separated integers.
    """
    parts = version_str.strip().split(".")
    if len(parts) != 2:
        msg = f"Invalid schema version {version_str!r}; expected 'major.minor'"
        raise ValueError(msg)
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        msg = f"Invalid schema version {version_str!r}; expected numeric 'major.minor'"
        raise ValueError(msg) from None
    if major < 0 or minor < 0:
        msg = f"Invalid schema version {version_str!r}; components must be non-negative"
        raise ValueError(msg)
    return (major, minor)


def _migrate_v0_to_v1(data: dict[str, t.Any]) -> dict[str, t.Any]:
    """Migrate a v0.x fixture dict to v1.0 format.

    The v0.x schema is hypothetical (v1.0 is the first release).  This
    migration exists to exercise the pipeline and serve as a template for
    future migrations.
    """
    data["version"] = "1.0"
    return data


# Maps source *major* version -> (target version tuple, migration function).
# All files with a given major version are migrated using the registered
# function.  Migrations chain: v0 -> v1 -> v2 etc.
_MIGRATIONS: dict[int, tuple[tuple[int, int], _MigrationFn]] = {
    0: ((1, 0), _migrate_v0_to_v1),
}


def _normalize_version_field(data: dict[str, t.Any]) -> None:
    """Ensure *data* has a valid ``version`` field, mutating in-place.

    A missing ``version`` key is treated as ``"0.0"`` (legacy fixture
    predating the version field).  An explicit ``None`` value is treated
    as an invalid type, not as a missing key.

    Parameters
    ----------
    data : dict[str, t.Any]
        The raw fixture dict whose ``version`` field will be validated
        and, if absent, set to ``"0.0"``.

    Raises
    ------
    ValueError
        If the ``version`` field is present but is not a string
        (including ``None``).
    """
    if "version" not in data:
        data["version"] = "0.0"
    elif not isinstance(data["version"], str):
        actual = type(data["version"]).__name__
        msg = f"Invalid fixture version field: expected str, got {actual}"
        raise ValueError(msg)


def _check_version_compatibility(
    file_ver: tuple[int, int],
    current: tuple[int, int],
    file_version_str: str,
) -> None:
    """Reject fixture versions newer than the running schema.

    Parameters
    ----------
    file_ver : tuple[int, int]
        The parsed ``(major, minor)`` version from the fixture file.
    current : tuple[int, int]
        The parsed ``(major, minor)`` version of the current schema.
    file_version_str : str
        The raw version string from the fixture, used in error messages.

    Raises
    ------
    ValueError
        If *file_ver* is higher than *current* (no downgrade path).
    """
    if file_ver > current:
        msg = (
            f"Unsupported fixture schema version {file_version_str!r}; "
            f"no migration path to {_SCHEMA_VERSION!r}"
        )
        raise ValueError(msg)


def _execute_migration_chain(
    data: dict[str, t.Any],
    file_ver: tuple[int, int],
    current: tuple[int, int],
) -> dict[str, t.Any]:
    """Chain migrations until the major versions match.

    Parameters
    ----------
    data : dict[str, t.Any]
        The fixture dict to migrate.  Each migration function receives
        and returns a dict, so successive calls are chained.
    file_ver : tuple[int, int]
        The parsed ``(major, minor)`` version of the fixture.
    current : tuple[int, int]
        The parsed ``(major, minor)`` version of the current schema.

    Returns
    -------
    dict[str, t.Any]
        The fixture dict after all applicable migrations have been
        applied.

    Raises
    ------
    ValueError
        If no migration is registered for the current major version,
        or if a migration does not advance the major version.
    """
    while file_ver[0] < current[0]:
        entry = _MIGRATIONS.get(file_ver[0])
        if entry is None:
            msg = (
                f"No migration path from schema version "
                f"{data['version']!r} to {_SCHEMA_VERSION!r}"
            )
            raise ValueError(msg)
        target, migrate_fn = entry
        if target[0] <= file_ver[0]:
            msg = (
                f"Misconfigured migration for major version {file_ver[0]}: "
                f"target {target} does not advance the major version"
            )
            raise ValueError(msg)
        data = migrate_fn(data)
        file_ver = target

    return data


def _apply_migrations(data: dict[str, t.Any]) -> dict[str, t.Any]:
    """Apply chained migrations to bring *data* up to the current schema.

    The input dict is shallow-copied so the caller's original is never
    mutated.  A missing ``version`` key is treated as ``"0.0"`` (legacy
    fixture predating the version field).

    Parameters
    ----------
    data : dict[str, t.Any]
        The raw fixture dict, potentially at an older schema version.

    Returns
    -------
    dict[str, t.Any]
        The fixture dict migrated to the current schema version.

    Raises
    ------
    ValueError
        If the version is incompatible and no migration path exists.
    """
    data = dict(data)  # shallow copy to avoid mutating the caller's dict

    _normalize_version_field(data)

    current = _parse_version(_SCHEMA_VERSION)
    file_ver = _parse_version(data["version"])

    # Same major version: tolerate minor differences (semver contract).
    if file_ver[0] == current[0]:
        return data

    _check_version_compatibility(file_ver, current, data["version"])

    return _execute_migration_chain(data, file_ver, current)


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
        raw_module = data.get("test_module")
        raw_function = data.get("test_function")
        return cls(
            created_at=str(data["created_at"]),
            cmdmox_version=str(data["cmdmox_version"]),
            platform=str(data["platform"]),
            python_version=str(data["python_version"]),
            test_module=raw_module if isinstance(raw_module, str) else None,
            test_function=raw_function if isinstance(raw_function, str) else None,
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

    SCHEMA_VERSION: t.ClassVar[str] = _SCHEMA_VERSION

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
        """Construct from a JSON-compatible mapping.

        Older schema versions are migrated forward automatically.  Minor
        version differences within the same major version are tolerated
        because unknown fields are ignored.
        """
        data = _apply_migrations(data)
        return cls(
            version=cls.SCHEMA_VERSION,
            metadata=FixtureMetadata.from_dict(data["metadata"]),
            recordings=[
                RecordedInvocation.from_dict(r) for r in data.get("recordings", [])
            ],
            scrubbing_rules=[
                ScrubbingRule.from_dict(t.cast("ScrubbingRuleDict", r))
                for r in data.get("scrubbing_rules", [])
            ],
        )

    def save(self, path: Path) -> None:
        """Write this fixture to *path* as JSON, creating directories as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> FixtureFile:
        """Load a fixture from a JSON file at *path*."""
        data = json.loads(path.read_text())
        return cls.from_dict(data)
