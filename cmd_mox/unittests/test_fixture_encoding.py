"""Fixture files are UTF-8, whatever the process locale.

These tests are the discriminating evidence for the encoding fix: the
subprocess test promotes ``EncodingWarning`` to an error, and the byte order
mark test fails with ``JSONDecodeError`` when the reader follows the locale.
"""

from __future__ import annotations

import dataclasses as dc
import json
import subprocess
import sys
import textwrap
import typing as typ

from cmd_mox.record.fixture import FixtureFile, FixtureMetadata
from cmd_mox.unittests._replay_helpers import make_recorded_invocation

if typ.TYPE_CHECKING:
    from pathlib import Path


def _encoding_fixture() -> FixtureFile:
    """Return a fixture with ASCII-only content for byte-level assertions.

    Returns
    -------
    FixtureFile
        A one-recording fixture with fixed metadata.
    """
    return FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata(
            created_at="2026-01-15T10:30:00+00:00",
            cmdmox_version="0.2.0",
            platform="linux",
            python_version="3.12.0",
        ),
        recordings=[make_recorded_invocation()],
        scrubbing_rules=[],
    )


_ENCODING_PROBE = textwrap.dedent(
    """
    import sys
    import warnings
    from pathlib import Path

    from cmd_mox.record.fixture import (
        FixtureFile,
        FixtureMetadata,
        RecordedInvocation,
    )

    fixture = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata(
            created_at="2026-01-15T10:30:00+00:00",
            cmdmox_version="0.2.0",
            platform="linux",
            python_version="3.12.0",
        ),
        recordings=[
            RecordedInvocation(
                sequence=0,
                command="git",
                args=["status"],
                stdin="",
                env_subset={},
                stdout="ok\\n",
                stderr="",
                exit_code=0,
                timestamp="2026-01-15T10:30:01+00:00",
                duration_ms=0,
            )
        ],
        scrubbing_rules=[],
    )

    warnings.simplefilter("error", EncodingWarning)
    target = Path(sys.argv[1])
    fixture.save(target)
    FixtureFile.load(target)
    print("ok")
    """
)


class TestFixtureEncoding:
    """Fixture text I/O is UTF-8 and tolerant of a leading byte order mark."""

    def test_save_and_load_specify_an_encoding(self, tmp_path: Path) -> None:
        """Neither save() nor load() relies on the process locale encoding."""
        target = tmp_path / "probe.json"

        completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the probe runs the current interpreter with a fixed script
            [
                sys.executable,
                "-X",
                "warn_default_encoding",
                "-c",
                _ENCODING_PROBE,
                str(target),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "ok"

    def test_save_writes_plain_ascii_json_without_a_bom(self, tmp_path: Path) -> None:
        """ASCII content is written exactly as json.dumps produces it."""
        fixture = _encoding_fixture()
        path = tmp_path / "plain.json"

        fixture.save(path)

        expected = (json.dumps(fixture.to_dict(), indent=2) + "\n").encode("ascii")
        assert path.read_bytes().replace(b"\r\n", b"\n") == expected

    def test_load_tolerates_a_leading_byte_order_mark(self, tmp_path: Path) -> None:
        """A fixture saved with a UTF-8 BOM, as some editors do, still loads."""
        fixture = _encoding_fixture()
        plain = tmp_path / "plain.json"
        fixture.save(plain)
        bom_path = tmp_path / "bom.json"
        bom_path.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())

        assert FixtureFile.load(bom_path) == fixture

    def test_load_reads_non_ascii_text_as_utf8(self, tmp_path: Path) -> None:
        """Non-ASCII output written as UTF-8 loads with the same text."""
        fixture = dc.replace(
            _encoding_fixture(),
            recordings=[
                dc.replace(
                    make_recorded_invocation(), stdout="héllo ✓\n", stderr="warn ⚠\n"
                )
            ],
        )
        path = tmp_path / "unicode.json"
        path.write_bytes(
            (json.dumps(fixture.to_dict(), indent=2, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
        )

        loaded = FixtureFile.load(path)

        assert loaded.recordings[0].stdout == "héllo ✓\n"
        assert loaded.recordings[0].stderr == "warn ⚠\n"
