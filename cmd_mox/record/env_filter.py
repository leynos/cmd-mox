"""Environment variable subset filtering for fixture recordings.

Recordings capture only a meaningful subset of the process environment to
avoid bloating fixtures with irrelevant system paths and to prevent leaking
secrets.  The filtering strategy follows the design spec Section 9.3.3 and
Section 9.9.2.
"""

from __future__ import annotations

import typing as typ

from cmd_mox.domain import is_sensitive_recording_env_key

# System-specific keys that are excluded by default.
EXCLUDED_SYSTEM_KEYS: typ.Final[frozenset[str]] = frozenset({
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "SSH_AUTH_SOCK",
    "GPG_AGENT_INFO",
})

# CmdMox internal keys that must never appear in fixtures.
_CMOX_ENV_PREFIX: typ.Final[str] = "CMOX_"
_CMD_MOX_ENV_PREFIX: typ.Final[str] = "CMD_MOX_"

# Known command-specific prefixes: command name -> env var prefix.
COMMAND_ENV_PREFIXES: typ.Final[dict[str, str]] = {
    "git": "GIT_",
    "aws": "AWS_",
    "docker": "DOCKER_",
    "npm": "NPM_",
    "pip": "PIP_",
    "cargo": "CARGO_",
    "rustc": "RUSTC_",
}

# ``go`` has no separator-bearing prefix.  Its variables are documented as
# individual names, and a bare ``GO`` prefix would also capture unrelated
# names such as ``GOOGLE_CLOUD_PROJECT``.  This list is drawn from the Go
# toolchain and runtime variables in ``go help environment`` and
# ``go doc runtime``.  It changes between Go releases, so users must
# allowlist any newer name that is absent here.
GO_ENV_KEYS: typ.Final[frozenset[str]] = frozenset({
    "GO111MODULE",
    "GO386",
    "GOAMD64",
    "GOARCH",
    "GOARM",
    "GOARM64",
    "GOAUTH",
    "GOBIN",
    "GOCACHE",
    "GOCACHEPROG",
    "GOCOVERDIR",
    "GODEBUG",
    "GOENV",
    "GOEXPERIMENT",
    "GOFIPS140",
    "GOFLAGS",
    "GOGC",
    "GOINSECURE",
    "GOMAXPROCS",
    "GOMEMLIMIT",
    "GOMIPS",
    "GOMIPS64",
    "GOMODCACHE",
    "GONOPROXY",
    "GONOSUMDB",
    "GOOS",
    "GOPATH",
    "GOPPC64",
    "GOPRIVATE",
    "GOPROXY",
    "GORISCV64",
    "GOROOT",
    "GOSUMDB",
    "GOTMPDIR",
    "GOTOOLCHAIN",
    "GOTRACEBACK",
    "GOVCS",
    "GOWASM",
    "GOWORK",
})

# Known command-specific exact names: command name -> accepted env var names.
# Commands absent from this mapping use their prefix rule instead.
COMMAND_ENV_KEYS: typ.Final[dict[str, frozenset[str]]] = {
    "go": GO_ENV_KEYS,
}


def _is_cmox_internal(key: str) -> bool:
    """Return whether *key* is a CmdMox-internal environment variable.

    Returns
    -------
    bool
        ``True`` when *key* carries one of the CmdMox-internal prefixes.
    """
    return key.startswith(_CMOX_ENV_PREFIX) or key.startswith(_CMD_MOX_ENV_PREFIX)


def _should_include_env_key(
    key: str,
    *,
    requested: set[str],
    exact_keys: frozenset[str] | None,
    cmd_prefix: str,
) -> bool:
    """Return whether *key* should be included in the filtered subset.

    Returns
    -------
    bool
        ``True`` when *key* survives the internal, requested, system,
        sensitive and command-matching filters.
    """
    # CmdMox internals are never recorded, even if requested.
    if _is_cmox_internal(key):
        return False

    # Requested keys pass through, whether named in ``allowlist`` or
    # ``explicit_keys``.
    if key in requested:
        return True

    # System keys are excluded.  Sensitive keys (password, token, secret,
    # key, credentials, etc.) are excluded too, even if they share a
    # command-specific prefix or name.
    if key in EXCLUDED_SYSTEM_KEYS or is_sensitive_recording_env_key(key):
        return False

    # Exact-name commands accept only the variables named for them.  An empty
    # set stays restrictive rather than falling through to the permissive
    # unknown-command rule.
    if exact_keys is not None:
        return key in exact_keys

    # Command-specific prefix keys are included.
    if cmd_prefix:
        return key.startswith(cmd_prefix)

    # When no command is specified, include all remaining non-excluded keys.
    return True


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
        The command name, matched case-insensitively.  Most commands include
        keys under their prefix (for example ``"git"`` includes ``GIT_*``
        variables).  ``"go"`` includes only the documented names in
        :data:`GO_ENV_KEYS`.  An unrecognized command includes every key that
        is not excluded by another rule.
    allowlist : list[str] | None
        Caller-configured keys to retain, such as
        ``RecordingSession(env_allowlist=...)``.  A key must be present in
        *env* to appear in the result.  ``allowlist`` and *explicit_keys*
        share one precedence and their union is applied.
    explicit_keys : list[str] | None
        Keys requested through ``.with_env()``.  Treated exactly like
        *allowlist*: an entry in either keyword includes the key.  CmdMox
        internal keys (``CMOX_*``, ``CMD_MOX_*``) remain excluded even when
        requested.

    Returns
    -------
    dict[str, str]
        The keys that are safe and relevant to persist in a fixture.
    """
    requested = set(allowlist or []) | set(explicit_keys or [])
    normalized_command = command.lower()
    cmd_prefix = COMMAND_ENV_PREFIXES.get(normalized_command, "")
    exact_keys = COMMAND_ENV_KEYS.get(normalized_command)

    return {
        key: value
        for key, value in env.items()
        if _should_include_env_key(
            key,
            requested=requested,
            exact_keys=exact_keys,
            cmd_prefix=cmd_prefix,
        )
    }
