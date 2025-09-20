"""Platform helpers shared across cmd-mox modules.

Centralising the logic keeps the supported/unsupported matrix in one place and
lets both the pytest plug-in and external test suites react consistently.
"""

from __future__ import annotations

import os
import sys
import typing as t

# ``pytester``-driven tests set this override to emulate alternative platforms
# (for example Windows) without needing to spawn a different OS.
PLATFORM_OVERRIDE_ENV: t.Final[str] = "CMD_MOX_PLATFORM_OVERRIDE"

# Map ``sys.platform`` prefixes to user-facing skip reasons. Only Windows is
# unsupported today, but the structure keeps future additions focused here.
# Each entry pairs the prefix (as reported by ``sys.platform``) with the
# human-readable skip reason to surface to pytest users. Prefixes should match
# the start of ``sys.platform`` (e.g. ``"win"`` for ``"win32"``).
_UNSUPPORTED_PLATFORMS: t.Final[tuple[tuple[str, str], ...]] = (
    ("win", "cmd-mox does not support Windows"),
)

_PYTEST_REQUIRED_MESSAGE: t.Final[str] = (
    "pytest is required to automatically skip unsupported platforms."
)


def _normalise(platform: str) -> str:
    """Return a lowercase version of *platform* suitable for prefix checks."""
    return platform.strip().lower()


def _current_platform(platform: str | None = None) -> str:
    """Return the effective platform name, honouring test overrides."""
    if platform:
        return _normalise(platform)

    if override := os.getenv(PLATFORM_OVERRIDE_ENV):
        return _normalise(override)

    return _normalise(sys.platform)


def unsupported_reason(platform: str | None = None) -> str | None:
    """Return the skip reason for *platform*, or ``None`` when supported."""
    platform_name = _current_platform(platform)
    return next(
        (
            reason
            for prefix, reason in _UNSUPPORTED_PLATFORMS
            if platform_name.startswith(prefix)
        ),
        None,
    )


def is_supported(platform: str | None = None) -> bool:
    """Return ``True`` when *platform* (default: current) supports cmd-mox."""
    return unsupported_reason(platform) is None


def skip_if_unsupported(
    *, reason: str | None = None, platform: str | None = None
) -> None:
    """Skip the current pytest test if cmd-mox is unavailable on *platform*."""
    skip_reason = unsupported_reason(platform)
    if skip_reason is None:
        return

    if reason is not None:
        skip_reason = reason

    try:
        import pytest
    except ModuleNotFoundError as exc:  # pragma: no cover - pytest is a test dep
        raise RuntimeError(_PYTEST_REQUIRED_MESSAGE) from exc

    pytest.skip(skip_reason)


__all__ = [
    "PLATFORM_OVERRIDE_ENV",
    "is_supported",
    "skip_if_unsupported",
    "unsupported_reason",
]
