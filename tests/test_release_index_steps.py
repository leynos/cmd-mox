"""Tests for PyPI index retries and release propagation checks."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from helpers.release_publish import _IndexFetchCase, _tool_arguments, _write_index
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    install_fake_tools,
    load_workflow_step,
    read_tool_calls,
    run_bash_step,
    should_skip_release_tests_for_mutation_sandbox,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
_FETCH_INDEX_SCRIPT = _REPOSITORY_ROOT / "scripts" / "fetch_pypi_index.sh"
pytestmark = [
    pytest.mark.skipif(
        should_skip_release_tests_for_mutation_sandbox(
            Path.cwd(), RELEASE_WORKFLOW_PATH, _RELEASE_ASSETS_CLI
        ),
        reason="mutmut sandbox omits the release workflow and CLI",
    ),
    pytest.mark.skipif(
        os.name == "nt" or shutil.which("bash") is None,
        reason="release shell-step tests require Bash on a Unix-like system",
    ),
]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(_IndexFetchCase("200", 0, 1, 0), id="success"),
        pytest.param(_IndexFetchCase("404", 0, 1, 0), id="missing-project"),
        pytest.param(_IndexFetchCase("503,200", 0, 2, 1), id="server-recovery"),
        pytest.param(_IndexFetchCase("429,200", 0, 2, 1), id="rate-limit-recovery"),
        pytest.param(_IndexFetchCase("000,200", 0, 2, 1), id="network-recovery"),
        pytest.param(
            _IndexFetchCase("503,503,503,503,503,503", 1, 6, 5),
            id="persistent-server-error",
        ),
        pytest.param(_IndexFetchCase("403,200", 1, 1, 0), id="forbidden"),
    ],
)
def test_pypi_index_retries_only_network_rate_and_server_errors(
    tmp_path: Path,
    case: _IndexFetchCase,
) -> None:
    """Index fetch accepts 200/404 and retries only 000, 429 and 5xx."""
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    script, _ = load_workflow_step("publish-pypi", "index")
    release_script, _ = load_workflow_step("publish-release", "index")
    assert script == release_script, "both release jobs must call the shared fetch step"
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(_FETCH_INDEX_SCRIPT, tmp_path / "scripts" / "fetch_pypi_index.sh")
    assert _FETCH_INDEX_SCRIPT.read_text(encoding="utf-8").splitlines()[0] == (
        "set -euo pipefail"
    ), "the checked-in shared fetch script must enable strict Bash"

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "CURL_BODY": '{"files": []}',
            "CURL_STATUSES": case.statuses,
            "FAKE_TOOL_LOG": str(log),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    calls = read_tool_calls(log)
    assert result.returncode == case.expected_status, (
        f"unexpected index-fetch status {result.returncode}: {result.stderr}"
    )
    assert len(read_tool_calls(log, tool="curl")) == case.expected_calls, (
        "the index fetch used an unexpected number of curl attempts"
    )
    assert len(read_tool_calls(log, tool="sleep")) == case.expected_sleeps, (
        "only retryable statuses should cause a two-second delay"
    )
    if case.statuses == "404":
        assert (tmp_path / "index.json").read_text(encoding="utf-8") == (
            '{"files": []}\n'
        ), "an unpublished project must be represented as an empty index"
    if case.statuses == "403,200":
        assert any("HTTP 403" in line for line in result.stderr.splitlines()), (
            "a non-retryable HTTP error must fail immediately"
        )
    assert calls, "the fake tool log must contain each recorded invocation"


@pytest.mark.parametrize("first_status", ["200", "404"])
def test_publish_release_waits_for_every_distribution_to_reach_pypi(
    tmp_path: Path,
    first_status: str,
) -> None:
    """The propagation poll outlasts the initial retry window when needed."""
    names = ["cmd_mox-0.3.0.tar.gz", "cmd_mox-0.3.0-py3-none-any.whl"]
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    for name in names:
        (publish / name).write_bytes(name.encode())
    (tmp_path / "release-assets.json").write_text('{"assets": []}', encoding="utf-8")
    complete_index_path = tmp_path / "complete-index.json"
    _write_index(complete_index_path, names)

    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    script, declared_env = load_workflow_step("publish-release", "index")
    assert declared_env == {
        "EXPECTED_PUBLISH_DIR": "dist/publish",
        "RELEASE_ASSETS_PATH": "release-assets.json",
    }, "publish-release must poll for every file in its release-dist inventory"
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(_FETCH_INDEX_SCRIPT, tmp_path / "scripts" / "fetch_pypi_index.sh")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            **declared_env,
            "CURL_BODIES": json.dumps([
                *(['{"files": []}'] * 6),
                complete_index_path.read_text(encoding="utf-8"),
            ]),
            "CURL_STATUSES": ",".join([first_status, *(["200"] * 6)]),
            "FAKE_TOOL_LOG": str(log),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    assert result.returncode == 0, result.stderr
    assert len(read_tool_calls(log, tool="curl")) == 7, (
        "the propagation poll must outlast the six-attempt initial fetch"
    )
    assert all(
        arguments[arguments.index("--max-time") + 1] == "20"
        for arguments in map(_tool_arguments, read_tool_calls(log, tool="curl"))
    ), "every propagation request must have a bounded twenty-second timeout"
    sleep_arguments = list(map(_tool_arguments, read_tool_calls(log, tool="sleep")))
    assert [arguments[0] for arguments in sleep_arguments] == [
        "2",
        "4",
        "8",
        "16",
        "30",
        "30",
    ], "the propagation poll must use bounded exponential backoff"
    assert len(sleep_arguments) == 6, (
        "the stale index responses must delay between polling attempts"
    )
    assert (tmp_path / "index.json").read_text(encoding="utf-8") == (
        complete_index_path.read_text(encoding="utf-8")
    ), "the final index must contain every published distribution"


def test_publish_release_fails_after_ten_indexes_omit_a_distribution(
    tmp_path: Path,
) -> None:
    """A persistently stale PyPI index fails after the bounded retry window."""
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    expected_name = "cmd_mox-0.3.0.tar.gz"
    (publish / expected_name).write_bytes(b"sdist")
    (tmp_path / "release-assets.json").write_text('{"assets": []}', encoding="utf-8")

    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    script, declared_env = load_workflow_step("publish-release", "index")
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(_FETCH_INDEX_SCRIPT, tmp_path / "scripts" / "fetch_pypi_index.sh")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            **declared_env,
            "CURL_BODIES": json.dumps(['{"files": []}']),
            "FAKE_TOOL_LOG": str(log),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    assert result.returncode == 1, "an index that never lists the upload must fail"
    assert "PyPI index did not list release files after 10 attempts" in result.stderr, (
        "the failure must identify the expired index wait"
    )
    assert expected_name in result.stderr, "the failure must name the missing sdist"
    assert len(read_tool_calls(log, tool="curl")) == 10, (
        "the propagation wait must stop after its ten bounded attempts"
    )
    assert len(read_tool_calls(log, tool="sleep")) == 9, (
        "the propagation wait must delay only between attempts"
    )
