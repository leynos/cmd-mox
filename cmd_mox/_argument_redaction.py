"""Classify and redact credential-bearing command arguments.

These helpers mask supported secret-like option values, credential headers,
URL userinfo and credential-bearing query parameters, and embedded
``KEY=value`` assignments in configuration diagnostics. Argument positions
are preserved so conflict messages retain context.

Examples
--------
>>> redact_sensitive_argument_values(["--token", "opaque", "--output", "json"])
['--token', '<redacted>', '--output', 'json']
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import PureWindowsPath

if typ.TYPE_CHECKING:
    import collections.abc as cabc

SENSITIVE_ENV_KEY_TOKENS: typ.Final[tuple[str, ...]] = (
    "secret",
    "token",
    "api_key",
    "password",
)
# Pre-normalize tokens once for case-insensitive checks
_SENSITIVE_TOKENS: typ.Final[tuple[str, ...]] = tuple(
    tok.casefold() for tok in SENSITIVE_ENV_KEY_TOKENS
)
_AUTHENTICATION_ARGUMENT_OPTIONS: typ.Final[frozenset[str]] = frozenset({
    "--user",
    "--proxy-user",
    "--oauth2-bearer",
    "--cookie",
    "--authorization",
})
_SHORT_AUTHENTICATION_ARGUMENT_OPTIONS: typ.Final[frozenset[str]] = frozenset({
    "-u",
    "-b",
})
_CURL_PROXY_USER_SHORT_OPTION: typ.Final[str] = "-U"

# Comprehensive regex for secret-bearing env key segments. Also recognizes the
# compact ``APIKEY`` spelling used by some command-line options.
_SECRET_ENV_KEY_RE: typ.Final[re.Pattern[str]] = re.compile(
    r"(?i)(^|[_-])(?:API)?(KEY|TOKEN|SECRET|PASSWORD|PASSPHRASE|CREDENTIALS?"
    r"|PASS(?:WORD)?|PWD)(?=[_-]|=|:|\d|$)"
)
_AUTHORIZATION_CREDENTIAL_RE: typ.Final[re.Pattern[str]] = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*[^,;]+"
)
_COOKIE_HEADER_NAMES: typ.Final[frozenset[str]] = frozenset({"cookie", "set-cookie"})
_JOINED_SHORT_AUTHENTICATION_OPTIONS: typ.Final[frozenset[str]] = frozenset({
    "-u",
    "-b",
})
_JOINED_SHORT_HEADER_OPTION: typ.Final[str] = "-H"
_CREDENTIAL_HEADER_NAME_RE: typ.Final[re.Pattern[str]] = re.compile(
    r"(?i)^\s*(?P<name>[a-z0-9][a-z0-9_-]*)\s*:"
)
_URL_USERINFO_RE: typ.Final[re.Pattern[str]] = re.compile(
    r"(?i)(?P<prefix>\b[a-z][a-z0-9+.-]*://)[^/?#\s]+@"
)
_URL_QUERY_PARAMETER_RE: typ.Final[re.Pattern[str]] = re.compile(
    r"(?P<separator>[?&;])(?P<key>[^=&#;\s]+)=(?P<value>[^&#;\s]*)"
)


def _is_sensitive_credential_header(arg: str) -> bool:
    """Return whether an argument is a credential-bearing header.

    Returns
    -------
    bool
        ``True`` when the argument names a sensitive header carrying a value.
    """
    header = _CREDENTIAL_HEADER_NAME_RE.match(arg)
    return bool(
        header
        and (
            _AUTHORIZATION_CREDENTIAL_RE.search(arg)
            or header.group("name").casefold() in _COOKIE_HEADER_NAMES
            or _SECRET_ENV_KEY_RE.search(header.group("name"))
        )
    )


def _redact_url_query_parameter(match: re.Match[str]) -> str:
    """Mask a URL query value when its key names a credential.

    Returns
    -------
    str
        The original query pair or one with its credential value masked.
    """
    key = match.group("key")
    if not is_sensitive_recording_env_key(key):
        return match.group(0)
    return f"{match.group('separator')}{key}=<redacted>"


def _redact_url_credentials(arg: str) -> str:
    """Hide URL userinfo and secret-bearing query parameter values.

    Returns
    -------
    str
        The argument with URL credentials masked.
    """
    safe_arg = _URL_USERINFO_RE.sub(r"\g<prefix><redacted>@", arg)
    return _URL_QUERY_PARAMETER_RE.sub(_redact_url_query_parameter, safe_arg)


def _is_sensitive_env_key(key: str) -> bool:
    """Return True if key likely holds secret material (substring match).

    Returns
    -------
    bool
        ``True`` when *key* contains one of the sensitive substrings.
    """
    k = key.casefold()
    return any(tkn in k for tkn in _SENSITIVE_TOKENS)


def is_sensitive_recording_env_key(key: str) -> bool:
    """Return True if *key* should be treated as secret-bearing for recordings.

    Combines the substring-based check from :func:`_is_sensitive_env_key` with
    a regex that catches word-segment patterns such as ``GITHUB_KEY`` or
    ``DB_PWD``.

    Returns
    -------
    bool
        Whether recordings should redact the environment variable's value.
    """
    return _is_sensitive_env_key(key) or bool(_SECRET_ENV_KEY_RE.search(key))


def _partition_option_argument(arg: str) -> tuple[str, str, str]:
    """Split a dash-prefixed option at its first supported separator.

    Returns
    -------
    tuple[str, str, str]
        Option, separator, and value from the argument.
    """
    option, separator, value = arg.partition(":")
    if separator:
        equals_index = option.find("=")
        if equals_index < 0:
            return option, separator, value
    return arg.partition("=")


def _is_sensitive_authentication_option(option: str, command: str | None) -> bool:
    """Recognize sensitive short and long authentication options.

    Parameters
    ----------
    option : str
        Option name without its inline value.
    command : str or None
        Executable name or path.

    Returns
    -------
    bool
        Whether the option is a credential-bearing authentication option.
    """
    if option in _SHORT_AUTHENTICATION_ARGUMENT_OPTIONS:
        return True
    if option.startswith("--"):
        return option.casefold() in _AUTHENTICATION_ARGUMENT_OPTIONS
    return (
        _command_basename(command) == "curl" and option == _CURL_PROXY_USER_SHORT_OPTION
    )


def _classify_sensitive_argument_option(
    arg: str, command: str | None = None
) -> tuple[str, str, bool]:
    """Classify an argument option and report whether its name is sensitive.

    Returns
    -------
    tuple[str, str, bool]
        Option name, separator if present, and sensitivity flag.
    """
    # A standalone header must be classified before splitting on ``=``; base64
    # padding would otherwise leave part of its credential in the option name.
    if not arg.startswith("-") and _is_sensitive_credential_header(arg):
        return arg, "", False

    option, separator, _value = arg.partition("=")
    if arg.startswith("-"):
        option, separator, _value = _partition_option_argument(arg)
    is_option = arg.startswith("-") or bool(separator)
    is_sensitive_option = is_option and (
        _is_sensitive_authentication_option(option, command)
        or is_sensitive_recording_env_key(option)
    )
    return option, separator, is_sensitive_option


def _contains_sensitive_argument_value(arg: str) -> bool:
    """Return whether an argument embeds a credential value.

    Returns
    -------
    bool
        Whether the argument contains a recognized credential marker.
    """
    if _is_sensitive_credential_header(arg):
        return True

    _option, separator, value = arg.partition("=")
    if separator:
        return bool(
            _SECRET_ENV_KEY_RE.search(value)
            or _AUTHORIZATION_CREDENTIAL_RE.search(arg)
            or _is_sensitive_credential_header(value)
        )
    return bool(_AUTHORIZATION_CREDENTIAL_RE.search(arg))


def _format_redacted_argument_value(option: str, separator: str) -> str:
    """Format a redacted argument value while preserving an inline option.

    Returns
    -------
    str
        The redacted argument representation.
    """
    if separator:
        return f"{option}{separator}<redacted>"
    return "<redacted>"


def _is_joined_short_authentication_option(
    arg: str, command: str | None = None
) -> bool:
    """Return whether *arg* joins a short authentication option to its value.

    Parameters
    ----------
    arg : str
        Command argument to check.
    command : str or None, optional
        Executable name or path, used to preserve known non-secret options.

    Returns
    -------
    bool
        Whether the argument begins with a short authentication option and
        continues directly into its value.
    """
    if _is_go_benchmark_option(arg, command):
        return False
    if _is_joined_curl_proxy_user_option(arg, command):
        return True
    return any(
        len(arg) > len(option) and arg.startswith(option) and arg[len(option)] != "="
        for option in _JOINED_SHORT_AUTHENTICATION_OPTIONS
    )


def _is_joined_curl_proxy_user_option(arg: str, command: str | None) -> bool:
    """Recognize curl's joined uppercase proxy-user credential option.

    Parameters
    ----------
    arg : str
        Command argument to check.
    command : str or None
        Executable name or path.

    Returns
    -------
    bool
        Whether the argument joins curl's ``-U`` option to its value.
    """
    return (
        _command_basename(command) == "curl"
        and len(arg) > len(_CURL_PROXY_USER_SHORT_OPTION)
        and arg.startswith(_CURL_PROXY_USER_SHORT_OPTION)
        and arg[len(_CURL_PROXY_USER_SHORT_OPTION)] != "="
    )


def _command_basename(command: str | None) -> str | None:
    """Return a case-folded executable name for command-aware exceptions.

    Parameters
    ----------
    command : str or None
        Executable name or path.

    Returns
    -------
    str or None
        Case-folded executable basename, or ``None`` when not supplied.
    """
    if command is None:
        return None
    return PureWindowsPath(command).name.casefold().removesuffix(".exe")


def _is_go_benchmark_option(arg: str, command: str | None) -> bool:
    """Return whether a Go benchmark flag is being redacted as a user value.

    Parameters
    ----------
    arg : str
        Command argument to check.
    command : str or None
        Executable name or path.

    Returns
    -------
    bool
        Whether the argument is a Go benchmark flag.
    """
    return _command_basename(command) == "go" and arg.startswith("-bench=")


def _is_known_non_secret_short_option(
    command: str | None, args: list[str], index: int
) -> bool:
    """Recognize short options whose values are ordinary in known commands.

    Parameters
    ----------
    command : str or None
        Executable name or path.
    args : list[str]
        Arguments being redacted.
    index : int
        Position of the argument to classify.

    Returns
    -------
    bool
        Whether this position is a known non-secret short option.
    """
    executable = _command_basename(command)
    arg = args[index]
    if executable == "git" and index > 0:
        operation = args[0]
        return (operation == "push" and arg == "-u") or (
            operation == "checkout" and arg == "-b"
        )
    return False


def _is_joined_sensitive_header_argument(arg: str) -> bool:
    """Return whether *arg* joins ``-H`` to a sensitive header.

    Parameters
    ----------
    arg : str
        Command argument to check.

    Returns
    -------
    bool
        Whether the argument contains a sensitive header after the short
        ``-H`` option.
    """
    if not arg.startswith(_JOINED_SHORT_HEADER_OPTION):
        return False
    return _is_sensitive_credential_header(arg[len(_JOINED_SHORT_HEADER_OPTION) :])


def _redact_embedded_value_or_keep(arg: str, option: str, separator: str) -> str:
    """Mask credential markers embedded in an otherwise ordinary argument.

    Returns
    -------
    str
        A redacted argument or the original safe argument.
    """
    if not _contains_sensitive_argument_value(arg):
        return arg
    return _format_redacted_argument_value(option, separator)


def _redact_current_argument(
    arg: str,
    classification: tuple[str, str, bool],
    *,
    command: str | None,
) -> str:
    """Redact inline sensitive options and embedded credential values.

    Returns
    -------
    str
        The diagnostic-safe argument representation.
    """
    option, separator, is_sensitive_option = classification
    if is_sensitive_option and separator:
        return _format_redacted_argument_value(option, separator)
    if _is_joined_sensitive_header_argument(arg):
        return f"{_JOINED_SHORT_HEADER_OPTION}<redacted>"
    if _is_joined_short_authentication_option(arg, command):
        return f"{arg[:2]}<redacted>"
    return _redact_embedded_value_or_keep(arg, option, separator)


def _iter_redacted_argument_values(
    args: list[str], command: str | None = None
) -> cabc.Iterator[str]:
    """Yield arguments with values for secret-like options hidden.

    Parameters
    ----------
    args : list[str]
        Command arguments to make safe for diagnostic output.

    Yields
    ------
    str
        Original arguments or ``<redacted>`` placeholders, preserving positions.
    """
    redact_next_value = False
    for index, arg in enumerate(args):
        safe_arg = _redact_url_credentials(arg)
        option, separator, is_sensitive_option = _classify_sensitive_argument_option(
            safe_arg, command
        )
        is_known_non_secret_option = _is_known_non_secret_short_option(
            command, args, index
        )
        if is_known_non_secret_option:
            is_sensitive_option = False
        if redact_next_value:
            yield "<redacted>"
        else:
            yield _redact_current_argument(
                safe_arg,
                (option, separator, is_sensitive_option),
                command=command,
            )
        # Preserve chained sensitive options after consuming their current value.
        redact_next_value = is_sensitive_option and not separator


def redact_sensitive_argument_values(
    args: list[str], *, command: str | None = None
) -> list[str]:
    """Return diagnostic-safe arguments with sensitive option values masked.

    Parameters
    ----------
    args : list[str]
        Command arguments to make safe for diagnostic output.
    command : str or None, optional
        Executable name, used to preserve known non-secret short options.

    Returns
    -------
    list[str]
        Arguments with sensitive option values replaced by ``<redacted>``.

    Examples
    --------
    >>> redact_sensitive_argument_values(["--token", "opaque", "--output", "json"])
    ['--token', '<redacted>', '--output', 'json']
    """
    return list(_iter_redacted_argument_values(args, command))
