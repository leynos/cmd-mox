# Argument matcher migration note

This note helps projects prepare for the next minor release, which makes
`with_matching_args()` treat a plain string as an exact-equality literal and
reject other non-callable values when the expectation is configured. It does
not assume a release number. For the complete behaviour and matching rules, see
[Argument matchers (comparators)](usage-guide.md#argument-matchers-comparators).

## Use literal strings for exact arguments

A string in a matcher position now requires exact equality with the
corresponding argument. Previously it was accepted without complaint and then
raised `TypeError` while matching, so the expectation could never match. Use
the literal itself when the argument must be exact:

```python
# Before: an exact literal hidden inside a comparator
cmd_mox.mock("git").with_matching_args(Regex(r"^clone$"), Any())

# After: the literal expresses exact equality directly
cmd_mox.mock("git").with_matching_args("clone", Any())
```

## Replace invalid matchers with strings or predicates

`with_matching_args()` now raises `ExpectationConfigurationError` during
configuration when a position receives a value that is neither a string nor
callable. The message names the command, the zero-based position, and the
value's type. Replace an invalid value with:

- the string itself when the argument must match exactly;
- a comparator such as `IsA`, `Regex`, `Predicate`, or any callable
  `Callable[[str], bool]` when the check is dynamic;
- `with_args(*args)` when every argument must match exactly.

```python
# Before: an int can never be called during matching
cmd_mox.mock("tool").with_matching_args(42)

# After: match an integer-shaped argument with a predicate
cmd_mox.mock("tool").with_matching_args(IsA(int))
```

## Check existing expectations

Search for `with_matching_args(` call sites that pass non-string, non-callable
values, and for literals that were wrapped in a comparator only to obtain exact
matching. Configuration errors are raised when the expectation is built, so a
test run reports the affected command and position without waiting for replay
verification.
