"""Unit tests for sensitive command-argument redaction."""

from __future__ import annotations

import dataclasses as dc

import pytest

from cmd_mox import ExpectationConfigurationError
from cmd_mox._argument_redaction import redact_sensitive_argument_values
from cmd_mox.expectations import Expectation


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            ("--token", "opaque", "--output", "json"),
            ("--token", "<redacted>", "--output", "json"),
        ),
        (("--token=opaque",), ("--token=<redacted>",)),
        (("-u", "alice:opaque"), ("-u", "<redacted>")),
        (("-ualice:opaque",), ("-u<redacted>",)),
        (("--header", "Authorization: Bearer opaque"), ("--header", "<redacted>")),
        (("-HCookie: session=opaque",), ("-H<redacted>",)),
        (
            ("https://alice:opaque@example.test/path",),
            ("https://<redacted>@example.test/path",),
        ),
        (("PGPASSWORD=opaque",), ("PGPASSWORD=<redacted>",)),
        (
            ("--output", "json", "ordinary positional"),
            ("--output", "json", "ordinary positional"),
        ),
        (("--user-agent", "Firefox"), ("--user-agent", "Firefox")),
    ],
    ids=(
        "detached-option",
        "inline-option",
        "detached-short-option",
        "joined-short-option",
        "authorization-header",
        "joined-cookie-header",
        "url-userinfo",
        "embedded-assignment",
        "safe-near-miss",
        "safe-option-near-miss",
    ),
)
def test_redact_sensitive_argument_values(
    args: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    """Sensitive branches hide values while safe arguments remain unchanged."""
    assert redact_sensitive_argument_values(list(args)) == list(expected), (
        "redaction must hide sensitive values and keep safe arguments unchanged"
    )


@pytest.mark.parametrize(
    ("command", "args"),
    [
        pytest.param("git", ("push", "-u", "origin", "main"), id="git-push-upstream"),
        pytest.param(
            r"C:\Program Files\Git\bin\git.exe",
            ("push", "-u", "origin", "main"),
            id="windows-git-executable",
        ),
        pytest.param("git", ("checkout", "-b", "feature"), id="git-checkout-branch"),
        pytest.param("git", ("diff", "-U", "3"), id="git-diff-context"),
        pytest.param("curl", ("-B", "upload.txt"), id="curl-use-ascii"),
        pytest.param("go", ("test", "-bench=."), id="go-test-benchmark"),
    ],
)
def test_expectation_conflict_retains_known_non_secret_short_options(
    command: str, args: tuple[str, ...]
) -> None:
    """Known non-secret short options remain visible in conflict messages."""
    expectation = Expectation(command).with_args(*args)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args("replacement")

    assert f"args={list(args)!r}" in str(exc_info.value), (
        "conflict diagnostics must preserve the known command arguments"
    )


@pytest.mark.parametrize(
    ("args", "redacted_args"),
    [
        (("-U", "alice:opaque"), "'-U', '<redacted>'"),
        (("-Ualice:opaque",), "'-U<redacted>'"),
    ],
    ids=("separate-proxy-user", "joined-proxy-user"),
)
def test_curl_proxy_user_short_option_is_redacted(
    args: tuple[str, ...], redacted_args: str
) -> None:
    """Curl's uppercase proxy-user option remains credential-sensitive."""
    expectation = Expectation("curl").with_args(*args)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args("replacement")

    message = str(exc_info.value)
    assert "opaque" not in message, "proxy credentials must be hidden"
    assert redacted_args in message, "the proxy-user option must remain visible"


@pytest.mark.parametrize(
    ("credential_url", "secret_values", "visible_values"),
    [
        (
            "https://alice:urlsecret@example.test/path?token=querysecret",
            ("alice", "urlsecret", "querysecret"),
            (),
        ),
        (
            "--url=https://alice:urlsecret@example.test/path?access_token=querysecret",
            ("alice", "urlsecret", "querysecret"),
            (),
        ),
        (
            "https://api.test/x?page=1&token=querysecret",
            ("querysecret",),
            ("page=1",),
        ),
        (
            "--url=https://api.test/x?page=1&apikey=querysecret",
            ("querysecret",),
            ("page=1",),
        ),
        (
            "https://api.test/x?page=1&password=querysecret",
            ("querysecret",),
            ("page=1",),
        ),
    ],
    ids=(
        "positional-url-userinfo",
        "inline-url-userinfo",
        "positional-url-token-after-query",
        "inline-url-apikey-after-query",
        "positional-url-password-after-query",
    ),
)
def test_duplicate_argument_error_redacts_combined_url_credentials(
    credential_url: str,
    secret_values: tuple[str, ...],
    visible_values: tuple[str, ...],
) -> None:
    """URL credentials are hidden while ordinary query parameters remain visible."""
    expectation = Expectation("deploy").with_args(credential_url)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args("replacement")

    message = str(exc_info.value)
    for secret in secret_values:
        assert secret not in message, f"URL credential {secret!r} must be hidden"
    for visible in visible_values:
        assert visible in message, f"ordinary query value {visible!r} should remain"


@dc.dataclass(frozen=True, slots=True)
class _DuplicateArgumentRedactionCase:
    """Inputs and expected diagnostic fragment for a redaction example."""

    identifier: str
    existing_args: tuple[str, ...]
    attempted_args: tuple[str, ...]
    credentials: tuple[str, str]
    redacted_fragment: str


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
    "case",
    [
        _DuplicateArgumentRedactionCase(
            "camel-case-access-token",
            ("--accessToken=oldopaque123",),
            ("--accessToken=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "--accessToken=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "camel-case-client-secret",
            ("--clientSecret=oldopaque123",),
            ("--clientSecret=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "--clientSecret=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "joined-auth-token",
            ("--authtoken=oldopaque123",),
            ("--authtoken=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "--authtoken=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "java-property-password",
            ("-Dpassword=oldopaque123",),
            ("-Dpassword=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "-Dpassword=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "environment-style-password",
            ("PGPASSWORD=oldopaque123",),
            ("PGPASSWORD=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "PGPASSWORD=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-short-user-option",
            ("-u", "alice:oldopaque123"),
            ("-u", "bob:newopaque456"),
            ("alice:oldopaque123", "bob:newopaque456"),
            "'-u', '<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-long-user-option",
            ("--user", "alice:oldopaque123"),
            ("--user", "bob:newopaque456"),
            ("alice:oldopaque123", "bob:newopaque456"),
            "'--user', '<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-proxy-user-option",
            ("--proxy-user", "alice:oldopaque123"),
            ("--proxy-user", "bob:newopaque456"),
            ("alice:oldopaque123", "bob:newopaque456"),
            "'--proxy-user', '<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-oauth2-bearer-option",
            ("--oauth2-bearer", "oldopaque123"),
            ("--oauth2-bearer", "newopaque456"),
            ("oldopaque123", "newopaque456"),
            "'--oauth2-bearer', '<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-short-cookie-option",
            ("-b", "oldopaque123"),
            ("-b", "newopaque456"),
            ("oldopaque123", "newopaque456"),
            "'-b', '<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-long-cookie-option",
            ("--cookie", "session=oldopaque123"),
            ("--cookie", "session=newopaque456"),
            ("session=oldopaque123", "session=newopaque456"),
            "'--cookie', '<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-joined-short-user-option",
            ("-ualice:oldopaque123",),
            ("-ubob:newopaque456",),
            ("alice:oldopaque123", "bob:newopaque456"),
            "-u<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-joined-short-cookie-option",
            ("-bsession=oldopaque123",),
            ("-bsession=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "-b<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-short-user-equals",
            ("-u=alice:oldopaque123",),
            ("-u=bob:newopaque456",),
            ("alice:oldopaque123", "bob:newopaque456"),
            "-u=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "curl-long-user-equals",
            ("--user=alice:oldopaque123",),
            ("--user=bob:newopaque456",),
            ("alice:oldopaque123", "bob:newopaque456"),
            "--user=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "positional-url",
            ("https://alice:oldopaque123@github.com/org/repo.git",),
            ("https://bob:newopaque456@github.com/org/repo.git",),
            ("alice:oldopaque123", "bob:newopaque456"),
            "https://<redacted>@github.com/org/repo.git",
        ),
        _DuplicateArgumentRedactionCase(
            "url-option",
            ("--url=https://alice:oldopaque123@github.com/org/repo.git",),
            ("--url=https://bob:newopaque456@github.com/org/repo.git",),
            ("alice:oldopaque123", "bob:newopaque456"),
            "--url=https://<redacted>@github.com/org/repo.git",
        ),
        _DuplicateArgumentRedactionCase(
            "password-containing-at",
            ("https://alice:p@ss@github.com/org/repo.git",),
            ("https://bob:n@ew@github.com/org/repo.git",),
            ("alice:p@ss", "bob:n@ew"),
            "https://<redacted>@github.com/org/repo.git",
        ),
        _DuplicateArgumentRedactionCase(
            "non-standard-authorization-schemes",
            ("--header=Authorization: Token abc123",),
            ("--header=Authorization: Digest def456",),
            ("abc123", "def456"),
            "--header=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "aws-and-bare-authorization-credentials",
            ("--header=Authorization: AWS4-HMAC-SHA256 abc123",),
            ("--header=Authorization: barevalue789",),
            ("abc123", "barevalue789"),
            "--header=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "separate-cookie-header",
            ("-H", "Cookie: session=oldopaque123"),
            ("-H", "Cookie: session=newopaque456"),
            ("oldopaque123", "newopaque456"),
            "'<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "inline-cookie-header",
            ("--header=Cookie: session=oldopaque123",),
            ("--header=Cookie: session=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "--header=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "joined-authorization-header",
            ("-HAuthorization: Bearer oldopaque123",),
            ("-HAuthorization: Bearer newopaque456",),
            ("oldopaque123", "newopaque456"),
            "-H<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "joined-cookie-header",
            ("-HCookie: session=oldopaque123",),
            ("-HCookie: session=newopaque456",),
            ("oldopaque123", "newopaque456"),
            "-H<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "joined-api-key-header",
            ("-HX-API-Key: oldopaque123",),
            ("-HX-API-Key: newopaque456",),
            ("oldopaque123", "newopaque456"),
            "-HX-API-Key:<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "joined-api-key-header-padding",
            ("-HX-API-Key: b2xkLXNlY3JldA==",),
            ("-HX-API-Key: bmV3LXNlY3JldA==",),
            ("b2xkLXNlY3JldA", "bmV3LXNlY3JldA"),
            "-HX-API-Key:<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "colon-option-value-with-equals",
            ("--api-key:oldopaque123=ignored",),
            ("--api-key:newopaque456=ignored",),
            ("oldopaque123", "newopaque456"),
            "--api-key:<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "authorization-basic-padding",
            ("-H", "Authorization: Basic dXNlcjpwYXNz=="),
            ("-H", "Authorization: Basic YXR0ZW1wdA=="),
            ("dXNlcjpwYXNz", "YXR0ZW1wdA"),
            "'<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "api-key-padding",
            ("-H", "X-API-Key: b2xkLXNlY3JldA=="),
            ("-H", "X-API-Key: bmV3LXNlY3JldA=="),
            ("b2xkLXNlY3JldA", "bmV3LXNlY3JldA"),
            "'<redacted>'",
        ),
        _DuplicateArgumentRedactionCase(
            "authorization-equals",
            ("Authorization=Bearer oldopaque123",),
            ("Authorization=Bearer newopaque456",),
            ("oldopaque123", "newopaque456"),
            "Authorization=<redacted>",
        ),
        _DuplicateArgumentRedactionCase(
            "option-authorization-equals",
            ("--authorization=Bearer oldopaque123",),
            ("--authorization=Bearer newopaque456",),
            ("oldopaque123", "newopaque456"),
            "--authorization=<redacted>",
        ),
    ],
    ids=lambda case: case.identifier,
)
def test_duplicate_argument_error_redacts_credential_arguments(
    case: _DuplicateArgumentRedactionCase,
) -> None:
    """Credential-bearing arguments are redacted from conflict diagnostics."""
    for args, credential in zip(
        (case.existing_args, case.attempted_args), case.credentials, strict=True
    ):
        redacted_args = repr(redact_sensitive_argument_values(list(args)))
        assert credential not in redacted_args, "credential values must be hidden"
        assert case.redacted_fragment in redacted_args, (
            "the sensitive argument should retain its redacted representation"
        )

    expectation = Expectation("deploy").with_args(*case.existing_args)

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args(*case.attempted_args)

    message = str(exc_info.value)
    for credential in case.credentials:
        assert credential not in message, "credential values must be hidden"
    assert message.count(case.redacted_fragment) == 2, (
        "both argument sets must retain the redacted representation"
    )


def test_duplicate_argument_error_preserves_key_like_positionals() -> None:
    """Non-assignment arguments with key-like segments remain diagnosable."""
    expectation = Expectation("deploy").with_args("my-key-file", "ssh-key")

    with pytest.raises(ExpectationConfigurationError) as exc_info:
        expectation.with_args("replacement-file")

    message = str(exc_info.value)
    assert "'my-key-file'" in message, "safe key-like positionals must remain visible"
    assert "'ssh-key'" in message, "safe key-like positionals must remain visible"
