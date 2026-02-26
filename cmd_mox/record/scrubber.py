"""Scrubbing rule dataclass and Scrubber protocol for fixture sanitization.

The ``Scrubber`` protocol defines the interface for sanitizing recorded
invocations before persistence.  A concrete implementation is deferred to
the XII-C roadmap item; this module provides the stable type contract.
"""

from __future__ import annotations

import dataclasses as dc
import typing as t

if t.TYPE_CHECKING:
    from .fixture import RecordedInvocation


class ScrubbingRuleDict(t.TypedDict):
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
        """Return a JSON-serializable mapping."""
        return ScrubbingRuleDict(
            pattern=self.pattern,
            replacement=self.replacement,
            applied_to=list(self.applied_to),
            description=self.description or None,
        )

    @classmethod
    def from_dict(cls, data: ScrubbingRuleDict) -> ScrubbingRule:
        """Construct a rule from a JSON-compatible mapping."""
        return cls(
            pattern=str(data["pattern"]),
            replacement=str(data["replacement"]),
            applied_to=list(data.get("applied_to", ["env", "stdout", "stderr"])),
            description=str(data.get("description") or ""),
        )


@t.runtime_checkable
class Scrubber(t.Protocol):
    """Protocol for scrubbing sensitive data from recorded invocations."""

    def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
        """Return a sanitized copy of *recording*."""
        ...
