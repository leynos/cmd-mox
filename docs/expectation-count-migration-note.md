# Expectation count migration note

This note helps projects prepare for the next minor release, which validates
the count passed to `times()` and `times_called()` and enforces explicit counts
on spies. It does not assume a release number. For the complete behaviour, see
the [fluent API reference](usage-guide.md#fluent-api-reference).

## Replace invalid counts

`times(count)` and `times_called(count)` now require a non-negative `int`.
Earlier releases stored the value unchecked, so an invalid count surfaced later
as an obscure failure: a negative count raised `IndexError` during
verification, and a float raised `TypeError` when ordered expectations were
expanded. The new release rejects the value when the expectation is configured:

- `True` and `False` raise `TypeError`, even though `bool` subclasses `int`.
- Floats, strings, and `None` raise `TypeError`.
- Negative integers raise `ValueError`.

Replace each invalid count with the number of calls the test actually expects.
Use `0` when the command must not run at all. A rejected count leaves the
expectation's previous count unchanged, so a failed configuration call does not
silently alter an expectation that was already set.

## Update explicitly counted spies

A spy is permissive by default: without an explicit count it accepts any number
of calls. Earlier releases did not enforce a count set on a spy during
verification, so a test could call `.times(2)` on a spy and still pass after
one or three calls. The new release enforces the count for every spy that sets
one, including `times(0)`.

Audit tests that call `times()` or `times_called()` on a spy:

- If the test intends to require an exact call count, keep the count and make
  the exercised code produce that many calls.
- If the test only inspects the spy's recorded invocations, remove the count so
  the spy stays permissive, or replace it with the `assert_called()` helpers.
- If the test relied on a zero count being ignored, expect verification to fail
  whenever the spy runs.

Stubs remain intentionally non-strict and ignore counts. Count violations,
including a zero-count command that ran, are reported by `verify()` rather than
at the moment of invocation, matching every other count mismatch.
