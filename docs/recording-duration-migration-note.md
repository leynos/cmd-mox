# Recording duration migration note

This note helps projects prepare for the next minor release, which records a
real duration for commands captured by `.record()`. It does not assume a
release number. For the user-facing summary see the
[durations section of the User Guide](users-guide.md#recording-durations), and
for the full definition see
[automatic recording with `.record()`](usage-guide.md#automatic-recording-with-record).

## What changes

Recordings written by `.record()` store the real command's wall-clock duration
in `duration_ms`. Earlier releases always wrote `0`. The interval runs from
when CmdMox issues the passthrough directive to when the shim reports the
result, so the stored value includes IPC and scheduling overhead as well as the
command's own execution time. Durations are non-negative integer milliseconds;
a clock that moves backwards is clamped to zero.

The change affects new recordings only. Fixtures already on disk keep the
`duration_ms` values they stored, and the field remains descriptive: replay,
matching and verification do not read it.

## Update fixtures and tests that pin the value

A re-recorded fixture now contains a duration instead of `0`, and the value
varies between runs. Take one of these routes:

- Re-record the fixture once, review the new `duration_ms`, and commit the
  result if the fixture is tracked.
- Leave existing fixtures untouched when they still replay as needed; their
  stored `0` remains valid and loads unchanged.
- Replace an assertion that a freshly recorded fixture has `duration_ms == 0`
  with a bound, such as `duration_ms >= 0`, or with an assertion about the
  command's observable behaviour instead of its timing.

Do not compare whole fixture files against golden copies: two recordings of the
same command now differ in `duration_ms`.
