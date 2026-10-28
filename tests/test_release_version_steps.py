"""Tests for exact release version checks and package index parsing."""

from __future__ import annotations

import importlib
import json
import typing as typ

import pytest
from helpers.release_workflow import (
    mutation_sandbox_skip as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    from pathlib import Path

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
        ("0.3.0-beta1", "true"),
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
        '[project]\nname = "cmd-mox"\nversion = "0.3.0-beta1"\n',
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = release_version.main(
        {"TAG_VERSION": "v0.3.0-beta1", "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert result == 0, "the matching beta tag must pass"
    assert output.read_text(encoding="utf-8") == "prerelease=true\n", (
        "the version check must export its pre-release result"
    )


def test_version_check_matches_but_rejects_the_historical_beta_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact historical beta spelling reaches the version-age guard."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "cmd-mox"\nversion = "0.2.0-beta1"\n',
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = release_version.main(
        {"TAG_VERSION": "v0.2.0-beta1", "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert result == 1, "the historical release segment must not be automated"
    error = capsys.readouterr().err
    assert "Unsupported initial release" in error, (
        "an exact historical beta match must reach the release-age guard"
    )
    assert "Tag/pyproject.toml mismatch" not in error, (
        "the exact v-prefixed beta spelling must match the project version"
    )
    assert not output.exists(), "a blocked release must not publish version output"


def test_version_check_rejects_the_historical_release_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hand-built 0.2.0 release cannot enter automated publication."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "cmd-mox"\nversion = "0.2.0"\n',
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = release_version.main(
        {"TAG_VERSION": "v0.2.0", "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert result == 1, "the historical release tag must not be automated"
    assert "Unsupported initial release" in capsys.readouterr().err, (
        "the guard must identify the blocked historical release"
    )
    assert not output.exists(), "a blocked release must not publish version output"


@pytest.mark.parametrize(
    "version",
    ["0.1.9", "0.2.0rc1", "0.2.0.post1", "0.2.0+local"],
)
def test_version_check_rejects_versions_not_after_the_historical_release(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    version: str,
) -> None:
    """Older and suffixed versions in the 0.2.0 release family are blocked."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "cmd-mox"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = release_version.main(
        {"TAG_VERSION": f"v{version}", "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert result == 1, f"release {version} must not reach publication"
    assert "Unsupported initial release" in capsys.readouterr().err, (
        f"release {version} must fail the release-age guard"
    )
    assert not output.exists(), "a blocked release must not publish version output"


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
