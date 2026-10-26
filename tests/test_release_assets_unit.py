"""Direct tests for release reconciliation decisions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    should_skip_release_tests_for_mutation_sandbox,
)

release_assets = pytest.importorskip("scripts.release_assets")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"

pytestmark = [
    pytest.mark.skipif(
        should_skip_release_tests_for_mutation_sandbox(
            Path.cwd(), RELEASE_WORKFLOW_PATH, _RELEASE_ASSETS_CLI
        ),
        reason="mutmut sandbox omits the release workflow and CLI",
    ),
    pytest.mark.skipif(
        os.name == "nt" or shutil.which("bash") is None,
        reason="release reconciliation tests require Bash on a Unix-like system",
    ),
]


def _write_json(path: Path, document: dict[str, object]) -> None:
    """Write one deterministic JSON fixture."""
    path.write_text(json.dumps(document), encoding="utf-8")


def _write_asset(directory: Path, name: str, content: bytes) -> Path:
    """Create one asset and return its path.

    Returns
    -------
    Path
        The created asset path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    return path


def _sha256(content: bytes) -> str:
    """Return the digest used in the local reconciliation fixtures.

    Returns
    -------
    str
        The lower-case SHA-256 digest.
    """
    return hashlib.sha256(content).hexdigest()


def test_mismatches_describe_missing_and_different_destination_assets() -> None:
    """Each missing or differing destination gets a stable diagnostic."""
    problems = release_assets.mismatches(
        ["absent.whl", "digest.whl", "github.whl", "same.whl"],
        {"digest.whl": "one", "github.whl": "two", "same.whl": "same"},
        {"digest.whl": "different", "github.whl": None, "same.whl": "same"},
    )
    assert problems == [
        "absent.whl is missing from PyPI",
        "digest.whl differs: PyPI sha256 one, GitHub sha256 different",
        "github.whl is missing from the GitHub Release or has no digest",
    ], "each missing or differing destination must be identified"


def test_github_digest_parsing_safe_names_and_file_hashes(tmp_path: Path) -> None:
    """GitHub digests normalize, and unsafe names or bytes fail closed."""
    assets_path = tmp_path / "assets.json"
    _write_json(
        assets_path,
        {
            "assets": [
                {"name": "wheel.whl", "digest": "sha256:ABCD"},
                {"name": "unknown.bin", "digest": "md5:abcd"},
            ]
        },
    )
    github_assets = release_assets.read_github_assets(assets_path)
    assert github_assets == {
        "wheel.whl": "abcd",
        "unknown.bin": None,
    }, "only supported GitHub SHA-256 digests must be retained"

    directory = tmp_path / "publish"
    wheel = _write_asset(directory, "a.whl", b"wheel bytes")
    _write_asset(directory, "b.whl", b"other bytes")
    assert release_assets.local_names(directory) == ["a.whl", "b.whl"], (
        "safe asset names must be sorted"
    )
    digest = _sha256(b"wheel bytes")
    assert release_assets.sha256_of(wheel) == digest, "the file digest must match"
    release_assets._check_digest(wheel, digest)
    with pytest.raises(release_assets.DigestMismatchError):
        release_assets._check_digest(wheel, None)

    _write_asset(directory, "bad name.whl", b"unsafe")
    with pytest.raises(release_assets.UnsafeAssetNamesError):
        release_assets.local_names(directory)


def test_asset_errors_keep_actionable_messages() -> None:
    """Handled reconciliation failures retain their workflow details."""
    assert "refusing unsafe artefact names" in str(
        release_assets.UnsafeAssetNamesError(["unsafe name"])
    ), "unsafe names must be included in the error"
    assert "expected wrong" in str(
        release_assets.DigestMismatchError("wheel.whl", "actual", "wrong")
    ), "digest errors must identify the expected value"
    assert "must use HTTPS" in str(release_assets.InsecurePypiDownloadError()), (
        "download errors must require HTTPS"
    )
    assert "no retained provenance" in str(
        release_assets.MissingRetainedProvenanceError()
    ), "carried bytes must retain their provenance"


def test_state_inventory_and_output_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State loading exposes current inventories and writes workflow output."""
    publish = tmp_path / "publish"
    wheel = _write_asset(publish, "cmd_mox-0.3.0.whl", b"wheel")
    index = tmp_path / "index.json"
    _write_json(
        index,
        {
            "files": [
                {
                    "filename": wheel.name,
                    "hashes": {"sha256": _sha256(b"wheel")},
                    "url": "files/wheel.whl",
                }
            ]
        },
    )
    github_assets = tmp_path / "github-assets.json"
    _write_json(
        github_assets,
        {"assets": [{"name": wheel.name, "digest": f"sha256:{_sha256(b'wheel')}"}]},
    )

    state = release_assets.State.load(publish, index, github_assets)
    assert release_assets.carryover(state) == [wheel.name], (
        "GitHub assets must be carried over"
    )
    assert release_assets.missing_from_pypi(state) == [], (
        "the indexed wheel must not be staged for PyPI"
    )
    assert state.plan().canonical[wheel.name] is release_assets.Source.PYPI, (
        "PyPI must be the canonical source when it already has the asset"
    )

    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    release_assets.write_output("remaining", value=False)
    assert output.read_text(encoding="utf-8") == "remaining=false\n", (
        "workflow outputs must use lower-case boolean values"
    )

    empty_state = release_assets.State.load(publish, None, github_assets)
    assert empty_state.pypi == {}, "an omitted index must produce an empty inventory"
    assert release_assets.missing_from_pypi(empty_state) == [wheel.name], (
        "every local asset is missing from an empty PyPI inventory"
    )


def test_stage_pypi_keeps_only_unpublished_canonical_assets(
    tmp_path: Path,
) -> None:
    """PyPI files are dropped and GitHub's canonical bytes replace local ones."""
    publish = tmp_path / "publish"
    github_files = tmp_path / "github-files"
    already = "already.whl"
    carried = "carried.whl"
    local = "local.whl"
    _write_asset(publish, already, b"already")
    _write_asset(publish, carried, b"untrusted local bytes")
    _write_asset(publish, local, b"local bytes")
    carried_bytes = b"canonical GitHub bytes"
    _write_asset(github_files, carried, carried_bytes)
    state = release_assets.State(
        publish=publish,
        pypi={
            already: release_assets.PypiFile(
                _sha256(b"already"), "https://pypi.test/already"
            )
        },
        github={already: _sha256(b"already"), carried: _sha256(carried_bytes)},
    )

    assert release_assets.stage_pypi(state, github_files) is True, (
        "unpublished canonical assets must remain for upload"
    )
    assert not (publish / already).exists(), "PyPI-held assets must be removed"
    assert (publish / carried).read_bytes() == carried_bytes, (
        "GitHub bytes must replace the local copy"
    )
    assert (publish / local).read_bytes() == b"local bytes", (
        "the local asset must remain when neither destination has it"
    )

    published = release_assets.State(
        publish=publish,
        pypi={
            local: release_assets.PypiFile(
                _sha256(b"local bytes"), "https://pypi.test/local"
            )
        },
        github={},
    )
    assert release_assets.stage_pypi(published, github_files) is True, (
        "GitHub-missing assets must still be uploaded to PyPI"
    )

    only_published = release_assets.State(
        publish=publish,
        pypi={
            carried: release_assets.PypiFile(
                _sha256(carried_bytes), "https://pypi.test/carried"
            )
        },
        github={},
    )
    assert release_assets.stage_pypi(only_published, github_files) is False, (
        "an existing PyPI asset must make the upload set empty"
    )
    assert not list(publish.iterdir()), "no PyPI upload assets should remain"


def test_stage_github_copies_local_assets_and_attestation(
    tmp_path: Path,
) -> None:
    """Locally canonical assets and an unattached bundle are staged."""
    name = "cmd_mox-0.3.0-py3-none-any.whl"
    bundle = "cmd-mox-v0.3.0-run42-1.sigstore.json"
    publish = tmp_path / "publish"
    bundles = tmp_path / "bundles"
    output = tmp_path / "output"
    _write_asset(publish, name, b"wheel bytes")
    _write_asset(bundles, bundle, b"provenance")

    local_state = release_assets.State(publish, {}, {})
    assert (
        release_assets._canonical_digest(name, release_assets.Source.LOCAL, local_state)
        is None
    ), "local assets do not have a remote canonical digest"
    assert release_assets.stage_github(local_state, bundles, output) == [], (
        "no PyPI fetch is needed for local assets"
    )
    assert {path.name for path in output.iterdir()} == {name, bundle}, (
        "GitHub must receive the local asset and its provenance bundle"
    )


def test_stage_github_fetches_and_checks_pypi_bytes(tmp_path: Path) -> None:
    """PyPI canonical bytes are fetched by URL and checked against the index."""
    name = "cmd_mox-0.3.0-py3-none-any.whl"
    publish = tmp_path / "publish"
    bundles = tmp_path / "bundles"
    output = tmp_path / "pypi-output"
    content = b"wheel bytes"
    wheel = _write_asset(publish, name, content)
    _write_asset(bundles, "cmd-mox-v0.3.0-run42-1.sigstore.json", b"provenance")
    pypi_state = release_assets.State(
        publish,
        {name: release_assets.PypiFile(_sha256(content), "https://pypi.test/wheel")},
        {},
    )
    assert release_assets.stage_github(pypi_state, bundles, output) == [
        (name, "https://pypi.test/wheel")
    ], "PyPI's canonical bytes must be fetched for GitHub"
    _write_asset(output, name, wheel.read_bytes())
    release_assets.check_fetched(pypi_state, output)
    _write_asset(output, name, b"wrong bytes")
    with pytest.raises(release_assets.DigestMismatchError):
        release_assets.check_fetched(pypi_state, output)


def test_stage_github_keeps_existing_tag_bundle(tmp_path: Path) -> None:
    """A prior bundle for the same tag prevents staging another run's bundle."""
    name = "cmd_mox-0.3.0-py3-none-any.whl"
    bundle = "cmd-mox-v0.3.0-run42-1.sigstore.json"
    publish = tmp_path / "publish"
    bundles = tmp_path / "bundles"
    output = tmp_path / "github-output"
    content = b"wheel bytes"
    _write_asset(publish, name, content)
    _write_asset(bundles, bundle, b"provenance")
    github_state = release_assets.State(
        publish,
        {},
        {
            # GitHub's stored wheel is the canonical source on a re-run.
            name: _sha256(content),
            "cmd-mox-v0.3.0-run7-1.sigstore.json": _sha256(b"older bundle"),
        },
    )
    assert release_assets.stage_github(github_state, bundles, output) == [], (
        "an existing GitHub asset must not be fetched again"
    )
    assert not list(output.iterdir()), "an existing tag bundle must not be duplicated"


def test_stage_github_requires_secure_pypi_and_retained_provenance(
    tmp_path: Path,
) -> None:
    """Carried bytes need HTTPS downloads and a bundle already on the release."""
    name = "cmd_mox-0.3.0.tar.gz"
    publish = tmp_path / "publish"
    bundles = tmp_path / "bundles"
    output = tmp_path / "output"
    current = b"new sdist"
    _write_asset(publish, name, current)
    _write_asset(bundles, "cmd-mox-v0.3.0-run42-1.sigstore.json", b"current bundle")

    insecure = release_assets.State(
        publish,
        {name: release_assets.PypiFile(_sha256(current), "http://pypi.test/file")},
        {},
    )
    with pytest.raises(release_assets.InsecurePypiDownloadError):
        release_assets.stage_github(insecure, bundles, output)

    carried = release_assets.State(
        publish,
        {
            name: release_assets.PypiFile(
                _sha256(b"older sdist"), "https://pypi.test/file"
            )
        },
        {},
    )
    with pytest.raises(release_assets.MissingRetainedProvenanceError):
        release_assets.stage_github(carried, bundles, output)
    assert release_assets.verify(carried, bundles) == [
        f"{name} is missing from the GitHub Release or has no digest",
        "carried-over release assets have no retained provenance bundle",
    ], "verification must report missing bytes and retained provenance"

    retained = release_assets.State(
        publish,
        carried.pypi,
        {"cmd-mox-v0.3.0-run7-1.sigstore.json": _sha256(b"old bundle")},
    )
    retained_output = tmp_path / "retained-output"
    assert release_assets.stage_github(retained, bundles, retained_output) == [
        (name, "https://pypi.test/file")
    ], "carried PyPI bytes must be fetched from their canonical URL"
    assert not list(retained_output.iterdir()), "the existing bundle must be reused"


def test_verify_checks_destination_digests_and_bundle_families(
    tmp_path: Path,
) -> None:
    """Verification accepts matching bytes and reports absent tag bundles."""
    name = "cmd_mox-0.3.0.tar.gz"
    bundle = "cmd-mox-v0.3.0-run42-1.sigstore.json"
    content = b"sdist"
    publish = tmp_path / "publish"
    bundles = tmp_path / "bundles"
    _write_asset(publish, name, content)
    _write_asset(bundles, bundle, b"bundle")
    digest = _sha256(content)

    incomplete = release_assets.State(
        publish,
        {name: release_assets.PypiFile(digest, "https://pypi.test/file")},
        {name: digest},
    )
    assert release_assets.verify(incomplete, bundles) == [
        f"{bundle} is missing from the GitHub Release"
    ], "verification must require an attached provenance bundle"

    complete = release_assets.State(
        publish,
        incomplete.pypi,
        {
            name: digest,
            "cmd-mox-v0.3.0-run7-1.sigstore.json": _sha256(b"older bundle"),
        },
    )
    assert release_assets.verify(complete, bundles) == [], (
        "matching destinations and a retained bundle must verify cleanly"
    )

    carried_over = release_assets.State(
        publish,
        {
            name: release_assets.PypiFile(
                _sha256(b"older sdist"), "https://pypi.test/file"
            )
        },
        {
            name: _sha256(b"older sdist"),
            "cmd-mox-v0.3.0-run7-1.sigstore.json": _sha256(b"older bundle"),
        },
    )
    assert release_assets.verify(carried_over, bundles) == [], (
        "carried bytes are valid when their prior tag bundle remains attached"
    )
