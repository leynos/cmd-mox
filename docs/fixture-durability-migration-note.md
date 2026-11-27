# Fixture durability migration note

This note covers the change in which `FixtureFile.save()` writes through an
internal atomic-write helper. The JSON schema does not change, and
`FixtureFile.load()` still reads fixtures exactly as before.

## What changes

- **Owner-only fixtures on POSIX.** New fixtures are created with mode
  `0o600`, whatever the process umask allows. Previously the file took the
  umask-derived default, usually `0o644`. Git tracks only the execute bit, so
  committed fixtures are unaffected; a shared fixture that another user or a CI
  step must read needs an explicit `chmod` after recording.
- **Atomic replacement.** CmdMox serializes the document, writes a hidden
  sibling temporary file, flushes it to stable storage, and renames it over the
  destination. On POSIX a reader sees either the previous complete fixture or
  the new one, never a partial document. On Windows the replacement is a single
  metadata operation but is not documented as atomic.
- **Failure behaviour.** A save that fails before the rename leaves the
  previous fixture byte-for-byte intact. An ordinary exception after the
  rename, while synchronizing the directory, raises `AtomicWriteError` with
  `target_replaced` true: the new fixture is already in place and is not rolled
  back. An interrupt such as `KeyboardInterrupt` propagates unchanged after the
  same clean-up.
- **LF newlines on Windows.** The helper writes binary, so fixture bytes use LF
  on every platform. The previous `Path.write_text(...)` implementation
  translated `\n` to `\r\n` on Windows, so a fixture recorded on Windows
  changes byte-for-byte on its first save after upgrading. The JSON content is
  unchanged, and existing CRLF fixtures still load.
- **Orphaned temporary files.** A process killed hard (`SIGKILL`, the
  out-of-memory killer, or `pytest-timeout`'s thread method) can leave one
  temporary file beside the fixture. Its name is dot-prefixed and ends in
  `.tmp`; a long fixture name is truncated in the prefix, so the fixture name
  may not appear. Add `.*.tmp` to `.gitignore` so such a file is never
  committed. A sweep of orphans is planned for roadmap item `14.2`.

## What does not change

- The fixture JSON schema and the `version` field.
- `FixtureFile.load()` and its strict validation.
- The serialized bytes on POSIX, which are identical to the previous writer.

## What to do when upgrading

1. If a workflow shares newly recorded fixtures with another account or reads
   them from a CI step that runs as a different user, apply the mode that
   workflow needs after recording.
2. If a workflow compares fixture bytes against a committed copy, re-record the
   Windows fixtures once so the LF form is committed.
3. Add `.*.tmp` to the repository's `.gitignore` where fixtures are recorded.
4. Catch `AtomicWriteError` where a test must distinguish a pre-rename failure,
   which preserves the previous fixture, from a post-rename failure, which does
   not roll back.
