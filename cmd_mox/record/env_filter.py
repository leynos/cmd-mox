"""Environment variable subset filtering for fixture recordings.

Recordings capture only a meaningful subset of the process environment to
avoid bloating fixtures with irrelevant system paths and to prevent leaking
secrets.  The filtering strategy follows the design spec Section 9.3.3 and
Section 9.9.2.
"""

from __future__ import annotations

import typing as t

from cmd_mox.expectations import is_sensitive_recording_env_key

# System-specific keys that are excluded by default.
EXCLUDED_SYSTEM_KEYS: t.Final[frozenset[str]] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "SSH_AUTH_SOCK",
        "GPG_AGENT_INFO",
    }
)

# CmdMox internal keys that must never appear in fixtures.
_CMOX_ENV_PREFIX: t.Final[str] = "CMOX_"
_CMD_MOX_ENV_PREFIX: t.Final[str] = "CMD_MOX_"

# Known command-specific prefixes: command name -> env var prefix.
COMMAND_ENV_PREFIXES: t.Final[dict[str, str]] = {
    "git": "GIT_",
    "aws": "AWS_",
    "docker": "DOCKER_",
    "npm": "NPM_",
    "pip": "PIP_",
    "cargo": "CARGO_",
    "go": "GO",
    "rustc": "RUSTC_",
}


def _is_cmox_internal(key: str) -> bool:
    """Return True if *key* is a CmdMox-internal environment variable."""
    return key.startswith(_CMOX_ENV_PREFIX) or key.startswith(_CMD_MOX_ENV_PREFIX)


def _should_include_env_key(
    key: str,
    *,
    explicit: set[str],
    allow: set[str],
    cmd_prefix: str,
) -> bool:
    """Return True if *key* should be included in the filtered subset."""
    # CmdMox internals are never recorded, even if allowlisted or explicit.
    if _is_cmox_internal(key):
        return False

    # Explicitly requested keys always pass through.
    if key in explicit or key in allow:
        return True

    # System keys are excluded.
    if key in EXCLUDED_SYSTEM_KEYS:
        return False

    # Sensitive keys (password, token, secret, key, credentials, etc.) are
    # excluded even if they share a command-specific prefix.
    if is_sensitive_recording_env_key(key):
        return False

    # Command-specific prefix keys are included.
    if cmd_prefix and key.startswith(cmd_prefix):
        return True

    # When a command is specified, exclude keys that don't match the prefix.
    # When no command is specified, include all remaining non-excluded keys.
    return not cmd_prefix


def filter_env_subset(
    env: dict[str, str],
    *,
    command: str = "",
    allowlist: list[str] | None = None,
    explicit_keys: list[str] | None = None,
) -> dict[str, str]:
    """Return a filtered subset of *env* suitable for fixture persistence.

    Parameters
    ----------
    env : dict[str, str]
        The full environment dictionary to filter.
    command : str
        The command name, used to include matching prefix keys
        (e.g. ``"git"`` includes ``GIT_*`` variables).
    allowlist : list[str] | None
        Additional keys to always include regardless of other rules.
    explicit_keys : list[str] | None
        Keys explicitly requested via ``.with_env()``; always included.
    """
    allow = set(allowlist or [])
    explicit = set(explicit_keys or [])
    cmd_prefix = COMMAND_ENV_PREFIXES.get(command.lower(), "")

    return {
        key: value
        for key, value in env.items()
        if _should_include_env_key(
            key, explicit=explicit, allow=allow, cmd_prefix=cmd_prefix
        )
    }
