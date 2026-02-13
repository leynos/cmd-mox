"""Behavioural tests for CmdMox controller using pytest-bdd."""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest
from pytest_bdd import scenario

from cmd_mox.errors import (
    UnexpectedCommandError,
    UnfulfilledExpectationError,
    VerificationError,
)

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.environment import EnvironmentManager


FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"

_ERROR_TYPES: dict[str, type[VerificationError]] = {
    "UnexpectedCommandError": UnexpectedCommandError,
    "UnfulfilledExpectationError": UnfulfilledExpectationError,
    "VerificationError": VerificationError,
}


class ReplayInterruptionState(t.TypedDict):
    """Capture cleanup details after replay fails to start."""

    shim_dir: Path
    socket_path: Path
    manager_active: EnvironmentManager | None


from tests.steps import *  # noqa: F403,E402 - re-export pytest-bdd steps


@scenario(str(FEATURES_DIR / "controller.feature"), "stubbed command execution")
def test_stubbed_command_execution() -> None:
    """Stubbed command returns expected output."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "shim forwards stdout stderr and exit code",
)
def test_shim_forwards_streams() -> None:
    """Shim applies server provided stdout, stderr, and exit code."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "shim merges environment overrides across invocations",
)
def test_shim_merges_env_overrides() -> None:
    """Shim persists environment overrides between invocations."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "register command repairs broken shims during replay",
)
def test_register_command_repairs_broken_shims() -> None:
    """register_command recreates broken symlinks while replaying."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "mocked command execution")
def test_mocked_command_execution() -> None:
    """Mocked command returns expected output."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "spy records invocation")
def test_spy_records_invocation() -> None:
    """Spy records command invocation."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "spy assertion helpers")
def test_spy_assertion_helpers() -> None:
    """Spy exposes assert_called helpers."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "journal preserves invocation order"
)
def test_journal_preserves_order() -> None:
    """Journal records commands in order."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "times alias maps to times_called")
def test_times_alias_maps_to_times_called() -> None:
    """times() and times_called() behave identically in the DSL."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "context manager usage")
def test_context_manager_usage() -> None:
    """CmdMox works within a ``with`` block."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "replay cleanup handles interrupts")
def test_replay_cleanup_handles_interrupts() -> None:
    """Replay interruption should tear down the environment."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "replay is idempotent during replay phase",
)
def test_replay_is_idempotent_during_replay_phase() -> None:
    """Repeated replay() calls should be safe once replay is active."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "stub runs dynamic handler")
def test_stub_runs_dynamic_handler() -> None:
    """Stub executes a custom handler."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "ordered mocks match arguments")
def test_ordered_mocks_match_arguments() -> None:
    """Mocks enforce argument matching and ordering."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "environment variables can be injected"
)
def test_environment_injection() -> None:
    """Stub applies environment variables to the shim."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "passthrough spy merges expectation environment",
)
def test_passthrough_spy_merges_expectation_env() -> None:
    """Passthrough spies merge expectation and invocation environments."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "passthrough spy executes real command"
)
def test_passthrough_spy() -> None:
    """Spy runs the real command while recording."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "passthrough spy handles missing command"
)
def test_passthrough_spy_missing_command() -> None:
    """Spy reports an error when the real command is absent."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "passthrough spy handles permission error"
)
def test_passthrough_spy_permission_error() -> None:
    """Spy records permission errors from the real command."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "passthrough spy handles timeout")
def test_passthrough_spy_timeout() -> None:
    """Spy records timeouts from the real command."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "mock matches arguments with comparators",
)
def test_mock_matches_arguments_with_comparators() -> None:
    """Mocks can use comparator objects for flexible argument matching."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "replay fails when environment disappears during startup",
)
def test_replay_fails_when_environment_disappears() -> None:
    """Replay reports missing environment state when it vanishes mid-startup."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "comparator argument count mismatch is reported",
)
def test_comparator_argument_count_mismatch() -> None:
    """Mismatch counts are surfaced when comparator expectations differ."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "comparator matchers missing results in mismatch",
)
def test_comparator_matchers_missing_results_in_mismatch() -> None:
    """Missing matcher lists cause verification to fail gracefully."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "journal captures invocation details"
)
def test_journal_captures_invocation_details() -> None:
    """Journal records full invocation details."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "journal prunes excess entries")
def test_journal_prunes_excess_entries() -> None:
    """Journal drops older entries beyond configured size."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "invalid max journal size is rejected"
)
def test_invalid_max_journal_size_is_rejected() -> None:
    """Controller rejects non-positive journal size."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "verification reports unexpected invocation details",
)
def test_verification_reports_unexpected_invocation_details() -> None:
    """Verification errors include details for unexpected invocations."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "verification redacts sensitive environment values",
)
def test_verification_redacts_sensitive_environment_values() -> None:
    """Verification errors should redact sensitive environment variables."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "verification reports missing invocations",
)
def test_verification_reports_missing_invocations() -> None:
    """Verification errors highlight unfulfilled expectations."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "commands can be used in pipelines")
def test_commands_can_be_used_in_pipelines() -> None:
    """CmdMox supports piped shell commands by mocking each tool."""
    pass
