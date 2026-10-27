"""Release asset CLI tests for explicit filesystem and JSON boundaries."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import typing as typ
from pathlib import Path

import pytest
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    digest_bundle,
    install_fake_tools,
    should_skip_release_tests_for_mutation_sandbox,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
if should_skip_release_tests_for_mutation_sandbox(
    Path.cwd(), RELEASE_WORKFLOW_PATH, _RELEASE_ASSETS_CLI
):
    pytest.skip(
        "mutmut sandbox omits the release workflow and CLI", allow_module_level=True
    )

assets = typ.cast("typ.Any", importlib.import_module("scripts.release_assets"))
adapters = typ.cast("typ.Any", importlib.import_module("scripts.release_assets_cli"))
asset_io = typ.cast("typ.Any", importlib.import_module("scripts.release_assets_io"))


class _CLIInventories(typ.NamedTuple):
    """Describe local and remote files for one adapter invocation."""

    built: dict[str, bytes]
    pypi: dict[str, bytes]
    github: dict[str, bytes]
    retained: dict[str, dict[str, bytes]] | None = None


class _AttestationCandidate(typ.NamedTuple):
    """One local artifact and bundle row selected for signature verification."""

    artifact: str
    bundle: str
    bundle_name: str
    filename: str
    digest: str


def _attestation_candidate(line: str) -> _AttestationCandidate:
    """Parse one tab-separated workflow verification candidate.

    Returns
    -------
    _AttestationCandidate
        The parsed artifact path, bundle path, filename, and digest.
    """
    fields = line.split("\t")
    assert len(fields) == 5, "candidate rows must contain five tab-separated fields"
    return _AttestationCandidate(
        artifact=fields[0],
        bundle=fields[1],
        bundle_name=fields[2],
        filename=fields[3],
        digest=fields[4],
    )


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


def _enable_fake_gh(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route GitHub CLI calls through the recording test executable.

    Returns
    -------
    pathlib.Path
        Path to the recording tool log.
    """
    fake_bin = install_fake_tools(root / "bin")
    tool_log = root / "tool-log.jsonl"
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TOOL_LOG", str(tool_log))
    return tool_log


def test_adapter_reads_safe_names_hashes_json_and_sigstore_subjects(
    tmp_path: Path,
) -> None:
    """The adapter validates and loads all environmental values explicitly."""
    wheel = _write(tmp_path / "publish", "a.whl", b"wheel")
    assert asset_io.local_names(wheel.parent) == ["a.whl"], (
        "directory inventory must be stable"
    )
    assert asset_io.sha256_of(wheel) == _sha256(b"wheel"), (
        "file hashing must use SHA-256"
    )
    asset_io.check_digest(wheel, _sha256(b"wheel"))
    with pytest.raises(assets.DigestMismatchError):
        asset_io.check_digest(wheel, None)
    unsafe = _write(wheel.parent, "bad name.whl", b"unsafe")
    with pytest.raises(asset_io.UnsafeAssetNamesError):
        asset_io.local_names(wheel.parent)
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
    inventory = asset_io.read_pypi_index(index)
    assert inventory.digests["a.whl"] == _sha256(b"wheel"), (
        "PyPI hashes normalize to lower-case"
    )
    assert inventory.urls["a.whl"].endswith("/cmd-mox/a.whl"), (
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
    assert asset_io.read_github_assets(gh)["other.whl"] is None, (
        "unsupported GitHub digest types remain absent"
    )
    bundle = _write(
        tmp_path / "bundles",
        "cmd-mox-v0.3.0-run1-1.sigstore.json",
        digest_bundle({"a.whl": _sha256(b"wheel")}),
    )
    assert asset_io.read_bundle_subjects(bundle) == frozenset({
        ("a.whl", _sha256(b"wheel"))
    }), "DSSE subjects must retain their names and digests"
    output = tmp_path / "output"
    asset_io.write_output(output, "remaining", value=False)
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
    with pytest.raises(assets.ReleaseAssetError):
        asset_io.read_bundle_subjects(path)


def test_adapter_reports_malformed_registry_shapes_as_release_errors(
    tmp_path: Path,
) -> None:
    """Malformed registry mappings are handled at their JSON input boundary."""
    index = _write(
        tmp_path,
        "index.json",
        b'{"files":[{"filename":"a.whl"}]}',
    )
    github = _write(tmp_path, "assets.json", b'{"assets":[null]}')
    bundle = _write(
        tmp_path,
        "bundle.sigstore.json",
        b'{"dsseEnvelope":[]}',
    )

    with pytest.raises(assets.ReleaseAssetError, match="invalid PEP 691 PyPI index"):
        asset_io.read_pypi_index(index)
    with pytest.raises(
        assets.ReleaseAssetError, match="invalid GitHub release asset inventory"
    ):
        asset_io.read_github_assets(github)
    with pytest.raises(
        assets.ReleaseAssetError, match="invalid Sigstore bundle structure"
    ):
        asset_io.read_bundle_subjects(bundle)


def test_adapter_reports_malformed_registry_json_as_release_errors(
    tmp_path: Path,
) -> None:
    """Invalid JSON from either registry is translated at the input boundary."""
    index = _write(tmp_path, "invalid-index.json", b"{")
    github = _write(tmp_path, "invalid-assets.json", b"{")

    with pytest.raises(assets.ReleaseAssetError, match="invalid PEP 691 PyPI index"):
        asset_io.read_pypi_index(index)
    with pytest.raises(
        assets.ReleaseAssetError, match="invalid GitHub release asset inventory"
    ):
        asset_io.read_github_assets(github)


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
    verified_rows = []
    for directory in (root / "bundles", root / "retained"):
        for bundle in sorted(directory.glob("*.sigstore.json")):
            verified_rows.extend(
                f"{bundle.name}\t{name}\t{digest}\n"
                for name, digest in asset_io.read_bundle_subjects(bundle)
            )
    _write(root, "verified-subjects.tsv", "".join(verified_rows).encode())
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
        "--verified-subjects",
        str(root / "verified-subjects.tsv"),
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
    _enable_fake_gh(tmp_path, monkeypatch)
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


def test_attestation_checks_lists_exact_current_and_retained_pairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow verifier receives only digest-matched required subjects."""
    prior = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    argv = _cli_inputs(
        tmp_path,
        _CLIInventories(
            built={"local.whl": b"current", "github.whl": b"rebuilt"},
            pypi={},
            github={"github.whl": b"retained"},
            retained={prior: {"github.whl": b"retained"}},
        ),
    )
    assert adapters.main(["attestation-checks", "--purpose", "pypi", *argv]) == 0, (
        "the CLI must prepare signature checks before staging"
    )
    rows = tuple(
        _attestation_candidate(line) for line in capsys.readouterr().out.splitlines()
    )
    assert len(rows) == 2, "the workflow needs both retained and current checks"
    assert rows[0] == _AttestationCandidate(
        artifact=str(tmp_path / "retained" / "github.whl"),
        bundle=str(tmp_path / "retained" / prior),
        bundle_name=prior,
        filename="github.whl",
        digest=_sha256(b"retained"),
    ), "the retained distribution must be verified against its retained bundle"
    assert rows[1] == _AttestationCandidate(
        artifact=str(tmp_path / "publish" / "local.whl"),
        bundle=str(tmp_path / "bundles" / "cmd-mox-v0.3.0-run2-1.sigstore.json"),
        bundle_name="cmd-mox-v0.3.0-run2-1.sigstore.json",
        filename="local.whl",
        digest=_sha256(b"current"),
    ), "the current distribution must be verified against its current bundle"


def test_attestation_checks_hashes_downloaded_github_assets_without_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Downloaded release bytes supply a digest when GitHub omits one."""
    prior = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    canonical_bytes = b"retained GitHub distribution"
    argv = _cli_inputs(
        tmp_path,
        _CLIInventories(
            built={"github.whl": b"rebuilt distribution"},
            pypi={},
            github={"github.whl": canonical_bytes},
            retained={prior: {"github.whl": canonical_bytes}},
        ),
    )
    assets_path = tmp_path / "assets.json"
    inventory = json.loads(assets_path.read_text(encoding="utf-8"))
    next(asset for asset in inventory["assets"] if asset["name"] == "github.whl")[
        "digest"
    ] = None
    assets_path.write_text(json.dumps(inventory), encoding="utf-8")

    assert adapters.main(["attestation-checks", "--purpose", "pypi", *argv]) == 0, (
        "attestation planning hashes the downloaded canonical GitHub bytes"
    )
    candidate = _attestation_candidate(capsys.readouterr().out.strip())
    assert candidate.digest == _sha256(canonical_bytes), (
        "the signature check uses the digest of downloaded GitHub bytes"
    )

    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "workflow-output"))
    assert adapters.main(["stage-pypi", *argv]) == 0, (
        "PyPI staging accepts the digest recovered from downloaded GitHub bytes"
    )
    assert (tmp_path / "publish" / "github.whl").read_bytes() == canonical_bytes, (
        "the existing GitHub bytes remain canonical"
    )


def test_pypi_staging_rejects_subjects_missing_from_verified_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unverified bundle claims cannot authorize a PyPI distribution upload."""
    argv = _cli_inputs(
        tmp_path,
        _CLIInventories(built={"a.whl": b"wheel"}, pypi={}, github={}),
    )
    (tmp_path / "verified-subjects.tsv").write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))

    assert adapters.main(["stage-pypi", *argv]) == 1, (
        "an unsigned subject must fail the release step"
    )
    assert "has no matching provenance subject" in capsys.readouterr().err, (
        "the missing verification result must be annotated"
    )
    assert (tmp_path / "publish" / "a.whl").read_bytes() == b"wheel", (
        "verification must run before staging mutates distributions"
    )
    assert not (tmp_path / "output").exists(), (
        "failed provenance must not report pending upload work"
    )


def test_cli_github_commands_check_downloads_and_final_attached_subjects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Direct adapter coverage retains the workflow command's observable boundaries."""
    argv = _cli_inputs(
        tmp_path,
        _CLIInventories(built={"a.whl": b"wheel"}, pypi={"a.whl": b"wheel"}, github={}),
    )
    _enable_fake_gh(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    output = tmp_path / "upload"
    assert adapters.main(["fetch-github", *argv]) == 0, (
        "PyPI download planning runs before provenance is trusted"
    )
    assert "a.whl\thttps://pypi.test/a.whl" in capsys.readouterr().out, (
        "the adapter emits the HTTPS download decision"
    )
    _write(output, "a.whl", b"wrong")
    assert adapters.main(["check-fetched", *argv, "--output", str(output)]) == 1, (
        "wrong download bytes fail closed"
    )
    _write(output, "a.whl", b"wheel")
    assert adapters.main(["check-fetched", *argv, "--output", str(output)]) == 0, (
        "matching download bytes pass"
    )
    assert adapters.main(["stage-github", *argv, "--output", str(output)]) == 0, (
        "verified provenance permits GitHub staging"
    )
    capsys.readouterr()
    bundle = "cmd-mox-v0.3.0-run2-1.sigstore.json"
    bundle_digest = asset_io.sha256_of(tmp_path / "bundles" / bundle)
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
    assert adapters.main(["verify", *argv, "--output", str(output)]) == 0, (
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
    assert adapters.main(["verify", *argv, "--output", str(output)]) == 1, (
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
    assert capsys.readouterr().err.strip() == (
        "::error title=release-assets::GITHUB_OUTPUT is required for stage-pypi"
    ), "missing workflow output must identify the required variable and command"


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
    assert adapters.main(["fetch-github", *argv]) == 1, (
        "insecure selected URLs must fail before downloading assets"
    )
    assert "every PyPI download must use HTTPS" in capsys.readouterr().err, (
        "the transport failure must be annotated"
    )
    assert not (tmp_path / "upload").exists(), "failed URL validation stages nothing"
