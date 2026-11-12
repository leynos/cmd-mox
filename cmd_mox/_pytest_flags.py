"""Resolve the ``cmd_mox`` fixture's configuration flags.

The pytest plugin owns the fixture lifecycle; this module owns the separate
question of which settings apply to the current test item. Keeping the
precedence rules here leaves the plugin focused on lifecycle management.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import pytest

# Fixture parameter dict keys the plugin understands. A dictionary must carry
# at least one of these so typos in a sole key are not silently ignored.
_PARAM_KEYS: typ.Final[frozenset[str]] = frozenset({"auto_lifecycle", "fail_fast"})


@dc.dataclass(slots=True, frozen=True)
class CmdMoxFlags:
    """Resolved fixture settings for one test item."""

    auto_lifecycle: bool
    fail_fast: bool


@dc.dataclass(slots=True)
class _FlagSources:
    """Configuration sources consulted in order of decreasing specificity."""

    config: pytest.Config
    param: dict[str, typ.Any] | None
    marker_kwargs: cabc.Mapping[str, typ.Any]

    def resolve(self, key: str, option: str) -> bool:
        """Return the most specific value for *key*, or the ini default.

        Parameters
        ----------
        key : str
            Setting name shared by the parameter dictionary and marker kwargs.
        option : str
            Name of the matching command-line and ini option.

        Returns
        -------
        bool
            The most specific configured value.
        """
        if self.param is not None and key in self.param:
            return bool(self.param[key])
        if key in self.marker_kwargs:
            return bool(self.marker_kwargs[key])
        cli_value = self.config.getoption(option)
        if cli_value is not None:
            return bool(cli_value)
        return bool(self.config.getini(option))


def resolve_flags(request: pytest.FixtureRequest) -> CmdMoxFlags:
    """Resolve the fixture flags from parameter, marker, CLI, then ini.

    Parameters
    ----------
    request : pytest.FixtureRequest
        The request supplying the fixture parameter, marker, and config.

    Returns
    -------
    CmdMoxFlags
        The resolved auto-lifecycle and fail-fast settings.
    """
    sources = _FlagSources(
        config=request.config,
        param=_param_overrides(request),
        marker_kwargs=_marker_kwargs(request),
    )
    return CmdMoxFlags(
        auto_lifecycle=sources.resolve("auto_lifecycle", "cmd_mox_auto_lifecycle"),
        fail_fast=sources.resolve("fail_fast", "cmd_mox_fail_fast"),
    )


def _marker_kwargs(request: pytest.FixtureRequest) -> cabc.Mapping[str, typ.Any]:
    """Return the closest ``cmd_mox`` marker's keyword arguments."""
    marker = request.node.get_closest_marker("cmd_mox")
    if marker is None:
        return {}
    return marker.kwargs


def _param_overrides(request: pytest.FixtureRequest) -> dict[str, typ.Any] | None:
    """Return the fixture parameter as a settings dictionary.

    A boolean parameter keeps its historical meaning and configures only the
    auto-lifecycle flag.

    Returns
    -------
    dict[str, Any] or None
        Parameter overrides, or ``None`` when no parameter is supplied.

    Raises
    ------
    TypeError
        If the parameter is a dictionary with no recognised key or has an
        unsupported type.
    """
    param = getattr(request, "param", None)
    match param:
        case None:
            return None
        case dict() as parameter:
            if not _PARAM_KEYS.intersection(parameter):
                keys = list(parameter.keys())
                msg = (
                    "cmd_mox fixture param dict must contain 'auto_lifecycle' "
                    f"or 'fail_fast' key, got keys: {keys}"
                )
                raise TypeError(msg)
            return typ.cast("dict[str, typ.Any]", parameter)
        case bool():
            return {"auto_lifecycle": param}
        case _:
            msg = (
                "cmd_mox fixture param must be a bool or dict with "
                f"'auto_lifecycle' or 'fail_fast' key, got {type(param).__name__}"
            )
            raise TypeError(msg)
