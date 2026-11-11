"""Platform helpers shared across cmd-mox modules.

Centralising the logic keeps the supported/unsupported matrix in one place and
lets both the pytest plug-in and external test suites react consistently.

The ``platform`` parameter and the ``CMD_MOX_PLATFORM_OVERRIDE`` environment
variable are deprecated: they change only support classification, never shim
generation or IPC transport selection. Both remain functional for now so
existing callers keep working, but a future release will remove them.
"""

from __future__ import annotations

import os
import sys
import typing as typ
import warnings

# Deprecated: ``pytester``-driven tests used to set this override to emulate
# alternative platforms. It still feeds support classification, but it cannot
# change shim generation or IPC transport selection.
PLATFORM_OVERRIDE_ENV: typ.Final[str] = "CMD_MOX_PLATFORM_OVERRIDE"

# Map host identifier prefixes to user-facing skip reasons. The table is
# currently empty because every host cmd-mox recognises is supported; tests
# patch it to exercise the skip helpers without a genuinely unsupported host.
# Each entry pairs a prefix (as reported by ``sys.platform``) with the
# human-readable skip reason to surface to pytest users. Prefixes should match
# the start of ``sys.platform`` (e.g. ``"win"`` for ``"win32"``).
_UNSUPPORTED_PLATFORMS: typ.Final[tuple[tuple[str, str], ...]] = ()

_PYTEST_REQUIRED_MESSAGE: typ.Final[str] = (
    "pytest is required to automatically skip unsupported platforms. "
    "Install it with 'pip install pytest' or only call skip_if_unsupported within "
    "pytest."
)


def _normalize_platform(platform: str) -> str:
    """Return a normalized platform name suitable for prefix checks.

    Returns
    -------
    str
        *platform* stripped of surrounding whitespace and lower-cased.
    """
    return platform.strip().lower()


def _current_platform(platform: str | None = None) -> str:
    """Return the effective platform name.

    The explicit *platform* wins, then the deprecated override environment
    variable, then ``sys.platform``.

    Returns
    -------
    str
        The normalized explicit *platform*, the override environment variable,
        or ``sys.platform``, in that order of precedence.
    """
    if platform:
        return _normalize_platform(platform)

    if override := os.getenv(PLATFORM_OVERRIDE_ENV):
        return _normalize_platform(override)

    return _normalize_platform(sys.platform)


def _unsupported_reason_for(platform_name: str) -> str | None:
    """Return the first table reason matching *platform_name*, if any."""
    return next(
        (
            reason
            for prefix, reason in _UNSUPPORTED_PLATFORMS
            if platform_name.startswith(prefix)
        ),
        None,
    )


def _warn_deprecated_inputs(function_name: str, platform: str | None) -> None:
    """Warn once per deprecated input supplied to *function_name*.

    ``stacklevel=3`` attributes the warning to the caller of the public helper.
    When the pytest fixture is the caller, that lands on
    ``cmd_mox/pytest_plugin.py`` rather than on user code.
    """
    if platform is not None:
        warnings.warn(
            f"{function_name}() received the deprecated 'platform' argument. "
            "It only changes support classification; it does not change shim "
            "generation or IPC transport selection. It will be removed in a "
            "future release.",
            DeprecationWarning,
            stacklevel=3,
        )

    # The override is consulted only when the explicit argument is falsy.
    if not platform and os.getenv(PLATFORM_OVERRIDE_ENV):
        warnings.warn(
            f"{function_name}() consulted the deprecated "
            f"{PLATFORM_OVERRIDE_ENV} environment variable. It only changes "
            "support classification; it does not change shim generation or "
            "IPC transport selection. It will be removed in a future release.",
            DeprecationWarning,
            stacklevel=3,
        )


def unsupported_reason(platform: str | None = None) -> str | None:
    """Return the skip reason for a platform, or None when supported.

    Parameters
    ----------
    platform : str, optional
        Deprecated. A ``sys.platform``-like string. When ``None``, the current
        platform is used and the deprecated ``CMD_MOX_PLATFORM_OVERRIDE`` is
        honoured. Supplying either input emits a ``DeprecationWarning``.

    Returns
    -------
    str or None
        The user-facing reason if the platform is unsupported; otherwise
        ``None``.
    """
    _warn_deprecated_inputs("unsupported_reason", platform)
    return _unsupported_reason_for(_current_platform(platform))


def is_supported(platform: str | None = None) -> bool:
    """Return True when the platform supports cmd-mox.

    Parameters
    ----------
    platform : str, optional
        Deprecated. A ``sys.platform``-like string. When ``None``, the current
        platform is used and the deprecated ``CMD_MOX_PLATFORM_OVERRIDE`` is
        honoured. Supplying either input emits a ``DeprecationWarning``.

    Returns
    -------
    bool
        ``True`` if cmd-mox is supported; ``False`` if unsupported.
    """
    _warn_deprecated_inputs("is_supported", platform)
    return _unsupported_reason_for(_current_platform(platform)) is None


def skip_if_unsupported(
    *, reason: str | None = None, platform: str | None = None
) -> None:
    """Skip the current pytest test on unsupported platforms.

    Parameters
    ----------
    reason : str, optional
        Custom message to display when skipping. Used only if the platform is
        unsupported.
    platform : str, optional
        Deprecated. A ``sys.platform``-like string to evaluate. When ``None``,
        the current platform is used and the deprecated
        ``CMD_MOX_PLATFORM_OVERRIDE`` is honoured. Supplying either input emits
        a ``DeprecationWarning``.

    Raises
    ------
    RuntimeError
        If pytest cannot be imported in the current environment.
    """
    _warn_deprecated_inputs("skip_if_unsupported", platform)
    platform_reason = _unsupported_reason_for(_current_platform(platform))
    if platform_reason is None:
        return

    # Custom skip messages should only apply once we've confirmed cmd-mox is
    # unavailable on the requested platform. Supported systems must never skip
    # just because a caller provided a message.
    skip_reason = reason if reason is not None else platform_reason

    try:
        import pytest
    except ModuleNotFoundError as exc:  # pragma: no cover - pytest is a test dep
        raise RuntimeError(_PYTEST_REQUIRED_MESSAGE) from exc

    pytest.skip(skip_reason)


__all__ = (
    "PLATFORM_OVERRIDE_ENV",
    "is_supported",
    "skip_if_unsupported",
    "unsupported_reason",
)
