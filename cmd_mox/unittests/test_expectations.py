"""Unit tests for expectation matching and environment injection."""

from __future__ import annotations

import os
import typing as typ

# These tests invoke shim binaries with `shell=False` so the command
# strings are not interpreted by the shell. The paths come from the
# environment manager and are not user controlled, which avoids
# subprocess command injection issues.
import pytest

from cmd_mox import (
    CmdMox,
    ExpectationConfigurationError,
    Regex,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
)
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation, Response
from cmd_mox.unittests._env_helpers import require_shim_dir

pytestmark = pytest.mark.requires_unix_sockets

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc
    import subprocess
    from pathlib import Path


def _with_metadata(expectation: Expectation) -> Expectation:
    """Register an exact metadata argument expectation.

    Returns
    -------
    Expectation
        The configured expectation.
    """
    return expectation.with_args("metadata")


def _with_nextest_run(expectation: Expectation) -> Expectation:
    """Register an exact nextest run argument expectation.

    Returns
    -------
    Expectation
        The configured expectation.
    """
    return expectation.with_args("nextest", "run")


def _with_alphabetic_args(expectation: Expectation) -> Expectation:
    """Register an alphabetic argument matcher expectation.

    Returns
    -------
    Expectation
        The configured expectation.
    """
    return expectation.with_matching_args(str.isalpha)


@pytest.mark.parametrize(
    ("register_existing", "register_attempted", "existing", "attempted"),
    [
        pytest.param(
            _with_metadata,
            _with_nextest_run,
            "args=['metadata']",
            "args=['nextest', 'run']",
            id="exact-then-exact",
        ),
        pytest.param(
            _with_metadata,
            _with_alphabetic_args,
            "args=['metadata']",
            "match_args=",
            id="exact-then-matcher",
        ),
        pytest.param(
            _with_alphabetic_args,
            _with_metadata,
            "match_args=",
            "args=['metadata']",
            id="matcher-then-exact",
        ),
    ],
)
def test_argument_expectation_rejects_reconfiguration(
    register_existing: cabc.Callable[[Expectation], Expectation],
    register_attempted: cabc.Callable[[Expectation], Expectation],
    existing: str,
    attempted: str,
) -> None:
    """Repeated and mixed argument expectations fail without mutation."""
    expectation = Expectation("cargo")
    register_existing(expectation)
    existing_args = expectation.args.copy() if expectation.args is not None else None
    existing_matchers = (
        expectation.match_args.copy() if expectation.match_args is not None else None
    )

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        register_attempted(expectation)

    message = str(exc_info.value)
    assert "Command 'cargo'" in message, "the error should name the command"
    assert existing in message, "the error should show the existing set"
    assert attempted in message, "the error should show the attempted set"
    assert "runs(handler)" in message, "the error should direct users to handlers"
    assert expectation.args == existing_args, "the failed call must preserve exact args"
    assert expectation.match_args == existing_matchers, (
        "the failed call must preserve matchers"
    )


@pytest.mark.parametrize(
    (
        "existing_secret_args",
        "attempted_secret_args",
        "existing_redacted",
        "attempted_redacted",
    ),
    [
        (
            ("--apikey=old-secret",),
            ("--apikey", "new-secret"),
            "--apikey=<redacted>",
            "'--apikey', '<redacted>'",
        ),
        (
            ("--passphrase", "old-secret"),
            ("--passphrase=new-secret",),
            "'--passphrase', '<redacted>'",
            "--passphrase=<redacted>",
        ),
        (
            ("--passphrase=old-secret",),
            ("--passphrase", "new-secret"),
            "--passphrase=<redacted>",
            "'--passphrase', '<redacted>'",
        ),
        (
            ("--header=Authorization: Bearer old-secret",),
            ("--env=API_KEY=new-secret",),
            "'--header=<redacted>'",
            "'--env=<redacted>'",
        ),
        (
            ("--header=X-API-Key: old-secret",),
            ("--header=X-Auth-Token: new-secret",),
            "'--header=<redacted>'",
            "'--header=<redacted>'",
        ),
        (
            ("--header", "X-API-Key: old-secret"),
            ("--header", "X-Auth-Token: new-secret"),
            "'--header', '<redacted>'",
            "'--header', '<redacted>'",
        ),
        (
            ("--token:old-secret",),
            ("--token:new-secret",),
            "--token:<redacted>",
            "--token:<redacted>",
        ),
        (
            ("--token", "--api-key", "old-secret"),
            ("--password", "--api-key", "new-secret"),
            "'--token', '<redacted>', '<redacted>'",
            "'--password', '<redacted>', '<redacted>'",
        ),
    ],
)
def test_duplicate_argument_error_redacts_sensitive_values(
    existing_secret_args: tuple[str, ...],
    attempted_secret_args: tuple[str, ...],
    existing_redacted: str,
    attempted_redacted: str,
) -> None:
    """Configuration errors retain safe arguments but hide secret values."""
    expectation = Expectation("deploy").with_args(
        *existing_secret_args, "--output", "json"
    )

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(*attempted_secret_args, "--output", "yaml")

    message = str(exc_info.value)
    assert "old-secret" not in message, "the existing secret value must be hidden"
    assert "new-secret" not in message, "the attempted secret value must be hidden"
    assert existing_redacted in message, "the existing sensitive option must remain"
    assert attempted_redacted in message, "the attempted sensitive option must remain"
    assert "'--output', 'json'" in message, "the existing safe arguments must remain"
    assert "'--output', 'yaml'" in message, "the attempted safe arguments must remain"


@pytest.mark.parametrize(
    (
        "existing_args",
        "attempted_args",
        "existing_credential",
        "attempted_credential",
        "redacted_argument",
    ),
    [
        pytest.param(
            ("--accessToken=oldopaque123",),
            ("--accessToken=newopaque456",),
            "oldopaque123",
            "newopaque456",
            "--accessToken=<redacted>",
            id="camel-case-access-token",
        ),
        pytest.param(
            ("--clientSecret=oldopaque123",),
            ("--clientSecret=newopaque456",),
            "oldopaque123",
            "newopaque456",
            "--clientSecret=<redacted>",
            id="camel-case-client-secret",
        ),
        pytest.param(
            ("--authtoken=oldopaque123",),
            ("--authtoken=newopaque456",),
            "oldopaque123",
            "newopaque456",
            "--authtoken=<redacted>",
            id="joined-auth-token",
        ),
        pytest.param(
            ("-Dpassword=oldopaque123",),
            ("-Dpassword=newopaque456",),
            "oldopaque123",
            "newopaque456",
            "-Dpassword=<redacted>",
            id="java-property-password",
        ),
        pytest.param(
            ("PGPASSWORD=oldopaque123",),
            ("PGPASSWORD=newopaque456",),
            "oldopaque123",
            "newopaque456",
            "PGPASSWORD=<redacted>",
            id="environment-style-password",
        ),
        pytest.param(
            ("-u", "alice:oldopaque123"),
            ("-u", "bob:newopaque456"),
            "alice:oldopaque123",
            "bob:newopaque456",
            "'-u', '<redacted>'",
            id="curl-short-user-option",
        ),
        pytest.param(
            ("--user", "alice:oldopaque123"),
            ("--user", "bob:newopaque456"),
            "alice:oldopaque123",
            "bob:newopaque456",
            "'--user', '<redacted>'",
            id="curl-long-user-option",
        ),
        pytest.param(
            ("--proxy-user", "alice:oldopaque123"),
            ("--proxy-user", "bob:newopaque456"),
            "alice:oldopaque123",
            "bob:newopaque456",
            "'--proxy-user', '<redacted>'",
            id="curl-proxy-user-option",
        ),
        pytest.param(
            ("--oauth2-bearer", "oldopaque123"),
            ("--oauth2-bearer", "newopaque456"),
            "oldopaque123",
            "newopaque456",
            "'--oauth2-bearer', '<redacted>'",
            id="curl-oauth2-bearer-option",
        ),
        pytest.param(
            ("-b", "oldopaque123"),
            ("-b", "newopaque456"),
            "oldopaque123",
            "newopaque456",
            "'-b', '<redacted>'",
            id="curl-short-cookie-option",
        ),
        pytest.param(
            ("--cookie", "session=oldopaque123"),
            ("--cookie", "session=newopaque456"),
            "session=oldopaque123",
            "session=newopaque456",
            "'--cookie', '<redacted>'",
            id="curl-long-cookie-option",
        ),
        pytest.param(
            ("-ualice:oldopaque123",),
            ("-ubob:newopaque456",),
            "alice:oldopaque123",
            "bob:newopaque456",
            "-u<redacted>",
            id="curl-joined-short-user-option",
        ),
        pytest.param(
            ("-bsession=oldopaque123",),
            ("-bsession=newopaque456",),
            "oldopaque123",
            "newopaque456",
            "-b<redacted>",
            id="curl-joined-short-cookie-option",
        ),
        pytest.param(
            ("-u=alice:oldopaque123",),
            ("-u=bob:newopaque456",),
            "alice:oldopaque123",
            "bob:newopaque456",
            "-u=<redacted>",
            id="curl-short-user-equals",
        ),
        pytest.param(
            ("--user=alice:oldopaque123",),
            ("--user=bob:newopaque456",),
            "alice:oldopaque123",
            "bob:newopaque456",
            "--user=<redacted>",
            id="curl-long-user-equals",
        ),
    ],
)
def test_duplicate_argument_error_redacts_credential_option_values(
    existing_args: tuple[str, ...],
    attempted_args: tuple[str, ...],
    existing_credential: str,
    attempted_credential: str,
    redacted_argument: str,
) -> None:
    """Joined secret names and authentication options hide their values."""
    expectation = Expectation("deploy").with_args(*existing_args)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(*attempted_args)

    message = str(exc_info.value)
    assert existing_credential not in message, "the existing credential must be hidden"
    assert attempted_credential not in message, (
        "the attempted credential must be hidden"
    )
    assert message.count(redacted_argument) == 2, (
        "both credential options must be redacted"
    )


@pytest.mark.parametrize(
    (
        "existing_url",
        "attempted_url",
        "existing_credential",
        "attempted_credential",
        "redacted_url",
    ),
    [
        pytest.param(
            "https://alice:oldopaque123@github.com/org/repo.git",
            "https://bob:newopaque456@github.com/org/repo.git",
            "alice:oldopaque123",
            "bob:newopaque456",
            "https://<redacted>@github.com/org/repo.git",
            id="positional-url",
        ),
        pytest.param(
            "--url=https://alice:oldopaque123@github.com/org/repo.git",
            "--url=https://bob:newopaque456@github.com/org/repo.git",
            "alice:oldopaque123",
            "bob:newopaque456",
            "--url=https://<redacted>@github.com/org/repo.git",
            id="url-option",
        ),
        pytest.param(
            "https://alice:p@ss@github.com/org/repo.git",
            "https://bob:n@ew@github.com/org/repo.git",
            "alice:p@ss",
            "bob:n@ew",
            "https://<redacted>@github.com/org/repo.git",
            id="password-containing-at",
        ),
    ],
)
def test_duplicate_argument_error_redacts_url_userinfo(
    existing_url: str,
    attempted_url: str,
    existing_credential: str,
    attempted_credential: str,
    redacted_url: str,
) -> None:
    """Credential-bearing URLs keep their useful, non-secret components."""
    expectation = Expectation("git").with_args(existing_url)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(attempted_url)

    message = str(exc_info.value)
    assert existing_credential not in message, "the existing credential must be hidden"
    assert attempted_credential not in message, (
        "the attempted credential must be hidden"
    )
    assert message.count(redacted_url) == 2, (
        "both credentials should be hidden while the URL remains readable"
    )


@pytest.mark.parametrize(
    ("existing_header", "attempted_header", "existing_value", "attempted_value"),
    [
        pytest.param(
            "Authorization: Token abc123",
            "Authorization: Digest def456",
            "abc123",
            "def456",
            id="non-standard-schemes",
        ),
        pytest.param(
            "Authorization: AWS4-HMAC-SHA256 abc123",
            "Authorization: barevalue789",
            "abc123",
            "barevalue789",
            id="aws-and-bare-credentials",
        ),
    ],
)
def test_duplicate_argument_error_redacts_authorization_values(
    existing_header: str,
    attempted_header: str,
    existing_value: str,
    attempted_value: str,
) -> None:
    """Authorization values remain hidden for every scheme and bare values."""
    expectation = Expectation("deploy").with_args(f"--header={existing_header}")

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(f"--header={attempted_header}")

    message = str(exc_info.value)
    assert existing_value not in message, "the existing credential must be hidden"
    assert attempted_value not in message, "the attempted credential must be hidden"
    assert message.count("--header=<redacted>") == 2, (
        "both header values must be redacted"
    )


@pytest.mark.parametrize(
    (
        "existing_args",
        "attempted_args",
        "existing_credential",
        "attempted_credential",
        "redacted_argument",
    ),
    [
        pytest.param(
            ("-H", "Cookie: session=oldopaque123"),
            ("-H", "Cookie: session=newopaque456"),
            "oldopaque123",
            "newopaque456",
            "'<redacted>'",
            id="separate-cookie-header",
        ),
        pytest.param(
            ("--header=Cookie: session=oldopaque123",),
            ("--header=Cookie: session=newopaque456",),
            "oldopaque123",
            "newopaque456",
            "--header=<redacted>",
            id="inline-cookie-header",
        ),
    ],
)
def test_duplicate_argument_error_redacts_cookie_header_values(
    existing_args: tuple[str, ...],
    attempted_args: tuple[str, ...],
    existing_credential: str,
    attempted_credential: str,
    redacted_argument: str,
) -> None:
    """Cookie header values are hidden in separate and inline forms."""
    expectation = Expectation("deploy").with_args(*existing_args)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(*attempted_args)

    message = str(exc_info.value)
    assert existing_credential not in message, "the existing credential must be hidden"
    assert attempted_credential not in message, (
        "the attempted credential must be hidden"
    )
    assert message.count(redacted_argument) == 2, "both cookie values must be redacted"


@pytest.mark.parametrize(
    (
        "existing_header",
        "attempted_header",
        "existing_credential",
        "attempted_credential",
        "redacted_header",
    ),
    [
        pytest.param(
            "-HAuthorization: Bearer oldopaque123",
            "-HAuthorization: Bearer newopaque456",
            "oldopaque123",
            "newopaque456",
            "-H<redacted>",
            id="joined-authorization-header",
        ),
        pytest.param(
            "-HCookie: session=oldopaque123",
            "-HCookie: session=newopaque456",
            "oldopaque123",
            "newopaque456",
            "-H<redacted>",
            id="joined-cookie-header",
        ),
        pytest.param(
            "-HX-API-Key: oldopaque123",
            "-HX-API-Key: newopaque456",
            "oldopaque123",
            "newopaque456",
            "-HX-API-Key:<redacted>",
            id="joined-api-key-header",
        ),
    ],
)
def test_duplicate_argument_error_redacts_joined_header_values(
    existing_header: str,
    attempted_header: str,
    existing_credential: str,
    attempted_credential: str,
    redacted_header: str,
) -> None:
    """Joined curl header values remain redacted in conflict errors."""
    expectation = Expectation("curl").with_args(existing_header)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(attempted_header)

    message = str(exc_info.value)
    assert existing_credential not in message, "the existing credential must be hidden"
    assert attempted_credential not in message, (
        "the attempted credential must be hidden"
    )
    assert message.count(redacted_header) == 2, "both header values must be redacted"


@pytest.mark.parametrize(
    (
        "existing_header",
        "attempted_header",
        "existing_credential",
        "attempted_credential",
    ),
    [
        pytest.param(
            "Authorization: Basic dXNlcjpwYXNz==",
            "Authorization: Basic YXR0ZW1wdA==",
            "dXNlcjpwYXNz",
            "YXR0ZW1wdA",
            id="authorization-basic-padding",
        ),
        pytest.param(
            "X-API-Key: b2xkLXNlY3JldA==",
            "X-API-Key: bmV3LXNlY3JldA==",
            "b2xkLXNlY3JldA",
            "bmV3LXNlY3JldA",
            id="api-key-padding",
        ),
    ],
)
def test_duplicate_argument_error_redacts_padded_header_credentials(
    existing_header: str,
    attempted_header: str,
    existing_credential: str,
    attempted_credential: str,
) -> None:
    """Padded standalone header values remain hidden in conflict errors."""
    expectation = Expectation("deploy").with_args("-H", existing_header)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args("-H", attempted_header)

    message = str(exc_info.value)
    assert existing_credential not in message, "the existing credential must be hidden"
    assert attempted_credential not in message, (
        "the attempted credential must be hidden"
    )
    assert message.count("'<redacted>'") == 2, "both headers must be redacted"


@pytest.mark.parametrize(
    ("existing_argument", "attempted_argument", "redacted_argument"),
    [
        pytest.param(
            "Authorization=Bearer oldopaque123",
            "Authorization=Bearer newopaque456",
            "Authorization=<redacted>",
            id="authorization-equals",
        ),
        pytest.param(
            "--authorization=Bearer oldopaque123",
            "--authorization=Bearer newopaque456",
            "--authorization=<redacted>",
            id="option-authorization-equals",
        ),
    ],
)
def test_duplicate_argument_error_redacts_equals_form_authorization_values(
    existing_argument: str,
    attempted_argument: str,
    redacted_argument: str,
) -> None:
    """Authorization credentials remain hidden in equals-form arguments."""
    expectation = Expectation("deploy").with_args(existing_argument)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(attempted_argument)

    message = str(exc_info.value)
    assert "oldopaque123" not in message, "the existing credential must be hidden"
    assert "newopaque456" not in message, "the attempted credential must be hidden"
    assert message.count(redacted_argument) == 2, (
        "both Authorization arguments must be redacted"
    )


def test_duplicate_argument_error_preserves_key_like_positionals() -> None:
    """Non-assignment arguments with key-like segments remain diagnosable."""
    expectation = Expectation("deploy").with_args("my-key-file", "ssh-key")

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args("replacement-file")

    message = str(exc_info.value)
    assert "'my-key-file'" in message, "safe key-like positionals must remain visible"
    assert "'ssh-key'" in message, "safe key-like positionals must remain visible"


def test_with_matching_args_rejects_a_second_argument_expectation() -> None:
    """A second matcher set must fail at registration."""

    class SensitiveMatcher:
        """Matcher whose representation contains a retained value."""

        def __init__(self, value: str) -> None:
            self.value = value

        def __call__(self, argument: str) -> bool:
            return argument == self.value

        def __repr__(self) -> str:
            return f"SensitiveMatcher({self.value!r})"

    existing_matcher = SensitiveMatcher("existing-matcher-secret")
    attempted_matcher = SensitiveMatcher("attempted-matcher-secret")
    expectation = Expectation("cargo").with_matching_args(existing_matcher)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_matching_args(attempted_matcher)

    message = str(exc_info.value)
    assert "Command 'cargo'" in message, "the error should name the command"
    assert "match_args=" in message, "the error should show matcher sets"
    assert message.count("match_args=") == 2, "both matcher sets should be shown"
    assert message.count("'SensitiveMatcher'") == 2, (
        "safe matcher types should be shown"
    )
    assert "existing-matcher-secret" not in message, (
        "existing matcher data must be hidden"
    )
    assert "attempted-matcher-secret" not in message, (
        "attempted matcher data must be hidden"
    )
    assert "runs(handler)" in message, "the error should direct users to handlers"
    assert expectation.match_args == [existing_matcher], "the prior matcher must remain"
    assert expectation.args is None, "the failed call should not add exact arguments"


def test_mock_with_args_and_order(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mocks require specific arguments and call order."""
    mox = CmdMox()
    mox.mock("first").with_args("a").returns(stdout="1").in_order().times(1)
    mox.mock("second").with_args("b").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    path_first = require_shim_dir(mox.environment) / "first"
    path_second = require_shim_dir(mox.environment) / "second"
    run([str(path_first), "a"], shell=False)
    run([str(path_second), "b"], shell=False)

    mox.verify()

    assert len(mox.mocks["first"].invocations) == 1
    assert len(mox.mocks["second"].invocations) == 1


def test_mock_argument_mismatch(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Verification fails when arguments differ from expectation."""
    mox = CmdMox()
    mox.mock("foo").with_args("bar")
    mox.__enter__()
    mox.replay()

    path = require_shim_dir(mox.environment) / "foo"
    run([str(path), "baz"], shell=False)

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def test_with_matching_args_and_stdin(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Regular expressions and stdin matching are supported."""
    mox = CmdMox()
    mox.mock("grep").with_matching_args(Regex(r"foo=\d+")).with_stdin("data")
    mox.__enter__()
    mox.replay()

    path = require_shim_dir(mox.environment) / "grep"
    run(
        [str(path), "foo=123"],
        input="data",
        shell=False,
    )

    mox.verify()


def test_with_env_injection(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Environment variables provided via with_env() are applied."""
    mox = CmdMox()

    def handler(inv: Invocation) -> Response:
        return Response(stdout=os.environ.get("HELLO", ""))

    mox.stub("env").with_env({"HELLO": "WORLD"}).runs(handler)
    mox.__enter__()
    mox.replay()

    path = require_shim_dir(mox.environment) / "env"
    result = run([str(path)], shell=False)
    mox.verify()

    assert result.stdout.strip() == "WORLD"


@pytest.mark.parametrize(
    ("payload", "exc_type", "match"),
    [
        pytest.param(
            {42: "value"},
            TypeError,
            "name must be str",
            id="non-string-key",
        ),
        pytest.param(
            {"": "value"},
            ValueError,
            "cannot be empty",
            id="empty-key",
        ),
        pytest.param(
            {"VAR": 7},
            TypeError,
            "value must be str",
            id="non-string-value",
        ),
    ],
)
def test_with_env_rejects_invalid_entries(
    payload: dict[typ.Any, typ.Any],
    exc_type: type[Exception],
    match: str,
) -> None:
    """with_env() should reject malformed environment entries.

    The parametrized payloads deliberately violate the ``dict[str, str]``
    contract to exercise the defensive validation inside ``with_env()``.
    """
    expectation = Expectation("cmd")
    with pytest.raises(exc_type, match=match):
        expectation.with_env(payload)


def test_any_order_expectations_allow_flexible_sequence(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Expectations marked any_order() should not enforce call ordering."""
    mox = CmdMox()
    mox.mock("first").returns(stdout="1").in_order()
    mox.mock("second").returns(stdout="2").any_order()
    mox.__enter__()
    mox.replay()

    path_first = require_shim_dir(mox.environment) / "first"
    path_second = require_shim_dir(mox.environment) / "second"

    # Invoke the any_order expectation before the ordered one
    run([str(path_second)], shell=False)
    run([str(path_first)], shell=False)

    mox.verify()


def test_multiple_any_order_expectations_do_not_enforce_order(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Unordered expectations remain unordered when combined."""
    with CmdMox() as mox:
        mox.mock("first").returns(stdout="1").any_order()
        mox.mock("second").returns(stdout="2").any_order()
        mox.mock("third").returns(stdout="3").any_order()
        mox.replay()

        path_first = require_shim_dir(mox.environment) / "first"
        path_second = require_shim_dir(mox.environment) / "second"
        path_third = require_shim_dir(mox.environment) / "third"

        # Call expectations in a different order than defined
        run([str(path_third)], shell=False)
        run([str(path_first)], shell=False)
        run([str(path_second)], shell=False)

        mox.verify()

        assert len(mox.mocks["first"].invocations) == 1
        assert len(mox.mocks["second"].invocations) == 1
        assert len(mox.mocks["third"].invocations) == 1


def _test_expectation_failure_helper(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
    mock_configurator: cabc.Callable[[CmdMox], None],
    execution_strategy: cabc.Callable[
        [cabc.Callable[..., subprocess.CompletedProcess[str]], dict[str, Path]], None
    ],
    expected_exception: type[Exception] = UnfulfilledExpectationError,
) -> None:
    """Execute a scenario expected to fail."""
    mox = CmdMox()
    mock_configurator(mox)
    mox.__enter__()
    mox.replay()

    paths = {name: require_shim_dir(mox.environment) / name for name in mox.mocks}

    execution_strategy(run, paths)

    with pytest.raises(expected_exception):
        mox.verify()


def test_expectation_times_alias(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Expectation.times() and times_called() behave interchangeably."""
    exp = Expectation("foo").times(3)
    assert exp.count == 3

    mox = CmdMox()
    mox.mock("first").returns(stdout="1").times(2)
    mox.mock("second").returns(stdout="2").times_called(2)
    mox.__enter__()
    mox.replay()

    path_first = require_shim_dir(mox.environment) / "first"
    path_second = require_shim_dir(mox.environment) / "second"

    run([str(path_first)], shell=False)
    run([str(path_first)], shell=False)
    run([str(path_second)], shell=False)
    run([str(path_second)], shell=False)

    mox.verify()

    assert len(mox.mocks["first"].invocations) == 2
    assert len(mox.mocks["second"].invocations) == 2


@pytest.mark.parametrize(
    (
        "mock_configurator",
        "execution_strategy",
        "expected_exception",
    ),
    [
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").in_order(),
                mox.mock("second").returns(stdout="2").in_order(),
            ),
            lambda run, paths: (
                run([str(paths["second"])], shell=False),
                run([str(paths["first"])], shell=False),
            ),
            UnexpectedCommandError,
            id="order-validation",
        ),
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").times(2),
                mox.mock("second").returns(stdout="2").times_called(2),
            ),
            lambda run, paths: (
                run([str(paths["first"])], shell=False),
                run([str(paths["second"])], shell=False),
            ),
            UnfulfilledExpectationError,
            id="count-validation",
        ),
        pytest.param(
            lambda mox: (mox.mock("first").returns(stdout="1").any_order().times(2),),
            lambda run, paths: (run([str(paths["first"])], shell=False),),
            UnfulfilledExpectationError,
            id="any_order_call_count_fail",
        ),
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").any_order().times_called(2),
            ),
            lambda run, paths: (run([str(paths["first"])], shell=False),),
            UnfulfilledExpectationError,
            id="any_order_call_count_fail_times_called",
        ),
        pytest.param(
            lambda mox: (mox.mock("first").returns(stdout="1").any_order().times(1),),
            lambda run, paths: (
                run([str(paths["first"])], shell=False),
                run([str(paths["first"])], shell=False),
            ),
            UnexpectedCommandError,
            id="any_order_call_count_excess_times",
        ),
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").any_order().times_called(1),
            ),
            lambda run, paths: (
                run([str(paths["first"])], shell=False),
                run([str(paths["first"])], shell=False),
            ),
            UnexpectedCommandError,
            id="any_order_call_count_excess_times_called",
        ),
    ],
)
def test_expectation_failures(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
    mock_configurator: cabc.Callable[[CmdMox], None],
    execution_strategy: cabc.Callable[
        [cabc.Callable[..., subprocess.CompletedProcess[str]], dict[str, Path]], None
    ],
    expected_exception: type[Exception],
) -> None:
    """Verify expectation scenarios that should fail verification."""
    _test_expectation_failure_helper(
        run, mock_configurator, execution_strategy, expected_exception
    )


def _assert_mismatch_reason(exp: Expectation, inv: Invocation, fragment: str) -> None:
    """Assert *exp* rejects *inv* and explains the mismatch.

    Parameters
    ----------
    exp : Expectation
        Expectation under test.
    inv : Invocation
        Invocation that must not match *exp*.
    fragment : str
        Substring expected in the mismatch explanation.
    """
    assert exp.matches(inv) is False
    reason = exp.explain_mismatch(inv)
    assert reason is not None
    assert fragment in reason, f"{fragment!r} missing from {reason!r}"


class TestStdinMatching:
    """Tests for Expectation stdin matching and mismatch explanation."""

    def test_literal_string_stdin_matches(self) -> None:
        """A literal string stdin matches when invocation.stdin is equal."""
        exp = Expectation("cmd").with_stdin("hello")
        inv = Invocation(command="cmd", args=[], stdin="hello", env={})
        assert exp.matches(inv) is True
        assert exp.explain_mismatch(inv) == "args, stdin, or env mismatch"

    def test_literal_string_stdin_mismatch(self) -> None:
        """A literal string stdin does not match a different value."""
        exp = Expectation("cmd").with_stdin("hello")
        inv = Invocation(command="cmd", args=[], stdin="world", env={})
        _assert_mismatch_reason(exp, inv, "world")

    def test_callable_stdin_matches(self) -> None:
        """A callable stdin predicate is invoked with invocation.stdin."""
        exp = Expectation("cmd").with_stdin(lambda s: s.startswith("ok"))
        inv = Invocation(command="cmd", args=[], stdin="ok-data", env={})
        assert exp.matches(inv) is True

    def test_callable_stdin_mismatch(self) -> None:
        """A callable stdin predicate that returns False causes mismatch."""
        exp = Expectation("cmd").with_stdin(lambda s: s == "expected")
        inv = Invocation(command="cmd", args=[], stdin="actual", env={})
        _assert_mismatch_reason(exp, inv, "actual")

    def test_callable_stdin_exception_returns_false(self) -> None:
        """A callable stdin predicate that raises is treated as non-match."""
        exp = Expectation("cmd").with_stdin(lambda s: 1 / 0)
        inv = Invocation(command="cmd", args=[], stdin="data", env={})
        _assert_mismatch_reason(exp, inv, "raised")

    def test_non_string_non_callable_stdin_does_not_match(self) -> None:
        """A non-string, non-callable stdin value is rejected.

        This test intentionally sets stdin to an invalid type via direct
        attribute mutation because the public with_stdin() API enforces
        type constraints.  It exercises the defensive fallback branch.
        """
        exp = Expectation("cmd")
        exp.stdin = 42  # type: ignore[assignment, ty:invalid-assignment]
        inv = Invocation(command="cmd", args=[], stdin="hello", env={})
        _assert_mismatch_reason(exp, inv, "not str or callable")

    def test_none_stdin_always_matches(self) -> None:
        """When stdin expectation is None, any stdin value matches."""
        exp = Expectation("cmd")
        inv = Invocation(command="cmd", args=[], stdin="anything", env={})
        assert exp.matches(inv) is True


def test_validate_matchers_returns_false_when_missing() -> None:
    """_validate_matchers fails safely when matchers list is absent."""
    expectation = Expectation("cmd")
    assert not expectation._validate_matchers(["arg"])


def test_validate_matchers_rejects_length_mismatch() -> None:
    """_validate_matchers enforces one-to-one matcher/argument counts."""
    expectation = Expectation("cmd").with_matching_args(lambda _: True)
    assert not expectation._validate_matchers(["one", "two"])
