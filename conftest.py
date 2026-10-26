"""Global test configuration and shared fixtures."""

from __future__ import annotations

import socket
import sys
import tempfile
import typing as typ
from pathlib import Path

import pytest

import cmd_mox.environment

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from _pytest.pytester import RunResult

_UNIX_SOCKETS_SUPPORTED: bool | None = None


def _can_bind_unix_socket() -> bool:
    """Return whether the platform allows binding Unix domain sockets.

    Returns
    -------
    bool
        ``True`` when a Unix-domain socket can be bound, otherwise ``False``.
    """
    if not hasattr(socket, "AF_UNIX"):
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            sock_path = Path(tmp_dir) / "probe.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind(str(sock_path))
            except PermissionError:
                return False
            finally:
                sock.close()
                if sock_path.exists():
                    sock_path.unlink()
    except OSError:
        return False
    return True


def _unix_sockets_supported() -> bool:
    global _UNIX_SOCKETS_SUPPORTED
    if _UNIX_SOCKETS_SUPPORTED is None:
        _UNIX_SOCKETS_SUPPORTED = _can_bind_unix_socket()
    return _UNIX_SOCKETS_SUPPORTED


def _anchor_mutmut_source_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make cwd-relative mutmut paths absolute for the current scope."""
    mutmut_configuration = sys.modules.get("mutmut.configuration")
    if mutmut_configuration is None:
        return

    config_factory = typ.cast("typ.Any", getattr(mutmut_configuration, "Config", None))
    if config_factory is None:
        return

    mutmut_config = config_factory.get()
    package_root = Path(__file__).resolve().parent
    monkeypatch.setattr(
        mutmut_config,
        "source_paths",
        [
            path if path.is_absolute() else package_root / path
            for path in mutmut_config.source_paths
        ],
    )


@pytest.fixture(autouse=True)
def anchor_mutmut_source_paths_for_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep mutmut paths valid while tests can change cwd.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Restore the relative source paths after this test finishes.
    """
    _anchor_mutmut_source_paths(monkeypatch)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers and cache platform capability checks."""
    config.addinivalue_line(
        "markers",
        "requires_unix_sockets: mark test as requiring Unix domain socket support",
    )
    _unix_sockets_supported()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip tests needing Unix sockets when the platform disallows them."""
    if _unix_sockets_supported():
        return
    skip = pytest.mark.skip(
        reason="Unix domain sockets are not permitted in this environment"
    )
    for item in items:
        if "requires_unix_sockets" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def run_nested_pytester(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> cabc.Callable[..., RunResult]:
    """Create a nested pytest runner anchored to the outer invocation.

    Pytester changes the process cwd to its temporary project. Mutmut and
    other cwd-relative tools then resolve package paths from that directory,
    so the nested run restores the outer invocation cwd and pins its own
    configuration to the pytester project.

    Parameters
    ----------
    pytester : pytest.Pytester
        Create the temporary project and its inner pytest configuration.
    monkeypatch : pytest.MonkeyPatch
        Restore the pytester cwd after the nested in-process run.
    request : pytest.FixtureRequest
        Provide the outer invocation directory used by the nested run.

    Returns
    -------
    cabc.Callable[..., RunResult]
        A runner accepting extra pytest arguments, an optional generated test
        path, and ``plugins`` for ``runpytest_inprocess``.
    """
    inner_ini = pytester.path / "tox.ini"
    if not inner_ini.is_file():
        inner_ini = pytester.makeini("[pytest]\n")

    def run_nested(
        *args: str,
        test_path: Path | None = None,
        plugins: cabc.Sequence[object] = (),
    ) -> RunResult:
        generated_path = pytester.path if test_path is None else test_path
        with monkeypatch.context() as scoped_monkeypatch:
            scoped_monkeypatch.chdir(request.config.invocation_params.dir)
            _anchor_mutmut_source_paths(scoped_monkeypatch)
            return pytester.runpytest_inprocess(
                *args,
                "-c",
                str(inner_ini),
                "--rootdir",
                str(pytester.path),
                "--confcutdir",
                str(pytester.path),
                str(generated_path.resolve(strict=True)),
                plugins=plugins,
            )

    return run_nested


@pytest.fixture(autouse=True)
def reset_environment_manager_state() -> cabc.Generator[None, None, None]:
    """Ensure clean state for ``EnvironmentManager`` between tests."""
    cmd_mox.environment.EnvironmentManager.reset_active_manager()
    yield
    cmd_mox.environment.EnvironmentManager.reset_active_manager()


PARALLEL_ARTIFACT_ENV = "CMD_MOX_PARALLEL_ARTIFACT_DIR"


@pytest.fixture
def parallel_artifact_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a temporary directory for recording parallel test metadata.

    Returns
    -------
    pathlib.Path
        The path configured in ``CMD_MOX_PARALLEL_ARTIFACT_DIR``.
    """
    artifact_dir = tmp_path / "parallel-artifacts"
    monkeypatch.setenv(PARALLEL_ARTIFACT_ENV, str(artifact_dir))
    return artifact_dir
