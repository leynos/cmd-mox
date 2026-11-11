"""Scrubbing rule dataclass and Scrubber protocol for fixture sanitization.

The ``Scrubber`` protocol defines the interface for sanitizing recorded
invocations before persistence.  A concrete implementation is deferred to
roadmap item 12.3.1; this module provides the stable type contract.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

from ._schema import _optional_str, _require_object, _require_str, _require_str_list

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .fixture import RecordedInvocation


class ScrubbingRuleDict(typ.TypedDict):
    """Typed dictionary shape for serialized scrubbing rules."""

    pattern: str
    replacement: str
    applied_to: list[str]
    description: str | None


@dc.dataclass(slots=True)
class ScrubbingRule:
    """A single pattern-replacement rule applied during scrubbing."""

    pattern: str
    replacement: str
    applied_to: list[str] = dc.field(
        default_factory=lambda: ["env", "stdout", "stderr"],
    )
    description: str = ""

    def to_dict(self) -> ScrubbingRuleDict:
        """Return a JSON-serializable mapping.

        Returns
        -------
        ScrubbingRuleDict
            Serialized scrubbing rule fields.
        """
        return ScrubbingRuleDict(
            pattern=self.pattern,
            replacement=self.replacement,
            applied_to=list(self.applied_to),
            description=self.description or None,
        )

    @classmethod
    def from_dict(
        cls, data: cabc.Mapping[str, object], *, path: str = ""
    ) -> ScrubbingRule:
        """Construct a rule from a JSON-compatible mapping.

        Parameters
        ----------
        data : collections.abc.Mapping[str, object]
            Serialized scrubbing rule fields.
        path : str
            Schema path used in validation errors.

        Returns
        -------
        ScrubbingRule
            Parsed scrubbing rule.

        Raises
        ------
        ValueError
            If a required field is missing or has the wrong type.
        """  # ruff: ignore[docstring-extraneous-exception] - _schema helpers raise this caller-visible error.
        rule = _require_object(data, path)
        return cls(
            pattern=_require_str(rule, "pattern", path=path),
            replacement=_require_str(rule, "replacement", path=path),
            applied_to=_require_str_list(rule, "applied_to", path=path),
            description=_optional_str(rule, "description", path=path) or "",
        )


@typ.runtime_checkable
class Scrubber(typ.Protocol):
    """Protocol for scrubbing sensitive data from recorded invocations."""

    def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
        """Return a sanitized copy of *recording*."""
