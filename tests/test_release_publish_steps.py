"""Tests for version checking, attestation collection and PyPI publication.

Run with ``make test`` or
``python -m pytest -q tests/test_release_publish_steps.py``. The tests execute
the checked-in scripts and workflow shell steps with temporary fake tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    install_fake_tools,
    load_workflow_step,
    read_tool_calls,
    run_bash_step,
)

from scripts import release_assets, release_version

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
pytestmark = [
    pytest.mark.skipif(
        not (RELEASE_WORKFLOW_PATH.is_file() and _RELEASE_ASSETS_CLI.is_file()),
        reason="release workflow or release-assets CLI is absent",
    ),
    pytest.mark.skipif(
        os.name == "nt" or shutil.which("bash") is None,
        reason="release shell-step tests require Bash on a Unix-like system",
    ),
]


@pytest.mark.parametrize(
    ("version", "is_prerelease"),
    [
        ("0.2.0", "false"),
        ("0.2.0-beta1", "true"),
        ("0.2.0.dev1", "true"),
        ("0.2.0+local", "false"),
        ("0.2.0+beta1", "false"),
        ("0.2.0rc2+local", "true"),
    ],
)
def test_version_labels_classify_only_public_prerelease_markers(
    version: str, is_prerelease: str
) -> None:
    """Pre and dev labels count only before the local-version separator."""
    assert release_version.is_prerelease(version) is (is_prerelease == "true"), (
        f"unexpected pre-release classification for {version}"
    )


def test_version_check_accepts_the_exact_v_prefixed_beta_tag(tmp_path: Path) -> None:
    """A leading tag ``v`` is removed before exact project-version comparison."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "cmd-mox"\nversion = "0.2.0-beta1"\n',
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = release_version.main(
        {"TAG_VERSION": "v0.2.0-beta1", "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert result == 0, "the matching beta tag must pass"
    assert output.read_text(encoding="utf-8") == "prerelease=true\n", (
        "the version check must export its pre-release result"
    )


def test_version_check_annotates_an_exact_spelling_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Different version spellings fail instead of being normalised."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "cmd-mox"\nversion = "0.2.0-beta.1"\n',
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = release_version.main(
        {"TAG_VERSION": "v0.2.0-beta1", "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert result == 1, "a different version spelling must fail"
    assert "::error" in capsys.readouterr().err, (
        "a tag mismatch must emit a workflow error annotation"
    )
    assert not output.exists(), "a failed check must not set the prerelease output"


def test_pep691_index_resolves_relative_urls_and_missing_files_are_empty(
    tmp_path: Path,
) -> None:
    """Index parsing keeps SHA-256 and resolves links against cmd-mox's index."""
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({
            "files": [
                {
                    "filename": "cmd_mox-0.3.0.tar.gz",
                    "hashes": {"sha256": "ABCD"},
                    "url": "files/cmd_mox-0.3.0.tar.gz",
                }
            ]
        }),
        encoding="utf-8",
    )

    files = release_assets.read_pypi_index(index)
    assert files["cmd_mox-0.3.0.tar.gz"].sha256 == "abcd", (
        "PyPI hashes must be normalised to lower-case hex"
    )
    assert files["cmd_mox-0.3.0.tar.gz"].url == (
        "https://pypi.org/simple/cmd-mox/files/cmd_mox-0.3.0.tar.gz"
    ), "relative PEP 691 URLs must resolve from the project index"
    index.write_text("{}", encoding="utf-8")
    assert release_assets.read_pypi_index(index) == {}, (
        "an index without files must be treated as an empty inventory"
    )


def _distribution_tree(root: Path) -> tuple[Path, Path, Path]:
    """Create the input distribution tree used by the workflow shell tests.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path, pathlib.Path]
        The ``dist`` directory, the downloaded artifact directory, and the
        publication directory.
    """
    dist = root / "dist"
    source = dist / "wheels-pure"
    publish = dist / "publish"
    source.mkdir(parents=True)
    publish.mkdir()
    return dist, source, publish


def test_attestation_collection_copies_all_wheels_and_one_sdist(tmp_path: Path) -> None:
    """The collection step copies all input wheels and exactly one sdist."""
    _, source, publish = _distribution_tree(tmp_path)
    (source / "cmd_mox-0.3.0.tar.gz").write_bytes(b"sdist")
    (source / "cmd_mox-0.3.0-py3-none-any.whl").write_bytes(b"wheel one")
    (source / "cmd_mox-0.3.0-py3-none-any.whl.metadata").write_bytes(b"metadata")
    (source / "cmd_mox-0.3.0-py2.py3-none-any.whl").write_bytes(b"wheel two")
    (publish / "stale.whl").write_bytes(b"must not be copied")
    script, _ = load_workflow_step("attest", "collect")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in publish.iterdir()} == {
        "cmd_mox-0.3.0.tar.gz",
        "cmd_mox-0.3.0-py3-none-any.whl",
        "cmd_mox-0.3.0-py2.py3-none-any.whl",
    }, "collection must include every wheel but not scan its output directory"


@pytest.mark.parametrize(
    "sdist_names",
    [[], ["one.tar.gz", "two.tar.gz"]],
    ids=["missing-sdist", "two-sdists"],
)
def test_attestation_collection_fails_without_exactly_one_sdist(
    tmp_path: Path, sdist_names: list[str]
) -> None:
    """Collection fails before attestation for zero or multiple sdists."""
    _, source, _ = _distribution_tree(tmp_path)
    for name in sdist_names:
        (source / name).write_bytes(b"sdist")
    (source / "cmd_mox-0.3.0-py3-none-any.whl").write_bytes(b"wheel")
    script, _ = load_workflow_step("attest", "collect")

    result = run_bash_step(script, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})

    assert result.returncode == 1, "invalid source-distribution counts must fail"
    assert "Expected exactly one sdist" in result.stderr, (
        "collection failure must identify the sdist inventory"
    )


@pytest.mark.parametrize(
    ("statuses", "expected_status", "expected_calls", "expected_sleeps"),
    [
        ("200", 0, 1, 0),
        ("404", 0, 1, 0),
        ("503,200", 0, 2, 1),
        ("429,200", 0, 2, 1),
        ("000,200", 0, 2, 1),
        ("503,503,503,503,503,503", 1, 6, 5),
        ("403,200", 1, 1, 0),
    ],
)
def test_pypi_index_retries_only_network_rate_and_server_errors(
    tmp_path: Path,
    statuses: str,
    expected_status: int,
    expected_calls: int,
    expected_sleeps: int,
) -> None:
    """Index fetch accepts 200/404 and retries only 000, 429 and 5xx."""
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    script, _ = load_workflow_step("publish-pypi", "index")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "CURL_BODY": '{"files": []}',
            "CURL_STATUSES": statuses,
            "FAKE_TOOL_LOG": str(log),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    calls = read_tool_calls(log)
    assert result.returncode == expected_status, (
        f"unexpected index-fetch status {result.returncode}: {result.stderr}"
    )
    assert len(read_tool_calls(log, tool="curl")) == expected_calls, (
        "the index fetch used an unexpected number of curl attempts"
    )
    assert len(read_tool_calls(log, tool="sleep")) == expected_sleeps, (
        "only retryable statuses should cause a two-second delay"
    )
    if statuses == "404":
        assert (tmp_path / "index.json").read_text(encoding="utf-8") == (
            '{"files": []}\n'
        ), "an unpublished project must be represented as an empty index"
    if statuses == "403,200":
        assert any("HTTP 403" in line for line in result.stderr.splitlines()), (
            "a non-retryable HTTP error must fail immediately"
        )
    assert calls, "the fake tool log must contain each recorded invocation"


def _write_index(path: Path, filenames: list[str]) -> None:
    """Write a minimal PEP 691 index containing the supplied filenames."""
    path.write_text(
        json.dumps({
            "files": [
                {
                    "filename": name,
                    "hashes": {"sha256": hashlib.sha256(name.encode()).hexdigest()},
                    "url": f"https://files.example.invalid/{name}",
                }
                for name in filenames
            ]
        }),
        encoding="utf-8",
    )


def _write_github_assets(path: Path, digests: dict[str, str]) -> None:
    """Write a GitHub asset listing with the provided SHA-256 values."""
    path.write_text(
        json.dumps({
            "assets": [
                {"name": name, "digest": f"sha256:{digest}"}
                for name, digest in digests.items()
            ]
        }),
        encoding="utf-8",
    )


def test_stage_pypi_skips_published_names_and_uses_github_bytes(tmp_path: Path) -> None:
    """Only names PyPI lacks remain, using existing GitHub bytes when needed."""
    publish = tmp_path / "dist" / "publish"
    github_files = tmp_path / "github-state" / "files"
    publish.mkdir(parents=True)
    github_files.mkdir(parents=True)
    already_published = "cmd_mox-0.3.0.tar.gz"
    carried = "cmd_mox-0.3.0-py3-none-any.whl"
    local = "cmd_mox-0.3.0-extra.whl"
    (publish / already_published).write_bytes(b"rebuilt but not canonical")
    (publish / carried).write_bytes(b"new wheel bytes")
    (publish / local).write_bytes(b"new extra wheel")
    (github_files / carried).write_bytes(b"canonical GitHub bytes")
    index = tmp_path / "pypi-index.json"
    _write_index(index, [already_published])
    assets = tmp_path / "github-state" / "assets.json"
    assets.parent.mkdir(parents=True, exist_ok=True)
    _write_github_assets(
        assets,
        {carried: hashlib.sha256(b"canonical GitHub bytes").hexdigest()},
    )
    (tmp_path / "github-output").touch()
    script, _ = load_workflow_step("publish-pypi", "pending")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "PYPI_INDEX_PATH": str(index),
        },
    )

    assert result.returncode == 0, result.stderr
    assert not (publish / already_published).exists(), (
        "a filename already on PyPI must be removed from the upload directory"
    )
    assert (publish / carried).read_bytes() == b"canonical GitHub bytes", (
        "PyPI must receive GitHub's canonical bytes for an existing asset"
    )
    assert (publish / local).read_bytes() == b"new extra wheel", (
        "an unpublished local distribution must remain staged"
    )
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "remaining=true\n"
    ), "the publish action must run when files remain"


def test_stage_pypi_reports_empty_upload_and_condition_skips_pypi_action(
    tmp_path: Path,
) -> None:
    """An all-published inventory yields remaining=false and a false action gate."""
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    (tmp_path / "github-state" / "files").mkdir(parents=True)
    names = ["cmd_mox-0.3.0.tar.gz", "cmd_mox-0.3.0-py3-none-any.whl"]
    for name in names:
        (publish / name).write_bytes(name.encode())
    index = tmp_path / "pypi-index.json"
    _write_index(index, names)
    assets = tmp_path / "github-state" / "assets.json"
    assets.parent.mkdir(parents=True, exist_ok=True)
    _write_github_assets(assets, {})
    output = tmp_path / "github-output"
    output.touch()
    script, _ = load_workflow_step("publish-pypi", "pending")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYPI_INDEX_PATH": str(index)},
        output_path=output,
    )
    publish_job = yaml.safe_load(RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8"))[
        "jobs"
    ]["publish-pypi"]
    publish_step = next(
        step
        for step in publish_job["steps"]
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == "remaining=false\n", (
        "the CLI must report that no PyPI upload remains"
    )
    assert publish_step["if"] == "steps.pending.outputs.remaining == 'true'", (
        "the PyPI action condition must skip an empty upload"
    )


def test_stage_pypi_requires_the_simple_index(tmp_path: Path) -> None:
    """A command that reconciles PyPI names cannot run without its index."""
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    assets = tmp_path / "assets.json"
    _write_github_assets(assets, {})
    command = shlex.join([
        sys.executable,
        "-m",
        "scripts.release_assets_cli",
        "stage-pypi",
        "--publish",
        str(publish),
        "--assets",
        str(assets),
        "--github-files",
        str(tmp_path),
    ])
    result = run_bash_step(
        f"set -euo pipefail\n{command}",
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1, "stage-pypi must require --index"
    assert "::error title=release-assets::--index is required" in result.stderr, (
        "missing required reconciliation inputs must be annotated"
    )
