"""Unit tests for environment variable subset filtering."""

from __future__ import annotations

import pytest

from cmd_mox.record.env_filter import filter_env_subset


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
        env = {
            "MY_SECRET_KEY": "supersecret",
            "PATH": "/usr/bin",
            "NORMAL": "val",
        }
        result = filter_env_subset(
            env,
            allowlist=["MY_SECRET_KEY", "PATH"],
        )

        assert result["MY_SECRET_KEY"] == "supersecret"  # noqa: S105
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
        env = {
            "HOME": "/home/user",
            "SECRET_TOKEN": "tok",
            "NORMAL": "val",
        }
        result = filter_env_subset(
            env,
            explicit_keys=["HOME", "SECRET_TOKEN"],
        )

        assert result["HOME"] == "/home/user"
        assert result["SECRET_TOKEN"] == "tok"  # noqa: S105

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
