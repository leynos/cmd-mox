"""Release asset CLI tests for explicit filesystem and JSON boundaries."""

from __future__ import annotations

import hashlib
import json
import typing as typ

import pytest
from helpers.release_workflow import digest_bundle

if typ.TYPE_CHECKING:
    from pathlib import Path

assets = pytest.importorskip("scripts.release_assets")
adapters = pytest.importorskip("scripts.release_assets_cli")


class _CLIInventories(typ.NamedTuple):
    """Describe local and remote files for one adapter invocation."""

    built: dict[str, bytes]
    pypi: dict[str, bytes]
    github: dict[str, bytes]
    retained: dict[str, dict[str, bytes]] | None = None


def _sha256(content: bytes) -> str:
    """Return a fixture's SHA-256 digest.

    Returns
    -------
    str
        The lower-case hexadecimal digest.
    """
    return hashlib.sha256(content).hexdigest()


def _write(directory: Path, name: str, content: bytes) -> Path:
    """Write an adapter fixture and return its path.

    Returns
    -------
    pathlib.Path
        The created file path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    return path


def test_adapter_reads_safe_names_hashes_json_and_sigstore_subjects(
    tmp_path: Path,
) -> None:
    """The adapter validates and loads all environmental values explicitly."""
    wheel = _write(tmp_path / "publish", "a.whl", b"wheel")
    assert adapters.local_names(wheel.parent) == ["a.whl"], (
        "directory inventory must be stable"
    )
    assert adapters.sha256_of(wheel) == _sha256(b"wheel"), (
        "file hashing must use SHA-256"
    )
    adapters.check_digest(wheel, _sha256(b"wheel"))
    with pytest.raises(assets.DigestMismatchError):
        adapters.check_digest(wheel, None)
    unsafe = _write(wheel.parent, "bad name.whl", b"unsafe")
    with pytest.raises(assets.UnsafeAssetNamesError):
        adapters.local_names(wheel.parent)
    unsafe.unlink()
    index = _write(
        tmp_path,
        "index.json",
        json.dumps({
            "files": [
                {
                    "filename": "a.whl",
                    "hashes": {"sha256": _sha256(b"wheel").upper()},
                    "url": "a.whl",
                }
            ]
        }).encode(),
    )
    inventory = adapters.read_pypi_index(index)
    assert inventory["a.whl"].sha256 == _sha256(b"wheel"), (
        "PyPI hashes normalize to lower-case"
    )
    assert inventory["a.whl"].url.endswith("/cmd-mox/a.whl"), (
        "relative URLs resolve against the project index"
    )
    gh = _write(
        tmp_path,
        "assets.json",
        json.dumps({
            "assets": [
                {"name": "a.whl", "digest": f"sha256:{_sha256(b'wheel')}"},
                {"name": "other.whl", "digest": "md5:unsupported"},
            ]
        }).encode(),
    )
    assert adapters.read_github_assets(gh)["other.whl"] is None, (
        "unsupported GitHub digest types remain absent"
    )
    bundle = _write(
        tmp_path / "bundles",
        "cmd-mox-v0.3.0-run1-1.sigstore.json",
        digest_bundle({"a.whl": _sha256(b"wheel")}),
    )
    assert adapters.read_bundle_subjects(bundle) == frozenset({
        ("a.whl", _sha256(b"wheel"))
    }), "DSSE subjects must retain their names and digests"
    output = tmp_path / "output"
    adapters.write_output(output, "remaining", value=False)
    assert output.read_text() == "remaining=false\n", (
        "the output path is an explicit adapter input"
    )


@pytest.mark.parametrize(
    "content",
    [
        b"not JSON",
        b'{"dsseEnvelope":{"payloadType":"wrong","payload":""}}',
        b'{"dsseEnvelope":{"payloadType":"application/vnd.in-toto+json","payload":"?"}}',
        digest_bundle({"a.whl": "not-sha256"}),
    ],
)
def test_adapter_rejects_malformed_or_unrelated_provenance(
    tmp_path: Path, content: bytes
) -> None:
    """A malformed bundle never supplies provenance coverage."""
    path = _write(tmp_path, "bundle.sigstore.json", content)
    with pytest.raises((assets.ReleaseAssetError, ValueError)):
        adapters.read_bundle_subjects(path)


def _write_build_inputs(root: Path, built: dict[str, bytes]) -> None:
    """Write local distributions and their current-run provenance bundle."""
    for name, content in built.items():
        _write(root / "publish", name, content)
    _write(
        root / "bundles",
        "cmd-mox-v0.3.0-run2-1.sigstore.json",
        digest_bundle({name: _sha256(content) for name, content in built.items()}),
    )


def _write_retained_inputs(
    root: Path, github: dict[str, bytes], retained: dict[str, dict[str, bytes]] | None
) -> dict[str, bytes]:
    """Write GitHub files and return the complete asset set with retained bundles.

    Returns
    -------
    dict[str, bytes]
        GitHub distribution files and encoded retained provenance bundles.
    """
    directory = root / "retained"
    directory.mkdir(exist_ok=True)
    remote = dict(github)
    for name, subjects in (retained or {}).items():
        remote[name] = digest_bundle({
            subject: _sha256(content) for subject, content in subjects.items()
        })
    for name, content in remote.items():
        _write(directory, name, content)
    return remote


def _write_index(root: Path, pypi: dict[str, bytes]) -> None:
    """Write the PEP 691 inventory for the fixture's PyPI bytes."""
    _write(
        root,
        "index.json",
        json.dumps({
            "files": [
                {
                    "filename": name,
                    "hashes": {"sha256": _sha256(content)},
                    "url": f"https://pypi.test/{name}",
                }
                for name, content in pypi.items()
            ]
        }).encode(),
    )


def _write_assets(root: Path, remote: dict[str, bytes]) -> None:
    """Write the GitHub release asset names and their digests."""
    _write(
        root,
        "assets.json",
        json.dumps({
            "assets": [
                {"name": name, "digest": f"sha256:{_sha256(content)}"}
                for name, content in remote.items()
            ]
        }).encode(),
    )


def _cli_inputs(root: Path, inventories: _CLIInventories) -> list[str]:
    """Write adapter inputs and return explicit path arguments.

    Returns
    -------
    list[str]
        Common CLI arguments for this fixture's immutable inventories.
    """
    _write_build_inputs(root, inventories.built)
    remote = _write_retained_inputs(root, inventories.github, inventories.retained)
    _write_index(root, inventories.pypi)
    _write_assets(root, remote)
    return [
        "--publish",
        str(root / "publish"),
        "--index",
        str(root / "index.json"),
        "--assets",
        str(root / "assets.json"),
        "--bundles",
        str(root / "bundles"),
        "--github-files",
        str(root / "retained"),
    ]


def test_cli_pypi_commands_validate_then_copy_or_remove_canonical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The I/O adapter executes the pure source decision after validating subjects."""
    prior = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    argv = _cli_inputs(
        tmp_path,
        _CLIInventories(
            built={
                "local.whl": b"new",
                "github.whl": b"rebuilt",
                "pypi.whl": b"rebuilt",
            },
            pypi={"pypi.whl": b"old pypi"},
            github={"github.whl": b"old github"},
            retained={prior: {"github.whl": b"old github", "pypi.whl": b"old pypi"}},
        ),
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    assert adapters.main(["carryover", *argv]) == 0, (
        "carry-over must expose inspected bundle downloads"
    )
    assert prior in capsys.readouterr().out, (
        "retained bundles must be carried to later jobs"
    )
    assert adapters.main(["missing-pypi", *argv]) == 0, (
        "the adapter must expose absent index names"
    )
    assert "local.whl" in capsys.readouterr().out, (
        "the local upload must remain pending"
    )
    assert adapters.main(["stage-pypi", *argv]) == 0, (
        "canonical provenance must permit PyPI staging"
    )
    assert (tmp_path / "publish" / "github.whl").read_bytes() == b"old github", (
        "GitHub's canonical bytes replace rebuilt bytes"
    )
    assert not (tmp_path / "publish" / "pypi.whl").exists(), (
        "published PyPI names must be removed"
    )
    assert (tmp_path / "output").read_text() == "remaining=true\n", (
        "a remaining canonical upload enables publication"
    )
    assert adapters.main(["stage-pypi", *argv]) == 0, (
        "remaining local and GitHub names can be staged again"
    )


def test_cli_github_commands_check_downloads_and_final_attached_subjects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Direct adapter coverage retains the workflow command's observable boundaries."""
    argv = _cli_inputs(
        tmp_path,
        _CLIInventories(built={"a.whl": b"wheel"}, pypi={"a.whl": b"wheel"}, github={}),
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    output = tmp_path / "upload"
    assert adapters.main(["stage-github", *argv, "--output", str(output)]) == 0, (
        "an attested canonical file may be staged"
    )
    assert "a.whl\thttps://pypi.test/a.whl" in capsys.readouterr().out, (
        "the adapter emits the pure HTTPS fetch decision"
    )
    _write(output, "a.whl", b"wrong")
    assert adapters.main(["check-fetched", *argv, "--output", str(output)]) == 1, (
        "wrong download bytes fail closed"
    )
    _write(output, "a.whl", b"wheel")
    assert adapters.main(["check-fetched", *argv, "--output", str(output)]) == 0, (
        "matching download bytes pass"
    )
    bundle = "cmd-mox-v0.3.0-run2-1.sigstore.json"
    bundle_digest = adapters.sha256_of(tmp_path / "bundles" / bundle)
    _write(
        tmp_path,
        "assets.json",
        json.dumps({
            "assets": [
                {"name": "a.whl", "digest": f"sha256:{_sha256(b'wheel')}"},
                {
                    "name": bundle,
                    "digest": f"sha256:{bundle_digest}",
                },
            ]
        }).encode(),
    )
    assert adapters.main(["verify", *argv]) == 0, (
        "the attached current bundle must cover canonical bytes"
    )
    assert "hold the same bytes" in capsys.readouterr().out, (
        "verification emits a digest agreement message"
    )
    _write(
        tmp_path / "bundles",
        bundle,
        digest_bundle({"a.whl": _sha256(b"wrong")}),
    )
    assert adapters.main(["verify", *argv]) == 1, (
        "an attached name with changed bundle bytes must fail its asset digest"
    )


def test_cli_annotates_missing_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PyPI staging reports its required index at the adapter boundary."""
    _cli_inputs(
        tmp_path, _CLIInventories(built={"a.whl": b"wheel"}, pypi={}, github={})
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    assert (
        adapters.main([
            "stage-pypi",
            "--publish",
            str(tmp_path / "publish"),
            "--assets",
            str(tmp_path / "assets.json"),
        ])
        == 1
    ), "PyPI commands must explicitly require an index"
    assert "--index is required" in capsys.readouterr().err, (
        "missing options must be annotated"
    )


def test_cli_annotates_invalid_bundle_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unexpected provenance names fail before the adapter mutates files."""
    argv = _cli_inputs(
        tmp_path, _CLIInventories(built={"a.whl": b"wheel"}, pypi={}, github={})
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    _write(tmp_path / "bundles", "unexpected.json", b"{}")
    assert (
        adapters.main(["stage-github", *argv, "--output", str(tmp_path / "upload")])
        == 1
    ), "unexpected handoff files fail before mutation"
    assert "unexpected provenance bundle name" in capsys.readouterr().err, (
        "the input boundary must identify an invalid bundle name"
    )


def test_cli_annotates_missing_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Output-producing commands require their workflow output path."""
    argv = _cli_inputs(
        tmp_path, _CLIInventories(built={"a.whl": b"wheel"}, pypi={}, github={})
    )
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert adapters.main(["stage-pypi", *argv]) == 1, (
        "output-producing commands require their environment path"
    )
    assert "::error title=release-assets::" in capsys.readouterr().err, (
        "handled input failures need workflow annotations"
    )


def test_cli_rejects_insecure_selected_download_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Transport URL validation belongs at the command adapter boundary."""
    argv = _cli_inputs(
        tmp_path,
        _CLIInventories(built={"a.whl": b"wheel"}, pypi={"a.whl": b"wheel"}, github={}),
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    index = tmp_path / "index.json"
    index.write_text(
        index.read_text().replace("https://pypi.test/", "http://pypi.test/")
    )
    assert (
        adapters.main(["stage-github", *argv, "--output", str(tmp_path / "upload")])
        == 1
    ), "insecure selected URLs must fail before copying assets"
    assert "every PyPI download must use HTTPS" in capsys.readouterr().err, (
        "the transport failure must be annotated"
    )
    assert not (tmp_path / "upload").exists(), (
        "failed URL validation must not stage files"
    )
