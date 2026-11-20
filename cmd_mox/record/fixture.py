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

from ._schema import (
    SCHEMA_ERROR_PREFIX,
    _optional_str,
    _require_int,
    _require_list,
    _require_mapping,
    _require_object,
    _require_str,
    _require_str_list,
    _require_str_mapping,
)
from .scrubber import ScrubbingRule

# The single source of the current schema version.  The public string derives
# from this tuple; migration targets are historical, so they stay fixed once
# registered (see ``_MIGRATIONS``).
_CURRENT_VERSION: typ.Final[tuple[int, int, int]] = (1, 0, 0)


def _format_version(version: tuple[int, int, int]) -> str:
    """Format *version* as the canonical ``major.minor.patch`` string."""
    return ".".join(str(component) for component in version)


_SCHEMA_VERSION: typ.Final[str] = _format_version(_CURRENT_VERSION)

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

    Three-component versions must match the canonical SemVer core.  Legacy
    two-component versions accept only non-empty ASCII decimal components:
    leading zeroes remain valid, but signs, underscores, internal whitespace
    and non-ASCII digits are rejected before ``int`` sees them.

    Returns
    -------
    list[str]
        The stripped version's components.

    Raises
    ------
    ValueError
        If the component count or component syntax is invalid.
    """
    stripped_version = version_str.strip()
    parts = stripped_version.split(".")
    if not _MIN_VERSION_COMPONENT_COUNT <= len(parts) <= _MAX_VERSION_COMPONENT_COUNT:
        msg = f"Invalid schema version {version_str!r}; expected 'major.minor.patch'"
        raise ValueError(msg)
    if len(parts) == _MAX_VERSION_COMPONENT_COUNT:
        if _SEMVER_CORE_PATTERN.fullmatch(stripped_version) is None:
            msg = (
                f"Invalid schema version {version_str!r}; expected 'major.minor.patch'"
            )
            raise ValueError(msg)
    elif not all(part.isascii() and part.isdecimal() for part in parts):
        msg = (
            f"Invalid schema version {version_str!r}; "
            "expected numeric 'major.minor.patch'"
        )
        raise ValueError(msg)
    return parts


def _parse_version(version_str: str) -> tuple[int, int, int]:
    """Parse a schema version into a comparable major/minor/patch tuple.

    Parameters
    ----------
    version_str : str
        A version string in ``"major.minor.patch"`` format. Legacy
        ``"major.minor"`` values must use ASCII decimal components, which may
        carry leading zeroes, and are accepted with a zero patch component.

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
        # ``int`` still fails for a component beyond its runtime digit limit.
        msg = (
            f"Invalid schema version {version_str!r}; "
            "expected numeric 'major.minor.patch'"
        )
        raise ValueError(msg) from None
    # Legacy versions omit PATCH; complete the tuple before comparison.
    components.extend([0] * (_MAX_VERSION_COMPONENT_COUNT - len(components)))
    major, minor, patch = components
    return (major, minor, patch)


def _migrate_v0_to_v1(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Migrate a synthetic major 0 fixture to the 1.0.0 layout.

    Major 0 is a placeholder for fixtures written before schema versioning;
    those fixtures already use the 1.0.0 field layout.  The migration therefore
    returns *data* unchanged: ``_execute_migration_chain`` stamps the
    registered target version after the callable returns.

    Returns
    -------
    dict[str, typ.Any]
        *data* unchanged.
    """
    return data


# Maps source *major* version -> (target version tuple, migration function).
# All files with a given major version are migrated using the registered
# function.  Migrations chain: major 0 -> major 1 -> major 2 etc.
#
# A target records the version its migration produces and therefore stays
# fixed once registered.  Only a newly added hop may derive its target from
# ``_CURRENT_VERSION``.  This keeps the historical 0 -> 1 target at 1.0.0, so a
# major 0 fixture still runs every hop after a future major 2 adds its own
# ``1: ((2, 0, 0), _migrate_v1_to_v2)`` entry.
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
        msg = f"{SCHEMA_ERROR_PREFIX}: version: expected str, got {actual}"
        raise ValueError(msg)


def _execute_migration_chain(
    data: dict[str, typ.Any],
    file_ver: tuple[int, int, int],
    current: tuple[int, int, int],
) -> dict[str, typ.Any]:
    """Chain migrations until *data* reaches the current major version.

    This function owns every major-version decision.  It rejects a fixture
    whose major version is newer than *current*, then walks the registry one
    hop at a time.  Each migration callable receives and returns a mapping;
    the chain stamps the registered target version afterwards, so callables
    never write ``version`` themselves.

    Parameters
    ----------
    data : dict[str, typ.Any]
        The fixture dict to migrate.  Its ``version`` field must already hold
        the canonical form of *file_ver*.
    file_ver : tuple[int, int, int]
        The parsed ``(major, minor, patch)`` version of the fixture.
    current : tuple[int, int, int]
        The parsed ``(major, minor, patch)`` version of the current schema.

    Returns
    -------
    dict[str, typ.Any]
        The fixture dict after all applicable migrations have been
        applied and stamped with the latest registered target.

    Raises
    ------
    ValueError
        If the fixture has a higher major version with no migration path, if
        no migration is registered for the current major version, or if a
        migration does not advance the major version.
    """
    if file_ver[0] > current[0]:
        msg = (
            f"Unsupported fixture schema version {_format_version(file_ver)!r}; "
            f"no migration path to {_format_version(current)!r}"
        )
        raise ValueError(msg)

    while file_ver[0] < current[0]:
        entry = _MIGRATIONS.get(file_ver[0])
        if entry is None:
            msg = (
                f"No migration path from schema version "
                f"{data['version']!r} to {_format_version(current)!r}"
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
        data["version"] = _format_version(target)
        file_ver = target

    return data


def _apply_migrations(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Apply chained migrations to bring *data* up to the current schema.

    Returns
    -------
    dict[str, typ.Any]
        A deep copy of *data* migrated to the current schema version.  A
        missing ``version`` is treated as legacy ``0.0.0``, and same-major
        minor and patch differences pass through without a hop.
    """
    data = copy.deepcopy(data)  # deep copy to avoid mutating the caller's dict

    _normalize_version_field(data)

    current = _CURRENT_VERSION
    file_ver = _parse_version(data["version"])
    data["version"] = _format_version(file_ver)

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


class _Deserializable[ModelT](typ.Protocol):
    """A fixture model that can be reconstructed from a mapping."""

    @classmethod
    def from_dict(cls, data: cabc.Mapping[str, object], *, path: str = "") -> ModelT:
        """Reconstruct the model from *data*."""
        ...


def _parse_indexed[ModelT](
    data: cabc.Mapping[str, object],
    key: str,
    model: type[_Deserializable[ModelT]],
) -> list[ModelT]:
    """Parse the objects in the list at *key* with *model*."""
    return [
        model.from_dict(
            _require_object(item, f"{key}[{index}]"), path=f"{key}[{index}]"
        )
        for index, item in enumerate(_require_list(data, key))
    ]


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
    def from_dict(
        cls, data: cabc.Mapping[str, object], *, path: str = ""
    ) -> RecordedInvocation:
        """Construct an invocation from a JSON-compatible mapping.

        Parameters
        ----------
        data : collections.abc.Mapping[str, object]
            Mapping containing serialized invocation fields.
        path : str
            Schema path used in validation errors; defaults to the bare field
            names of a standalone invocation.

        Returns
        -------
        RecordedInvocation
            Parsed invocation model.

        Raises
        ------
        ValueError
            If a required field is missing or has the wrong type.
        """  # ruff: ignore[docstring-extraneous-exception] - _schema helpers raise this caller-visible error.
        recording = _require_object(data, path)
        return cls(
            sequence=_require_int(recording, "sequence", path=path),
            command=_require_str(recording, "command", path=path),
            args=_require_str_list(recording, "args", path=path),
            stdin=_require_str(recording, "stdin", path=path),
            env_subset=_require_str_mapping(recording, "env_subset", path=path),
            stdout=_require_str(recording, "stdout", path=path),
            stderr=_require_str(recording, "stderr", path=path),
            exit_code=_require_int(recording, "exit_code", path=path),
            timestamp=_require_str(recording, "timestamp", path=path),
            duration_ms=_require_int(recording, "duration_ms", path=path),
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
    def from_dict(
        cls, data: cabc.Mapping[str, object], *, path: str = "metadata"
    ) -> FixtureMetadata:
        """Construct metadata from a JSON-compatible mapping.

        Parameters
        ----------
        data : collections.abc.Mapping[str, object]
            Mapping containing serialized metadata fields.
        path : str
            Schema path used in validation errors.

        Returns
        -------
        FixtureMetadata
            Parsed metadata model.

        Raises
        ------
        ValueError
            If a required field is missing or has the wrong type.
        """  # ruff: ignore[docstring-extraneous-exception] - _schema helpers raise this caller-visible error.
        metadata = _require_object(data, path)
        return cls(
            created_at=_require_str(metadata, "created_at", path=path),
            cmdmox_version=_require_str(metadata, "cmdmox_version", path=path),
            platform=_require_str(metadata, "platform", path=path),
            python_version=_require_str(metadata, "python_version", path=path),
            test_module=_optional_str(metadata, "test_module", path=path),
            test_function=_optional_str(metadata, "test_function", path=path),
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
        """  # ruff: ignore[docstring-extraneous-exception] - _schema helpers and migrations raise this caller-visible error.
        root = _require_object(data, "fixture")
        migrated = _apply_migrations(dict(root))
        return cls(
            version=cls.SCHEMA_VERSION,
            metadata=FixtureMetadata.from_dict(_require_mapping(migrated, "metadata")),
            recordings=_parse_indexed(migrated, "recordings", RecordedInvocation),
            scrubbing_rules=_parse_indexed(migrated, "scrubbing_rules", ScrubbingRule),
        )

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
