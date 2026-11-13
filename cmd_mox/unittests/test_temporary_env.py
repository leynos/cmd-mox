"""Unit tests for :func:`cmd_mox.environment.temporary_env`."""

from __future__ import annotations

import os

import pytest

from cmd_mox.environment import temporary_env


def test_temporary_env_restores_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """temporary_env restores touched keys and preserves unrelated additions."""
    monkeypatch.delenv("TMP", raising=False)
    with temporary_env({"TMP": "1"}):
        monkeypatch.setenv("EXTRA", "foo")
        assert os.environ["TMP"] == "1"
        assert os.environ["EXTRA"] == "foo"
    assert "TMP" not in os.environ
    assert os.environ["EXTRA"] == "foo"


def test_temporary_env_restores_deleted_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Touched keys are restored while unrelated deletions survive."""
    monkeypatch.setenv("KEEP", "val")
    monkeypatch.setenv("UNRELATED", "kept")
    with temporary_env({"KEEP": "temporary"}):
        del os.environ["KEEP"]
        del os.environ["UNRELATED"]
    assert os.environ["KEEP"] == "val"
    assert "UNRELATED" not in os.environ


def test_temporary_env_restores_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """temporary_env restores touched keys even when the body raises."""
    monkeypatch.delenv("ERR", raising=False)

    def trigger() -> None:
        """Mutate the environment and raise inside ``temporary_env``."""
        with temporary_env({"ERR": "1"}):
            monkeypatch.setenv("EXTRA", "bar")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        trigger()
    assert "ERR" not in os.environ
    assert os.environ["EXTRA"] == "bar"


def test_nested_temporary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested temporary_env contexts restore only their own keys."""
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    with temporary_env({"A": "1"}):
        with temporary_env({"B": "2"}):
            monkeypatch.setenv("C", "3")
        assert "B" not in os.environ
        assert os.environ["A"] == "1"
        assert os.environ["C"] == "3"
    assert "A" not in os.environ
    assert os.environ["C"] == "3"


def test_temporary_env_interleaved_exits_do_not_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disjoint contexts that exit out of order leave no keys behind."""
    monkeypatch.delenv("A_LEAK", raising=False)
    monkeypatch.delenv("B_LEAK", raising=False)
    first = temporary_env({"A_LEAK": "1"})
    second = temporary_env({"B_LEAK": "2"})
    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    second.__exit__(None, None, None)
    assert "A_LEAK" not in os.environ
    assert "B_LEAK" not in os.environ


@pytest.mark.parametrize("initial", [None, "original"])
def test_temporary_env_same_key_lifo(
    monkeypatch: pytest.MonkeyPatch, initial: str | None
) -> None:
    """Same-key nesting restores the entry state from before the outer context."""
    if initial is None:
        monkeypatch.delenv("SAME", raising=False)
    else:
        monkeypatch.setenv("SAME", initial)
    with temporary_env({"SAME": "outer"}):
        assert os.environ["SAME"] == "outer"
        with temporary_env({"SAME": "inner"}):
            assert os.environ["SAME"] == "inner"
        assert os.environ["SAME"] == "outer"
    if initial is None:
        assert "SAME" not in os.environ
    else:
        assert os.environ["SAME"] == initial


def test_temporary_env_restores_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty string is restored as a value, not treated as an absent key."""
    monkeypatch.setenv("EMPTY", "")
    with temporary_env({"EMPTY": "temporary"}):
        assert os.environ["EMPTY"] == "temporary"
    assert os.environ["EMPTY"] == ""


def test_temporary_env_rolls_back_partial_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid entry applied before an invalid one is rolled back."""
    monkeypatch.delenv("VALID", raising=False)
    with (
        pytest.raises(ValueError, match="illegal environment variable name"),
        temporary_env({"VALID": "1", "A=B": "bad"}),
    ):
        pass
    assert "VALID" not in os.environ
