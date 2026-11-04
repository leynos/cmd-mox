"""Shared validation for bounded correlation identifiers."""

from __future__ import annotations

import re
import typing as typ

MAX_CORRELATION_ID_LENGTH: typ.Final[int] = 64
_CORRELATION_ID_PATTERN: typ.Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+")


def bounded_correlation_id(value: object) -> str | None:
    """Return *value* when it is a usable, bounded correlation identifier.

    Identifiers are opaque and supplied by the caller, so an absent, empty,
    non-string, over-long, or non-conforming value is treated as no identifier
    at all. Rejected values are dropped rather than truncated or sanitized in
    place: a repaired identifier is a forged one, and two distinct identifiers
    sharing a prefix would collapse onto one, silently correlating unrelated
    records.

    The character bound is defence in depth rather than a demonstrated
    injection. The identifier never enters the bounded counter key, and the log
    message is a fixed string, so the only exposure is the structured
    ``extra=`` field a downstream formatter may render verbatim.

    Parameters
    ----------
    value:
        Candidate identifier taken from a wire envelope or a validated model.

    Returns
    -------
    str or None
        The identifier, or ``None`` when it is unusable.
    """
    if not isinstance(value, str) or len(value) > MAX_CORRELATION_ID_LENGTH:
        return None
    # The length is checked first so the pattern only ever scans a short string.
    if _CORRELATION_ID_PATTERN.fullmatch(value) is None:
        return None
    return value
