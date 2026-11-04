"""Unit tests for the shared diagnostic environment redaction helpers.

The helpers exist so that every diagnostic that renders environment values
uses the same sensitive-key vocabulary as recording redaction. These tests pin
that vocabulary at the helper boundary.
"""

from __future__ import annotations

import pytest

from cmd_mox.domain import (
    REDACTED_VALUE,
    mask_env_mapping,
    mask_env_value,
)


@pytest.mark.parametrize(
    "key",
    [
        "DB_PWD",
        "GITHUB_KEY",
        "API_TOKEN",
        "AWS_CREDENTIALS",
        "SSH_PASSPHRASE",
        "db_pwd",
        "github_key",
        "MyPassword",
        # A false positive only hides one value from a diagnostic, so the
        # classifier favours readability of the key over precision here.
        "TOKENIZER",
    ],
)
def test_mask_env_value_redacts_sensitive_keys(key: str) -> None:
    """Sensitive keys are masked, whatever their casing."""
    assert mask_env_value(key, "leaked-secret") == REDACTED_VALUE


@pytest.mark.parametrize("key", ["MONKEY", "PATH", "HOME", "MONITOR"])
def test_mask_env_value_keeps_benign_keys(key: str) -> None:
    """Benign keys keep their value, including near-misses such as MONKEY."""
    assert mask_env_value(key, "visible") == "visible"


def test_mask_env_value_keeps_missing_values() -> None:
    """A missing value stays ``None`` even when the key looks sensitive."""
    assert mask_env_value("DB_PWD", None) is None


def test_mask_env_mapping_returns_a_new_masked_copy() -> None:
    """The mapping helper masks a copy, preserving order and ``None``."""
    source = {"DB_PWD": "leaked-secret", "PATH": "/bin", "API_TOKEN": None}

    masked = mask_env_mapping(source)

    assert masked == {
        "DB_PWD": REDACTED_VALUE,
        "PATH": "/bin",
        "API_TOKEN": None,
    }
    assert list(masked) == list(source), "Insertion order changed"
    assert masked is not source, "Helper returned the input mapping"
    original_value = source["DB_PWD"]
    assert original_value == "leaked-secret", "Helper mutated its input"


def test_mask_env_mapping_handles_an_empty_mapping() -> None:
    """An empty mapping produces an empty copy, not the input object."""
    source: dict[str, str | None] = {}

    masked = mask_env_mapping(source)

    assert masked == {}
    assert masked is not source
