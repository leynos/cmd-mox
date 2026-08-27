"""Python-native command mocking built around a record-replay-verify lifecycle.

For an overview of the architecture and guiding design, see the project
documentation (`https://github.com/leynos/cmd-mox/blob/main/docs/contents.md`).
"""

from __future__ import annotations

import importlib
import typing as typ

from .comparators import Any, Contains, IsA, Predicate, Regex, StartsWith
from .controller import CmdMox
from .environment import EnvironmentManager, temporary_env
from .errors import (
    CmdMoxError,
    LifecycleError,
    MissingEnvironmentError,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
    VerificationError,
)
from .expectations import Expectation
from .ipc import Invocation, IPCServer, NamedPipeServer, Response
from .platform import (
    PLATFORM_OVERRIDE_ENV,
    is_supported,
    skip_if_unsupported,
    unsupported_reason,
)
from .shimgen import SHIM_PATH, create_shim_symlinks
from .test_doubles import CommandDouble, MockCommand, SpyCommand, StubCommand

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from types import ModuleType as _ModuleType
else:  # pragma: no cover - typing fallback only
    _ModuleType = type(importlib)


@typ.overload
def __getattr__(
    name: typ.Literal["cmd_mox_fixture"],
) -> cabc.Callable[..., object]: ...


@typ.overload
def __getattr__(name: str) -> _ModuleType: ...


def __getattr__(name: str) -> _ModuleType | cabc.Callable[..., object]:
    """Lazily import optional dependencies when requested.

    Parameters
    ----------
    name : str
        Module attribute to resolve.

    Returns
    -------
    types.ModuleType or collections.abc.Callable[..., object]
        Imported submodule, or the ``cmd_mox`` pytest fixture callable when
        ``name`` is ``"cmd_mox_fixture"``.

    Raises
    ------
    AttributeError
        If ``name`` does not identify a submodule in :mod:`cmd_mox`.
    RuntimeError
        If resolving ``"cmd_mox_fixture"`` fails because an optional
        dependency of the pytest plugin is unavailable.
    ModuleNotFoundError
        If importing a requested submodule fails because a dependency other
        than the requested module is unavailable.
    """
    if name == "cmd_mox_fixture":
        try:
            from .pytest_plugin import cmd_mox as _cmd_mox_fixture
        except ModuleNotFoundError as exc:  # pytest optional at runtime
            msg = (
                "cmd_mox_fixture requires pytest; install 'pytest' to use the fixture."
            )
            raise RuntimeError(msg) from exc
        globals()[name] = _cmd_mox_fixture
        return _cmd_mox_fixture

    try:
        module = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name in {f"{__name__}.{name}", name}:
            raise AttributeError(name) from exc
        raise

    globals()[name] = module
    return module


__all__ = [
    "PLATFORM_OVERRIDE_ENV",
    "SHIM_PATH",
    "Any",
    "CmdMox",
    "CmdMoxError",
    "CommandDouble",
    "Contains",
    "EnvironmentManager",
    "Expectation",
    "IPCServer",
    "Invocation",
    "IsA",
    "LifecycleError",
    "MissingEnvironmentError",
    "MockCommand",
    "NamedPipeServer",
    "Predicate",
    "Regex",
    "Response",
    "SpyCommand",
    "StartsWith",
    "StubCommand",
    "UnexpectedCommandError",
    "UnfulfilledExpectationError",
    "VerificationError",
    "cmd_mox_fixture",  # ruff: ignore[undefined-export] - resolved lazily by module __getattr__ so importing cmd_mox does not require pytest
    "create_shim_symlinks",
    "is_supported",
    "skip_if_unsupported",
    "temporary_env",
    "unsupported_reason",
]
