"""Shared stubs for pytest-plugin manager tests.

The stubs live here so that the manager test modules stay within the
project's module-size ceiling and can share one faithful stand-in for
``pytest``'s runtime objects.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest

from cmd_mox.controller import Phase
from cmd_mox.pytest_plugin import _CmdMoxManager

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox import pytest_plugin
    from cmd_mox.environment import EnvironmentManager

    type CmdMoxFixtureGenerator = cabc.Callable[
        [pytest.FixtureRequest],
        cabc.Generator[pytest_plugin.CmdMox, None, None],
    ]

VERIFY_ERROR_MESSAGE = "verify boom"
EXIT_ERROR_MESSAGE = "exit boom"


@dc.dataclass(slots=True, kw_only=True)
class StubConfig:
    """Mimic ``pytest.Config`` with controllable plugin settings."""

    workerinput: object | None = None
    cli: bool | None = None
    ini: bool = True
    fail_fast_cli: bool | None = None
    fail_fast_ini: bool = False
    _option_values: dict[str, bool | None] = dc.field(init=False, repr=False)
    _ini_values: dict[str, bool | None] = dc.field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Build the option and ini lookup tables from the configured values."""
        self._option_values = {
            "cmd_mox_auto_lifecycle": self.cli,
            "cmd_mox_fail_fast": self.fail_fast_cli,
        }
        self._ini_values = {
            "cmd_mox_auto_lifecycle": self.ini,
            "cmd_mox_fail_fast": self.fail_fast_ini,
        }

    def getoption(self, name: str) -> bool | None:
        """Return the command-line value of the named option."""
        return self._lookup(self._option_values, name, "option")

    def getini(self, name: str) -> bool:
        """Return the ini-file value of the named option."""
        # Ini values are always booleans by construction, unlike CLI overrides.
        return typ.cast("bool", self._lookup(self._ini_values, name, "ini option"))

    @staticmethod
    def _lookup(values: dict[str, bool | None], name: str, label: str) -> bool | None:
        """Return *name* from *values*, rejecting unknown settings."""
        try:
            return values[name]
        except KeyError:  # pragma: no cover - guards against unexpected options
            msg = f"unexpected {label}: {name}"
            raise AssertionError(msg) from None


class StubMarker:
    """Simple marker surrogate exposing keyword arguments."""

    __slots__ = ("kwargs",)

    def __init__(self, **kwargs: object) -> None:
        """Store the marker keyword arguments."""
        self.kwargs = kwargs


class StubNode:
    """Minimal pytest node supporting markers and report sections."""

    __slots__ = ("_marker", "nodeid", "sections", "stash")

    def __init__(
        self, marker: StubMarker | None = None, nodeid: str = "test::stub"
    ) -> None:
        """Create a node with an optional ``cmd_mox`` marker and node identifier."""
        self._marker = marker
        self.sections: list[tuple[str, str, str]] = []
        self.stash = pytest.Stash()
        self.nodeid = nodeid

    def get_closest_marker(self, name: str) -> StubMarker | None:
        """Return the ``cmd_mox`` marker for that name, or ``None`` for others."""
        return self._marker if name == "cmd_mox" else None

    def add_report_section(self, when: str, key: str, content: str) -> None:
        """Record a report section for the given phase, key, and content."""
        self.sections.append((when, key, content))


class RequestKwargs(typ.TypedDict, total=False):
    """Keyword arguments forwarded into ``StubRequest``."""

    node: StubNode
    param: dict[str, bool]


class FixtureWithWrapped(typ.Protocol):
    """Describe pytest's runtime wrapper around a fixture function."""

    __wrapped__: CmdMoxFixtureGenerator


class StubRequest:
    """Minimal fixture request exposing ``config``/``node``/``param``."""

    __slots__ = ("config", "node", "param")

    def __init__(
        self,
        *,
        config: StubConfig,
        node: StubNode | None = None,
        param: object | None = None,
    ) -> None:
        """Store the config, node, and optional fixture parameter."""
        self.config = config
        self.node = node or StubNode()
        if param is not None:
            self.param = param


@dc.dataclass(slots=True)
class StubMoxBehaviorConfig:
    """Configuration for StubMox behavioral flags."""

    raise_on_exit: bool = False
    raise_on_verify: bool = False
    verify_on_exit: bool = False


@dc.dataclass(slots=True, kw_only=True)
class StubMox:
    """Minimal CmdMox stand-in for exercising manager behaviour."""

    phase: Phase = Phase.REPLAY
    behavior: StubMoxBehaviorConfig | None = None
    environment: EnvironmentManager | None = None
    lifecycle_hint: str | None = None
    fail_fast: bool = False
    raise_on_exit: bool = dc.field(init=False, default=False)
    raise_on_verify: bool = dc.field(init=False, default=False)
    verify_on_exit: bool = dc.field(init=False, default=False)
    enter_calls: int = dc.field(init=False, default=0)
    replay_calls: int = dc.field(init=False, default=0)
    verify_calls: int = dc.field(init=False, default=0)
    exit_calls: list[tuple[object | None, object | None, object | None]] = dc.field(
        init=False, default_factory=list
    )
    _lifecycle_hint: str | None = dc.field(init=False, default=None)

    def __post_init__(self) -> None:
        """Derive the behaviour flags and lifecycle hint from the configuration."""
        config = self.behavior or StubMoxBehaviorConfig()
        self.raise_on_exit = config.raise_on_exit
        self.raise_on_verify = config.raise_on_verify
        self.verify_on_exit = config.verify_on_exit
        self._lifecycle_hint = self.lifecycle_hint

    def __enter__(self) -> StubMox:
        """Count the context entry and return the stub itself."""
        self.enter_calls += 1
        return self

    def replay(self) -> None:
        """Count the replay call and move the stub into the replay phase."""
        self.replay_calls += 1
        self.phase = Phase.REPLAY

    def verify(self) -> None:
        """Count the verify call, raising if configured, then enter verify phase."""
        self.verify_calls += 1
        if self.raise_on_verify:
            raise RuntimeError(VERIFY_ERROR_MESSAGE)
        self.phase = Phase.VERIFY

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        """Record the exit arguments, raise if configured, then enter verify phase."""
        self.exit_calls.append((exc_type, exc, tb))
        if self.raise_on_exit:
            raise OSError(EXIT_ERROR_MESSAGE)
        self.phase = Phase.VERIFY


class ExitSpyCmdMoxManager(_CmdMoxManager):
    """Record the fixture's body-failure decision before teardown."""

    last_instance: ExitSpyCmdMoxManager | None = None

    @typ.override
    def __init__(self, request: pytest.FixtureRequest) -> None:
        """Initialize the manager spy for one fixture instance."""
        super().__init__(request)
        self.body_failed_values: list[bool] = []
        type(self).last_instance = self

    @typ.override
    def exit(self, *, body_failed: bool) -> None:
        """Record the fixture decision before delegating to manager teardown."""
        self.body_failed_values.append(body_failed)
        super().exit(body_failed=body_failed)


def assert_teardown_sections(node: StubNode, *expected: tuple[str, str, str]) -> None:
    """Assert the node recorded exactly the expected teardown report sections."""
    assert node.sections == list(expected), "Assertion failed"


def assert_lifecycle_calls(stub: StubMox, *, enter: int, replay: int) -> None:
    """Assert the stub recorded the expected enter and replay call counts."""
    assert stub.enter_calls == enter, "Assertion failed"
    assert stub.replay_calls == replay, "Assertion failed"
