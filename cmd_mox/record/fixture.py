"""Fixture file data models for Record Mode persistence.

These models represent the 1.0.0 fixture JSON schema defined in the design
specification (Section 9.3).  The ``FixtureFile`` class handles serialization,
deserialization, and file I/O.
"""

from __future__ import annotations

import collections.abc as cabc
import copy
import dataclasses as dc
import datetime as dt
import importlib.metadata
import json
import re
import sys
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from .scrubber import ScrubbingRule

_SCHEMA_VERSION: typ.Final[str] = "1.0.0"

# Two components remain accepted for fixtures written by the previous schema.
_MIN_VERSION_COMPONENT_COUNT: typ.Final[int] = 2
_MAX_VERSION_COMPONENT_COUNT: typ.Final[int] = 3
_SEMVER_CORE_PATTERN: typ.Final[re.Pattern[str]] = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}"
)

# ---------------------------------------------------------------------------
# Schema version parsing and migration
# ---------------------------------------------------------------------------

type _MigrationFn = cabc.Callable[[dict[str, typ.Any]], dict[str, typ.Any]]


def _split_version(version_str: str) -> list[str]:
    """Split a version and validate its component syntax.

    Returns
    -------
    list[str]
        The stripped version's components.

    Raises
    ------
    ValueError
        If the component count or three-part syntax is invalid.
    """
    stripped_version = version_str.strip()
    parts = stripped_version.split(".")
    if not _MIN_VERSION_COMPONENT_COUNT <= len(parts) <= _MAX_VERSION_COMPONENT_COUNT:
        msg = f"Invalid schema version {version_str!r}; expected 'major.minor.patch'"
        raise ValueError(msg)
    if (
        len(parts) == _MAX_VERSION_COMPONENT_COUNT
        and _SEMVER_CORE_PATTERN.fullmatch(stripped_version) is None
    ):
        msg = f"Invalid schema version {version_str!r}; expected 'major.minor.patch'"
        raise ValueError(msg)
    return parts


def _parse_version(version_str: str) -> tuple[int, int, int]:
    """Parse a schema version into a comparable major/minor/patch tuple.

    Parameters
    ----------
    version_str : str
        A version string in ``"major.minor.patch"`` format. Legacy
        ``"major.minor"`` values retain their historical integer parsing and
        are accepted with a zero patch component.

    Returns
    -------
    tuple[int, int, int]
        A ``(major, minor, patch)`` tuple suitable for comparison.

    Raises
    ------
    ValueError
        If the string is not a two- or three-component numeric version.
    """
    parts = _split_version(version_str)
    try:
        components = [int(part) for part in parts]
    except ValueError:
        msg = (
            f"Invalid schema version {version_str!r}; "
            "expected numeric 'major.minor.patch'"
        )
        raise ValueError(msg) from None
    # Legacy versions omit PATCH; complete the tuple before comparison.
    components.extend([0] * (_MAX_VERSION_COMPONENT_COUNT - len(components)))
    major, minor, patch = components
    if min(major, minor, patch) < 0:
        msg = f"Invalid schema version {version_str!r}; components must be non-negative"
        raise ValueError(msg)
    return (major, minor, patch)


def _migrate_v0_to_v1(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Migrate a major 0 fixture dict to schema 1.0.0.

    Returns
    -------
    dict[str, typ.Any]
        *data* with its ``version`` field stamped as ``"1.0.0"``.
    """
    data["version"] = "1.0.0"
    return data


# Maps source *major* version -> (target version tuple, migration function).
# All files with a given major version are migrated using the registered
# function.  Migrations chain: major 0 -> major 1 -> major 2 etc.
_MIGRATIONS: dict[int, tuple[tuple[int, int, int], _MigrationFn]] = {
    0: ((1, 0, 0), _migrate_v0_to_v1),
}


def _normalize_version_field(data: dict[str, typ.Any]) -> None:
    """Ensure *data* has a valid ``version`` field, mutating in-place.

    A missing ``version`` key is treated as ``"0.0.0"`` (legacy fixture
    predating the version field).  An explicit ``None`` value is treated
    as an invalid type, not as a missing key.

    Parameters
    ----------
    data : dict[str, typ.Any]
        The raw fixture dict whose ``version`` field will be validated
        and, if absent, set to ``"0.0.0"``.

    Raises
    ------
    ValueError
        If the ``version`` field is present but is not a string
        (including ``None``).
    """
    if "version" not in data:
        data["version"] = "0.0.0"
    elif not isinstance(data["version"], str):
        actual = type(data["version"]).__name__
        msg = f"Invalid fixture version field: expected str, got {actual}"
        raise ValueError(msg)


def _check_version_compatibility(
    file_ver: tuple[int, int, int],
    current: tuple[int, int, int],
    file_version_str: str,
) -> None:
    """Reject fixture versions with a higher major version.

    Parameters
    ----------
    file_ver : tuple[int, int, int]
        The parsed ``(major, minor, patch)`` version from the fixture file.
    current : tuple[int, int, int]
        The parsed ``(major, minor, patch)`` version of the current schema.
    file_version_str : str
        The raw version string from the fixture, used in error messages.

    Raises
    ------
    ValueError
        If *file_ver* has a higher major version with no downgrade path.
    """
    if file_ver[0] > current[0]:
        msg = (
            f"Unsupported fixture schema version {file_version_str!r}; "
            f"no migration path to {_SCHEMA_VERSION!r}"
        )
        raise ValueError(msg)


def _execute_migration_chain(
    data: dict[str, typ.Any],
    file_ver: tuple[int, int, int],
    current: tuple[int, int, int],
) -> dict[str, typ.Any]:
    """Chain migrations until the major versions match.

    Parameters
    ----------
    data : dict[str, typ.Any]
        The fixture dict to migrate.  Each migration function receives
        and returns a dict, so successive calls are chained.
    file_ver : tuple[int, int, int]
        The parsed ``(major, minor, patch)`` version of the fixture.
    current : tuple[int, int, int]
        The parsed ``(major, minor, patch)`` version of the current schema.

    Returns
    -------
    dict[str, typ.Any]
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


def _apply_migrations(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Apply chained migrations to bring *data* up to the current schema.

    Returns
    -------
    dict[str, typ.Any]
        A deep copy of *data* migrated to the current schema version.
    """
    data = copy.deepcopy(data)  # deep copy to avoid mutating the caller's dict

    _normalize_version_field(data)

    current = _parse_version(_SCHEMA_VERSION)
    file_ver = _parse_version(data["version"])
    data["version"] = ".".join(str(component) for component in file_ver)

    # Same major version: tolerate minor and patch differences.
    if file_ver[0] == current[0]:
        return data

    _check_version_compatibility(file_ver, current, data["version"])

    return _execute_migration_chain(data, file_ver, current)


def _cmdmox_version() -> str:
    """Return the installed cmd-mox version, or ``"unknown"``.

    Returns
    -------
    str
        The distribution version, or ``"unknown"`` when cmd-mox is not
        installed as a distribution.
    """
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

    def to_dict(self) -> dict[str, typ.Any]:
        """Return a JSON-serializable mapping matching the 1.0.0 schema.

        Returns
        -------
        dict[str, typing.Any]
            Serialized invocation fields.
        """
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
    def from_dict(cls, data: dict[str, typ.Any]) -> RecordedInvocation:
        """Construct an invocation from a JSON-compatible mapping.

        Parameters
        ----------
        data : dict[str, typing.Any]
            Mapping containing serialized invocation fields.

        Returns
        -------
        RecordedInvocation
            Parsed invocation model.
        """
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

    def to_dict(self) -> dict[str, typ.Any]:
        """Return a JSON-serializable mapping.

        Returns
        -------
        dict[str, typing.Any]
            Serialized metadata fields.
        """
        d: dict[str, typ.Any] = {
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
    def from_dict(cls, data: dict[str, typ.Any]) -> FixtureMetadata:
        """Construct metadata from a JSON-compatible mapping.

        Parameters
        ----------
        data : dict[str, typing.Any]
            Mapping containing serialized metadata fields.

        Returns
        -------
        FixtureMetadata
            Parsed metadata model.
        """
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
        """Auto-populate metadata from the current runtime environment.

        Parameters
        ----------
        test_module : str or None
            Optional test module name to record.
        test_function : str or None
            Optional test function name to record.

        Returns
        -------
        FixtureMetadata
            Metadata populated from the current process.
        """
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

    SCHEMA_VERSION: typ.ClassVar[str] = _SCHEMA_VERSION

    version: str
    metadata: FixtureMetadata
    recordings: list[RecordedInvocation]
    scrubbing_rules: list[ScrubbingRule]

    def to_dict(self) -> dict[str, typ.Any]:
        """Return a JSON-serializable mapping matching the 1.0.0 schema.

        Returns
        -------
        dict[str, typing.Any]
            Serialized fixture fields.
        """
        return {
            "version": self.version,
            "metadata": self.metadata.to_dict(),
            "recordings": [r.to_dict() for r in self.recordings],
            "scrubbing_rules": [r.to_dict() for r in self.scrubbing_rules],
        }

    @classmethod
    def from_dict(cls, data: dict[str, typ.Any]) -> FixtureFile:
        """Construct from a JSON-compatible mapping.

        Older schema versions are migrated forward automatically. Minor and
        patch differences within the same major version are tolerated because
        unknown fields are ignored.

        Parameters
        ----------
        data : dict[str, typ.Any]
            A JSON-compatible mapping describing a fixture file.

        Returns
        -------
        FixtureFile
            The parsed fixture with its data migrated to the current schema.

        Raises
        ------
        ValueError
            If the mapping does not conform to the fixture schema.
        """
        try:
            data = _apply_migrations(data)
            return cls(
                version=cls.SCHEMA_VERSION,
                metadata=FixtureMetadata.from_dict(data["metadata"]),
                recordings=[
                    RecordedInvocation.from_dict(r) for r in data.get("recordings", [])
                ],
                scrubbing_rules=[
                    ScrubbingRule.from_dict(r) for r in data.get("scrubbing_rules", [])
                ],
            )
        except (AttributeError, KeyError, TypeError) as exc:
            msg = f"Invalid fixture schema: {exc}"
            raise ValueError(msg) from exc

    def save(self, path: Path) -> None:
        """Write this fixture to *path* as JSON, creating directories as needed.

        Parameters
        ----------
        path : pathlib.Path
            Destination JSON path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> FixtureFile:
        """Load a fixture from a JSON file at *path*.

        Parameters
        ----------
        path : pathlib.Path
            JSON fixture path to read.

        Returns
        -------
        FixtureFile
            Parsed and migrated fixture.

        Raises
        ------
        FileNotFoundError
            If ``path`` does not exist.
        ValueError
            If the fixture is not valid JSON or fails schema validation.
        """  # ruff: ignore[docstring-extraneous-exception] - pathlib and JSON/schema parsing propagate these caller-visible failures.
        data = json.loads(path.read_text())
        return cls.from_dict(data)
