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

# Both deprecation warnings share this explanation, so callers see the same
# bounded guarantee regardless of which deprecated input triggered the warning.
_DEPRECATION_EFFECT_NOTE: typ.Final[str] = (
    "It only changes support classification; it does not change shim generation "
    "or IPC transport selection. It will be removed in a future release."
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

    ``stacklevel=4`` attributes the warning to the caller of the public helper.
    When the pytest fixture is the caller, that lands on
    ``cmd_mox/pytest_plugin.py`` rather than on user code.
    """
    if platform is not None:
        warnings.warn(
            f"{function_name}() received the deprecated 'platform' argument. "
            f"{_DEPRECATION_EFFECT_NOTE}",
            DeprecationWarning,
            stacklevel=4,
        )

    # The override is consulted only when the explicit argument is falsy.
    if not platform and os.getenv(PLATFORM_OVERRIDE_ENV):
        warnings.warn(
            f"{function_name}() consulted the deprecated "
            f"{PLATFORM_OVERRIDE_ENV} environment variable. "
            f"{_DEPRECATION_EFFECT_NOTE}",
            DeprecationWarning,
            stacklevel=4,
        )


def _classify_platform(platform: str | None, *, function_name: str) -> str | None:
    """Warn about deprecated inputs, then classify the effective platform."""
    _warn_deprecated_inputs(function_name, platform)
    return _unsupported_reason_for(_current_platform(platform))


def unsupported_reason(platform: str | None = None) -> str | None:
    """Return the skip reason for *platform*, or ``None`` when supported.

    The reason comes from the first matching entry in the unsupported-platform
    table. Classification consults the deprecated ``platform`` argument, then
    the deprecated ``CMD_MOX_PLATFORM_OVERRIDE``, then ``sys.platform``, and
    emits a ``DeprecationWarning`` when either deprecated input is used.

    Parameters
    ----------
    platform : str, optional
        Deprecated host identifier, such as ``"win32"``.

    Returns
    -------
    str or None
        The configured reason when unsupported; otherwise ``None``.
    """
    return _classify_platform(platform, function_name="unsupported_reason")


def is_supported(platform: str | None = None) -> bool:
    """Return whether cmd-mox supports the effective platform.

    This boolean view shares :func:`unsupported_reason`'s classification and
    deprecation rules, so the two helpers cannot disagree about a host.

    Parameters
    ----------
    platform : str, optional
        Deprecated alias for the host identifier to classify. ``None`` uses the
        override, when set, then ``sys.platform``.

    Returns
    -------
    bool
        ``True`` when no unsupported-platform rule matches; otherwise ``False``.
    """
    reason = _classify_platform(platform, function_name="is_supported")
    return reason is None


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
    platform_reason = _classify_platform(platform, function_name="skip_if_unsupported")
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
