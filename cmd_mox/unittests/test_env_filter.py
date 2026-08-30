"""Unit tests for environment variable subset filtering."""

from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import given

from cmd_mox.record.env_filter import filter_env_subset

#: Keys drawn from a mix of arbitrary text and names that exercise each
#: filtering rule (system, sensitive, command-prefixed, CmdMox-internal).
_ENV_KEYS = st.one_of(
    st.text(max_size=8),
    st.sampled_from([
        "PATH",
        "HOME",
        "SAFE_VAR",
        "GIT_AUTHOR_NAME",
        "API_TOKEN",
        "CMOX_IPC_SOCKET",
        "CMD_MOX_DEBUG",
    ]),
)
_ENVS = st.dictionaries(_ENV_KEYS, st.text(max_size=8), max_size=8)
_KEY_LISTS = st.lists(_ENV_KEYS, max_size=4)
_COMMANDS = st.sampled_from(["", "git", "aws", "npm", "unknown-command"])


class TestFilterEnvSubset:
    """Tests for filter_env_subset()."""

    def test_excludes_sensitive_keys(self) -> None:
        """Keys matching sensitive patterns are excluded."""
        env = {
            "AWS_SECRET_ACCESS_KEY": "s3cr3t",
            "API_TOKEN": "tok123",
            "DB_PASSWORD": "hunter2",
            "GITHUB_KEY": "ghk",
            "SAFE_VAR": "keep",
        }
        result = filter_env_subset(env)

        assert "AWS_SECRET_ACCESS_KEY" not in result
        assert "API_TOKEN" not in result
        assert "DB_PASSWORD" not in result
        assert "GITHUB_KEY" not in result
        assert "SAFE_VAR" in result

    def test_excludes_system_keys(self) -> None:
        """System-specific keys are excluded."""
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/user",
            "USER": "testuser",
            "SHELL": "/bin/bash",
            "SSH_AUTH_SOCK": "/run/ssh-agent",
            "GPG_AGENT_INFO": "/run/gpg",
            "SAFE_VAR": "keep",
        }
        result = filter_env_subset(env)

        for key in ("PATH", "HOME", "USER", "SHELL", "SSH_AUTH_SOCK", "GPG_AGENT_INFO"):
            assert key not in result, f"{key} should be excluded"

        assert "SAFE_VAR" in result

    def test_includes_allowlisted_keys(self) -> None:
        """Allowlisted keys pass through even if they match exclusion patterns."""
        allowlisted_value = "supersecret"
        env = {
            "MY_SECRET_KEY": allowlisted_value,
            "PATH": "/usr/bin",
            "NORMAL": "val",
        }
        result = filter_env_subset(
            env,
            allowlist=["MY_SECRET_KEY", "PATH"],
        )

        assert result["MY_SECRET_KEY"] == allowlisted_value
        assert result["PATH"] == "/usr/bin"

    def test_includes_command_prefix_keys(self) -> None:
        """Command-specific prefix keys are included."""
        env = {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "UNRELATED": "val",
            "PATH": "/usr/bin",
        }
        result = filter_env_subset(env, command="git")

        assert "GIT_AUTHOR_NAME" in result
        assert "GIT_COMMITTER_EMAIL" in result
        assert "UNRELATED" not in result
        assert "PATH" not in result

    def test_includes_explicitly_requested_keys(self) -> None:
        """Keys passed as explicit_keys are always included."""
        requested_value = "tok"
        env = {
            "HOME": "/home/user",
            "SECRET_TOKEN": requested_value,
            "NORMAL": "val",
        }
        result = filter_env_subset(
            env,
            explicit_keys=["HOME", "SECRET_TOKEN"],
        )

        assert result["HOME"] == "/home/user"
        assert result["SECRET_TOKEN"] == requested_value

    def test_preserves_non_sensitive_keys(self) -> None:
        """Arbitrary non-sensitive, non-system keys pass through."""
        env = {
            "MY_SETTING": "enabled",
            "APP_MODE": "test",
            "DEBUG": "1",
        }
        result = filter_env_subset(env)

        assert result == env

    def test_empty_env(self) -> None:
        """An empty env produces an empty result."""
        assert filter_env_subset({}) == {}

    def test_excludes_sensitive_command_prefix_keys(self) -> None:
        """Sensitive keys with command-specific prefixes are still excluded."""
        env = {
            "GIT_SECRET_TOKEN": "s3cr3t",
            "GIT_AUTHOR_NAME": "User",
        }
        result = filter_env_subset(env, command="git")

        assert "GIT_AUTHOR_NAME" in result
        assert "GIT_SECRET_TOKEN" not in result

    def test_excludes_cmox_internal_keys(self) -> None:
        """CmdMox internal environment variables are excluded."""
        env = {
            "CMOX_IPC_SOCKET": "ipc-socket-path",
            "CMOX_IPC_TIMEOUT": "5.0",
            "CMOX_REAL_COMMAND_echo": "/usr/bin/echo",
            "SAFE_VAR": "keep",
        }
        result = filter_env_subset(env)

        assert "CMOX_IPC_SOCKET" not in result
        assert "CMOX_IPC_TIMEOUT" not in result
        assert "CMOX_REAL_COMMAND_echo" not in result
        assert "SAFE_VAR" in result

    @pytest.mark.parametrize(
        ("label", "allowlist", "explicit_keys", "cmox_keys"),
        [
            (
                "allowlist",
                ["CMOX_IPC_SOCKET", "CMD_MOX_DEBUG"],
                None,
                ["CMOX_IPC_SOCKET", "CMD_MOX_DEBUG"],
            ),
            (
                "explicit_keys",
                None,
                ["CMOX_IPC_SOCKET"],
                ["CMOX_IPC_SOCKET"],
            ),
        ],
        ids=["allowlist", "explicit_keys"],
    )
    def test_excludes_cmox_internal_keys_even_when_requested(
        self,
        label: str,
        allowlist: list[str] | None,
        explicit_keys: list[str] | None,
        cmox_keys: list[str],
    ) -> None:
        """CmdMox internal keys are excluded regardless of request method."""
        env = dict.fromkeys(cmox_keys, "value")
        env["SAFE_VAR"] = "keep"

        result = filter_env_subset(
            env,
            allowlist=allowlist,
            explicit_keys=explicit_keys,
        )

        for key in cmox_keys:
            assert key not in result, f"{key} should be excluded even when in {label}"
        assert "SAFE_VAR" in result


@given(env=_ENVS, command=_COMMANDS, allowlist=_KEY_LISTS, explicit_keys=_KEY_LISTS)
def test_filter_env_subset_returns_a_sub_mapping(
    env: dict[str, str],
    command: str,
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """Filtering only ever drops entries; it never adds keys or rewrites values.

    Invariant: ``filter_env_subset(env, ...).items() <= env.items()`` for every
    combination of *command*, *allowlist*, and *explicit_keys*.
    """
    result = filter_env_subset(
        env, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    assert result.items() <= env.items(), result


@given(env=_ENVS, command=_COMMANDS, allowlist=_KEY_LISTS, explicit_keys=_KEY_LISTS)
def test_filter_env_subset_never_leaks_cmdmox_internals(
    env: dict[str, str],
    command: str,
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """CmdMox-internal keys are dropped however they are requested.

    Invariant: no key of the result starts with ``CMOX_`` or ``CMD_MOX_``, even
    when that key is named in *allowlist* or *explicit_keys*.
    """
    result = filter_env_subset(
        env, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    leaked = [key for key in result if key.startswith(("CMOX_", "CMD_MOX_"))]
    assert not leaked, leaked


@given(env=_ENVS, command=_COMMANDS, allowlist=_KEY_LISTS, explicit_keys=_KEY_LISTS)
def test_filter_env_subset_is_idempotent(
    env: dict[str, str],
    command: str,
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """Re-filtering an already filtered subset changes nothing.

    Invariant: ``filter(filter(env, **kw), **kw) == filter(env, **kw)``, which
    follows from the decision being a per-key predicate.
    """
    once = filter_env_subset(
        env, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    twice = filter_env_subset(
        once, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    assert twice == once, twice
