"""Match command arguments and describe fail-fast mismatches.

Fail-fast rejection must decide from literal values alone and must redact every
value it renders. Keeping those rules here leaves the expectation model with
configuration and matching, and the verifiers with verification policy.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

from ._argument_redaction import (
    REDACTED_ARGUMENT,
    mask_sensitive_positions,
    redact_sensitive_argument_values,
)

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc

    from .expectations import ArgMatcher, Expectation
    from .ipc import Invocation


@dc.dataclass(frozen=True, slots=True)
class MismatchContext:
    """Describe the policy and sibling context for a mismatch report.

    Attributes
    ----------
    call_predicates : bool
        Whether non-literal matchers may be invoked to describe a failure.
        Fail-fast dispatch keeps this ``False`` so an early rejection never
        runs caller-supplied code.
    mask_sources : collections.abc.Sequence[collections.abc.Sequence[str]] or None
        Additional argument rows whose sensitive positions must be masked in
        the rendered reason, such as sibling ordered calls and expectations.
    """

    call_predicates: bool = True
    mask_sources: cabc.Sequence[cabc.Sequence[str]] | None = None


def matcher_accepts(matcher: ArgMatcher, value: str) -> bool:
    """Return whether *matcher* accepts *value*.

    Returns
    -------
    bool
        ``True`` when a literal string equals *value*, or when a callable
        returns a truthy result.
    """
    if isinstance(matcher, str):
        return matcher == value
    return bool(matcher(value))


def matcher_mismatch(
    exp: Expectation,
    invocation: Invocation,
    matchers: cabc.Sequence[ArgMatcher],
    *,
    context: MismatchContext,
) -> str | None:
    """Return the first arity or per-position matcher failure.

    Positions are examined in argument order so the reported failure is
    genuinely the first one, not merely the first literal matcher.
    ``context.call_predicates`` keeps fail-fast dispatch callback-free: an
    early rejection may not run caller-supplied code, so predicates are
    skipped there and left for verification. ``context.mask_sources`` lets a
    verification path mask values a sibling call or expectation marks as
    sensitive.

    Parameters
    ----------
    exp : Expectation
        Expectation whose command name masks the rendered arguments.
    invocation : Invocation
        Call being matched.
    matchers : collections.abc.Sequence[object]
        Expected matchers, aligned with the invocation arguments.
    context : MismatchContext
        Description policy and any sibling masking sources.

    Returns
    -------
    str or None
        An arity, ``arg[i]=... != ...``, predicate-failure, or validator-error
        description, or ``None`` when every checked matcher accepts its
        argument.
    """
    if len(invocation.args) != len(matchers):
        return f"expected {len(matchers)} args but got {len(invocation.args)}"
    actual, expected = _redacted_matcher_arguments(
        exp, invocation, matchers, context.mask_sources
    )
    for index, (arg, matcher) in enumerate(zip(invocation.args, matchers, strict=True)):
        if isinstance(matcher, str):
            # Literal equality cannot raise, so report it without the
            # exception wrapper used for caller-supplied predicates.
            if not matcher_accepts(matcher, arg):
                return f"arg[{index}]={actual[index]!r} != {expected[index]!r}"
            continue
        if not context.call_predicates:
            continue
        detail = _describe_predicate_failure(matcher, arg, expected=expected[index])
        if detail is not None:
            return f"arg[{index}]={actual[index]!r} {detail}"
    return None


def _describe_predicate_failure(
    matcher: cabc.Callable[[str], bool], arg: str, *, expected: str
) -> str | None:
    """Return the failure detail when *matcher* rejects *arg*.

    The caller supplies the argument index and the redacted argument, so this
    helper reports only what happened at one position.

    Returns
    -------
    str or None
        A ``predicate raised ...`` or ``failed ...`` detail, or ``None`` when
        *matcher* accepts *arg*.
    """
    try:
        ok = matcher_accepts(matcher, arg)
    except Exception as exc:  # ruff: ignore[blind-except] - this boundary intentionally converts arbitrary callback failures
        if expected == REDACTED_ARGUMENT:
            # The message may quote the value and the matcher repr may embed
            # its pattern, so a sensitive position keeps only the class name.
            return f"predicate raised {exc.__class__.__name__}"
        return f"predicate {matcher!r} raised {exc.__class__.__name__}: {exc}"
    if ok:
        return None
    # A matcher repr can carry the pattern it matches, so a sensitive position
    # is reported with the placeholder only.
    rendered = REDACTED_ARGUMENT if expected == REDACTED_ARGUMENT else repr(matcher)
    return f"failed {rendered}"


def _redacted_matcher_arguments(
    exp: Expectation,
    invocation: Invocation,
    matchers: cabc.Sequence[object],
    mask_sources: cabc.Sequence[cabc.Sequence[str]] | None,
) -> tuple[list[str], list[str]]:
    """Return redacted invocation arguments and literal matcher values.

    Predicate matchers have no literal value, so they are represented by an
    empty placeholder that keeps positions aligned with the invocation.

    Returns
    -------
    tuple[list[str], list[str]]
        Redacted actual arguments and redacted literal matchers.
    """
    actual = exp.redacted_arguments(invocation.args)
    literals = [matcher if isinstance(matcher, str) else "" for matcher in matchers]
    expected = redact_sensitive_argument_values(literals, command=exp.name)
    # An option name may itself be a predicate matcher, so the matcher list
    # cannot reveal which positions carry a credential.
    expected = mask_sensitive_positions(expected, invocation.args, command=exp.name)
    return _mask_shared_positions(
        actual, expected, command=exp.name, mask_sources=mask_sources
    )


def _mask_shared_positions(
    actual: list[str],
    expected: list[str],
    *,
    command: str,
    mask_sources: cabc.Sequence[cabc.Sequence[str]] | None,
) -> tuple[list[str], list[str]]:
    """Mask both rows at every position the sibling sources mark sensitive.

    A sibling call or expectation can name the option that marks a value as a
    credential even when neither row under description does.

    Returns
    -------
    tuple[list[str], list[str]]
        Diagnostic-safe actual and expected arguments.
    """
    for source in mask_sources or ():
        actual = mask_sensitive_positions(actual, list(source), command=command)
        expected = mask_sensitive_positions(expected, list(source), command=command)
    return actual, expected


def redacted_literal_argument_mismatch(
    exp: Expectation,
    invocation: Invocation,
    expected: list[str],
    mask_sources: cabc.Sequence[cabc.Sequence[str]] | None = None,
) -> str:
    """Return a redacted description of unequal literal arguments.

    Returns
    -------
    str
        Both argument lists with sensitive values masked.
    """
    actual = exp.redacted_arguments(invocation.args)
    safe_expected = redact_sensitive_argument_values(expected, command=exp.name)
    # The option names may differ, so the expected list alone cannot show
    # which of its values the call treats as a credential.
    safe_expected = mask_sensitive_positions(
        safe_expected, invocation.args, command=exp.name
    )
    actual, safe_expected = _mask_shared_positions(
        actual, safe_expected, command=exp.name, mask_sources=mask_sources
    )
    return f"arguments {actual!r} != {safe_expected!r}"
