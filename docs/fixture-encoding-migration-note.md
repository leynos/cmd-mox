# Fixture encoding migration note

This note describes the change to how fixture files are encoded on disk. For
the complete policy, see the
[fixture encoding section of the Usage Guide](usage-guide.md#fixture-file-encoding).

## What changes

Fixture JSON is written as UTF-8 and read as UTF-8 regardless of the process
locale. A file that begins with one UTF-8 byte order mark is accepted on load,
and the mark is not included in any parsed value.

The JSON shape and the schema version are unchanged. Only the byte encoding of
the file is now fixed instead of following the machine's locale.

## What this fixes

Reading with the locale encoding made the same fixture parse or fail depending
on the machine it ran on. A fixture saved with a byte order mark, as some
Windows editors do, failed with `Unexpected UTF-8 BOM`.

## What existing fixtures need

ASCII-only fixtures need no change; they are byte-for-byte identical under
UTF-8 and under a single-byte locale.

A fixture that contains non-ASCII text and was written on a machine whose
locale encoding is not UTF-8 may not load, or may load with mis-decoded text,
until it is re-saved as UTF-8. Re-save it with an editor or command that writes
UTF-8, or re-record it with the updated release, and verify the non-ASCII
values after re-saving. Keep a copy of the original until the re-saved fixture
has been replayed successfully.

## Upgrade shared fixture workflows

Upgrade every CmdMox installation that reads or writes a shared fixture before
distributing fixtures saved by the updated release, so every consumer reads
them as UTF-8. Releases before this change follow the locale; on a UTF-8 host
they read the new files correctly, and on a single-byte-locale host they do not.
