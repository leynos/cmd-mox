"""Tests for exact release version checks and package index parsing."""

from __future__ import annotations

import importlib
import json
import typing as typ
from pathlib import Path

import pytest
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    should_skip_release_tests_for_mutation_sandbox,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
pytestmark = pytest.mark.skipif(
    should_skip_release_tests_for_mutation_sandbox(
        Path.cwd(), RELEASE_WORKFLOW_PATH, _RELEASE_ASSETS_CLI
    ),
    reason="mutmut sandbox omits the release workflow and CLI",
)

release_assets = typ.cast("typ.Any", importlib.import_module("scripts.release_assets"))
release_asset_io = typ.cast(
    "typ.Any", importlib.import_module("scripts.release_assets_io")
)
release_version = typ.cast(
    "typ.Any", importlib.import_module("scripts.release_version")
)


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


def test_plan_uses_pypi_then_github_then_local_as_the_source_order() -> None:
    """Canonical source selection follows the release's destination priority."""
    decision = release_assets.plan(
        local={"pypi.whl", "github.whl", "local.whl"},
        on_pypi={"pypi.whl"},
        on_github={"pypi.whl", "github.whl"},
    )

    assert decision.canonical == {
        "pypi.whl": release_assets.Source.PYPI,
        "github.whl": release_assets.Source.GITHUB,
        "local.whl": release_assets.Source.LOCAL,
    }, "PyPI, then GitHub, then the local build must determine source priority"


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
    """Different version spellings fail instead of being normalized."""
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
                    "hashes": {"sha256": "A" * 64},
                    "url": "files/cmd_mox-0.3.0.tar.gz",
                }
            ]
        }),
        encoding="utf-8",
    )

    files = release_asset_io.read_pypi_index(index)
    assert files.digests["cmd_mox-0.3.0.tar.gz"] == "a" * 64, (
        "PyPI hashes must be normalized to lower-case hex"
    )
    assert files.urls["cmd_mox-0.3.0.tar.gz"] == (
        "https://pypi.org/simple/cmd-mox/files/cmd_mox-0.3.0.tar.gz"
    ), "relative PEP 691 URLs must resolve from the project index"
    index.write_text("{}", encoding="utf-8")
    assert release_asset_io.read_pypi_index(index).digests == {}, (
        "an index without files must be treated as an empty inventory"
    )
