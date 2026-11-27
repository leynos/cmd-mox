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

Most fixtures need no change. `FixtureFile.save()` uses `json.dumps()` with its
default `ensure_ascii=True`, so the bytes it writes are pure ASCII even when a
recorded value contains non-ASCII text: those characters appear as `\uXXXX`
escapes. A file like that loads identically under UTF-8 and under any
ASCII-compatible locale, so older releases read files written by the updated
writer without conversion.

The case that needs attention is a file whose *bytes* are non-ASCII UTF-8: a
fixture hand-edited and saved as UTF-8, or one written by a tool that emits raw
UTF-8 instead of escapes. The updated reader accepts it on any machine; an
older release reads it with the locale encoding, so on a non-UTF-8 host the
text may fail to decode or decode incorrectly. Either keep such a fixture with
the updated release, or re-save it through `FixtureFile.save()` so the
non-ASCII values become escapes again. Verify the affected values after
re-saving, and keep a copy of the original until the re-saved fixture has been
replayed successfully.

The third case is a legacy file whose bytes are in some other single-byte
encoding, such as a hand-edited `cp1252` fixture that an older release read
correctly under a matching locale. Decoding those bytes as UTF-8 can fail with
`UnicodeDecodeError`, and it can also succeed with silently different values:
the bytes `\xc3\xa9` are `Ã©` in `cp1252` but `é` in UTF-8. Decode the file
with its known original encoding before converting it to UTF-8, or pass the
decoded values through `FixtureFile.save()` so they become escapes. Keep a copy
of the original, convert from the encoding the file was actually written in
rather than guessing, and verify the values after replaying it — a successful
load does not prove the text is right.

## Upgrade shared fixture workflows

Upgrade every CmdMox installation that reads or writes a shared fixture before
distributing fixtures that contain non-ASCII UTF-8 bytes. Files whose non-ASCII
values are JSON escapes are portable to older releases on ASCII-compatible
hosts; raw UTF-8 bytes are not, so a mixed-version estate should either re-save
those fixtures through the updated writer or upgrade every consumer.
