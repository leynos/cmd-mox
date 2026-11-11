"""Unit tests for environment variable subset filtering."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import given

from cmd_mox.domain import is_sensitive_recording_env_key
from cmd_mox.record.env_filter import (
    COMMAND_ENV_KEYS,
    COMMAND_ENV_PREFIXES,
    GO_ENV_KEYS,
    filter_env_subset,
)

#: Keys drawn from a mix of arbitrary text and names that exercise each
#: filtering rule (system, sensitive, command-prefixed, CmdMox-internal).
_ENV_KEYS = st.one_of(
    st.text(max_size=8),
    st.sampled_from([
        "PATH",
        "HOME",
        "SAFE_VAR",
        "GIT_AUTHOR_NAME",
        "GOPATH",
        "GOOGLE_CLOUD_PROJECT",
        "API_TOKEN",
        "CMOX_IPC_SOCKET",
        "CMD_MOX_DEBUG",
    ]),
)
_ENVS = st.dictionaries(_ENV_KEYS, st.text(max_size=8), max_size=8)
_KEY_LISTS = st.lists(_ENV_KEYS, max_size=4)

#: Every recognized command plus the two fallback spellings.  Kept explicit so
#: a new entry in either mapping must be added here deliberately.
_ALL_COMMANDS = [
    "git",
    "aws",
    "docker",
    "npm",
    "pip",
    "cargo",
    "go",
    "rustc",
    "",
    "unknown-command",
]


@dc.dataclass(frozen=True, slots=True)
class CommandEnvCase:
    """One command's filtering expectations.

    Attributes
    ----------
    command : str
        The command name passed to :func:`filter_env_subset`.
    retained : str
        A key the command's own rule keeps.
    unrelated : str
        A benign key no rule keeps.
    foreign : str
        Another command's prefix key, which this command must drop.
    sensitive : str
        A sensitive key that would otherwise match the command's rule.
    """

    command: str
    retained: str
    unrelated: str
    foreign: str
    sensitive: str


#: One row per recognized command.
_COMMAND_ENV_TABLE: list[CommandEnvCase] = [
    CommandEnvCase("git", "GIT_DIR", "UNRELATED_SETTING", "AWS_REGION", "GIT_TOKEN"),
    CommandEnvCase(
        "aws", "AWS_REGION", "UNRELATED_SETTING", "GIT_DIR", "AWS_SECRET_ACCESS_KEY"
    ),
    CommandEnvCase(
        "docker",
        "DOCKER_HOST",
        "UNRELATED_SETTING",
        "NPM_CONFIG_CACHE",
        "DOCKER_AUTH_CONFIG",
    ),
    CommandEnvCase(
        "npm", "NPM_CONFIG_CACHE", "UNRELATED_SETTING", "DOCKER_HOST", "NPM_TOKEN"
    ),
    CommandEnvCase(
        "pip", "PIP_INDEX_URL", "UNRELATED_SETTING", "CARGO_HOME", "PIP_PASSWORD"
    ),
    CommandEnvCase(
        "cargo",
        "CARGO_HOME",
        "UNRELATED_SETTING",
        "PIP_INDEX_URL",
        "CARGO_REGISTRY_TOKEN",
    ),
    CommandEnvCase("go", "GOPATH", "UNRELATED_SETTING", "GIT_DIR", "GO_TOKEN"),
    CommandEnvCase(
        "rustc", "RUSTC_WRAPPER", "UNRELATED_SETTING", "GOPATH", "RUSTC_TOKEN"
    ),
]
_COMMAND_ENV_CASES = [
    pytest.param(case, id=case.command) for case in _COMMAND_ENV_TABLE
]

#: Hand-written copy of the Go variables documented by ``go help environment``
#: and ``go doc runtime``.  Written independently of :data:`GO_ENV_KEYS` so a
#: name dropped from the production set fails a test instead of silently
#: redefining the expected list.
_EXPECTED_GO_ENV_KEYS = frozenset({
    "GO111MODULE",
    "GO386",
    "GOAMD64",
    "GOARCH",
    "GOARM",
    "GOARM64",
    "GOAUTH",
    "GOBIN",
    "GOCACHE",
    "GOCACHEPROG",
    "GOCOVERDIR",
    "GODEBUG",
    "GOENV",
    "GOEXPERIMENT",
    "GO_EXTLINK_ENABLED",
    "GOFIPS140",
    "GOFLAGS",
    "GOGC",
    "GOINSECURE",
    "GOMAXPROCS",
    "GOMEMLIMIT",
    "GOMIPS",
    "GOMIPS64",
    "GOMODCACHE",
    "GONOPROXY",
    "GONOSUMDB",
    "GOOS",
    "GOPATH",
    "GOPPC64",
    "GOPRIVATE",
    "GOPROXY",
    "GORACE",
    "GORISCV64",
    "GOROOT",
    "GOSUMDB",
    "GOTMPDIR",
    "GOTOOLCHAIN",
    "GOTRACEBACK",
    "GOVCS",
    "GOWASM",
    "GOWORK",
})


class TestGoEnvironmentRule:
    """Tests for the exact-name rule that replaced Go's ``GO`` prefix."""

    def test_go_env_keys_match_the_documented_list(self) -> None:
        """The production set matches the independently written Go list."""
        assert GO_ENV_KEYS == _EXPECTED_GO_ENV_KEYS, (
            "GO_ENV_KEYS diverged from the documented Go variables: "
            f"missing {sorted(_EXPECTED_GO_ENV_KEYS - GO_ENV_KEYS)}, "
            f"unexpected {sorted(GO_ENV_KEYS - _EXPECTED_GO_ENV_KEYS)}"
        )

    def test_command_env_keys_rejects_mutation(self) -> None:
        """The shared exact-name policy cannot be widened at runtime."""
        # The cast is deliberate: it lets the test attempt the mutation that
        # the Mapping annotation otherwise makes unrepresentable.
        mutable_policy = typ.cast("dict[str, frozenset[str]]", COMMAND_ENV_KEYS)

        with pytest.raises(TypeError):
            mutable_policy["go"] = frozenset()

    def test_go_retains_every_documented_variable(self) -> None:
        """Go's exact-name rule keeps the whole documented list and no lookalikes."""
        env = dict.fromkeys(_EXPECTED_GO_ENV_KEYS, "keep")
        env.update({
            "GOOGLE_CLOUD_PROJECT": "drop",
            "GOLDEN_FLAG": "drop",
            "CGO_CFLAGS": "drop",
        })

        result = filter_env_subset(env, command="go")

        assert set(result) == _EXPECTED_GO_ENV_KEYS, (
            "go should retain every documented variable and drop lookalikes: "
            f"{sorted(_EXPECTED_GO_ENV_KEYS - set(result))} were dropped"
        )

    def test_go_does_not_record_google_cloud_project(self) -> None:
        """The issue #432 regression: ``GO`` must not match ``GOOGLE_*``."""
        env = {"GOOGLE_CLOUD_PROJECT": "x", "GOPATH": "y"}

        assert filter_env_subset(env, command="go") == {"GOPATH": "y"}, (
            "the go rule must not match GOOGLE_* names"
        )

    @pytest.mark.parametrize(
        ("allowlist", "explicit_keys", "key"),
        [
            pytest.param(
                ["GOOGLE_CLOUD_PROJECT"],
                None,
                "GOOGLE_CLOUD_PROJECT",
                id="allowlist",
            ),
            pytest.param(
                None,
                ["CGO_CFLAGS"],
                "CGO_CFLAGS",
                id="explicit_keys",
            ),
        ],
    )
    def test_requested_keys_readmit_go_lookalikes(
        self,
        allowlist: list[str] | None,
        explicit_keys: list[str] | None,
        key: str,
    ) -> None:
        """Either override keyword re-admits a key the Go rule rejects."""
        result = filter_env_subset(
            {key: "x"},
            command="go",
            allowlist=allowlist,
            explicit_keys=explicit_keys,
        )

        assert result == {key: "x"}, f"{key} should be re-admitted when requested"

    def test_go_exact_names_are_not_sensitive(self) -> None:
        """Every documented Go variable survives the sensitivity classifier."""
        sensitive = [key for key in GO_ENV_KEYS if is_sensitive_recording_env_key(key)]

        assert not sensitive, sensitive


class TestFilterEnvSubset:
    """Tests for filter_env_subset()."""

    def test_excludes_sensitive_keys(self) -> None:
        """Keys matching sensitive patterns are excluded."""
        env = {
            "AWS_SECRET_ACCESS_KEY": "s3cr3t",
            "API_TOKEN": "tok123",
            "DB_PASSWORD": "hunter2",
            "GITHUB_KEY": "ghk",
            "GIT_AUTHOR_NAME": "keep",
        }
        result = filter_env_subset(env, command="git")

        assert "AWS_SECRET_ACCESS_KEY" not in result
        assert "API_TOKEN" not in result
        assert "DB_PASSWORD" not in result
        assert "GITHUB_KEY" not in result
        assert "GIT_AUTHOR_NAME" in result

    def test_sensitive_keys_sharing_command_prefix_are_excluded(self) -> None:
        """Sensitivity wins over the known-prefix rule for the same command."""
        env = {
            "GIT_TOKEN": "tok123",
            "GIT_SECRET": "s3cr3t",
            "GIT_PASSWORD": "hunter2",
            "GIT_API_KEY": "key",
            "GIT_AUTHOR_NAME": "keep",
        }
        result = filter_env_subset(env, command="git")

        for key in ("GIT_TOKEN", "GIT_SECRET", "GIT_PASSWORD", "GIT_API_KEY"):
            assert key not in result, f"{key} should be excluded as sensitive"
        assert result == {"GIT_AUTHOR_NAME": "keep"}

    def test_excludes_credential_config_keys(self) -> None:
        """Registry credentials in ``*_AUTH_CONFIG`` never reach fixtures."""
        env = {
            "DOCKER_AUTH_CONFIG": '{"auths": {"registry.test": {"auth": "opaque"}}}',
            "AUTH_CONFIG": "opaque",
            # Shares the command prefix, so only the sensitivity rule can drop
            # this one. Without it the assertion would pass on the prefix rule.
            "DOCKER_HOST": "unix:///run/docker.sock",
        }
        result = filter_env_subset(env, command="docker")

        assert "DOCKER_AUTH_CONFIG" not in result
        assert "AUTH_CONFIG" not in result
        assert "DOCKER_HOST" in result

    @pytest.mark.parametrize(
        ("allowlist", "explicit_keys"),
        [
            pytest.param(["DOCKER_AUTH_CONFIG"], None, id="allowlist"),
            pytest.param(None, ["DOCKER_AUTH_CONFIG"], id="explicit_keys"),
        ],
    )
    def test_requested_sensitive_key_is_persisted_raw(
        self,
        allowlist: list[str] | None,
        explicit_keys: list[str] | None,
    ) -> None:
        """A request outranks the sensitive rule and stores the raw value."""
        raw = '{"auths": {"registry.test": {"auth": "opaque"}}}'

        result = filter_env_subset(
            {"DOCKER_AUTH_CONFIG": raw},
            command="docker",
            allowlist=allowlist,
            explicit_keys=explicit_keys,
        )

        assert result == {"DOCKER_AUTH_CONFIG": raw}, (
            "a requested sensitive key should be persisted raw"
        )

    def test_excludes_system_keys(self) -> None:
        """System-specific keys are excluded."""
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/user",
            "USER": "testuser",
            "SHELL": "/bin/bash",
            "SSH_AUTH_SOCK": "/run/ssh-agent",
            "GPG_AGENT_INFO": "/run/gpg",
            "GIT_AUTHOR_NAME": "keep",
        }
        result = filter_env_subset(env, command="git")

        for key in ("PATH", "HOME", "USER", "SHELL", "SSH_AUTH_SOCK", "GPG_AGENT_INFO"):
            assert key not in result, f"{key} should be excluded"

        assert "GIT_AUTHOR_NAME" in result

    def test_includes_allowlisted_keys(self) -> None:
        """Allowlisted keys pass through even if they match exclusion patterns."""
        allowlisted_value = "supersecret"
        env = {
            "MY_SECRET_KEY": allowlisted_value,
            "PATH": "/usr/bin",
            "NORMAL": "val",
        }
        result = filter_env_subset(
            env,
            allowlist=["MY_SECRET_KEY", "PATH"],
        )

        assert result["MY_SECRET_KEY"] == allowlisted_value
        assert result["PATH"] == "/usr/bin"

    def test_includes_command_prefix_keys(self) -> None:
        """Command-specific prefix keys are included."""
        env = {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "UNRELATED": "val",
            "PATH": "/usr/bin",
        }
        result = filter_env_subset(env, command="git")

        assert "GIT_AUTHOR_NAME" in result
        assert "GIT_COMMITTER_EMAIL" in result
        assert "UNRELATED" not in result
        assert "PATH" not in result

    def test_includes_explicitly_requested_keys(self) -> None:
        """Keys passed as explicit_keys are always included."""
        requested_value = "tok"
        env = {
            "HOME": "/home/user",
            "SECRET_TOKEN": requested_value,
            "NORMAL": "val",
        }
        result = filter_env_subset(
            env,
            explicit_keys=["HOME", "SECRET_TOKEN"],
        )

        assert result["HOME"] == "/home/user"
        assert result["SECRET_TOKEN"] == requested_value

    def test_excludes_unrequested_keys_for_unknown_command(self) -> None:
        """An unknown command records no unrequested environment variables."""
        env = {
            "MY_SETTING": "enabled",
            "APP_MODE": "test",
            "DEBUG": "1",
        }
        result = filter_env_subset(env, command="greet")

        assert result == {}

    @pytest.mark.parametrize("request_kind", ["allowlist", "explicit_keys"])
    def test_unknown_command_keeps_requested_keys(self, request_kind: str) -> None:
        """An unknown command still records allowlisted or explicit keys."""
        env = {"MY_SETTING": "enabled", "UNRELATED": "value"}
        requested = ["MY_SETTING"]
        if request_kind == "allowlist":
            result = filter_env_subset(env, command="greet", allowlist=requested)
        else:
            result = filter_env_subset(env, command="greet", explicit_keys=requested)

        assert result == {"MY_SETTING": "enabled"}

    def test_unknown_command_excludes_requested_internal_keys(self) -> None:
        """Internal keys stay excluded for an unknown command even when requested."""
        env = {"CMOX_IPC_SOCKET": "socket", "MY_SETTING": "enabled"}
        result = filter_env_subset(
            env,
            command="greet",
            allowlist=["CMOX_IPC_SOCKET"],
            explicit_keys=["CMOX_IPC_SOCKET"],
        )

        assert result == {}

    def test_unknown_command_does_not_add_absent_requested_keys(self) -> None:
        """Requested keys that are absent from the input are not invented."""
        result = filter_env_subset(
            {"MY_SETTING": "enabled"},
            command="greet",
            allowlist=["MISSING"],
            explicit_keys=["ALSO_MISSING"],
        )

        assert result == {}

    @pytest.mark.parametrize("command", ["/usr/bin/git", "git.exe"])
    def test_command_lookup_is_whole_name_only(self, command: str) -> None:
        """Paths and ``.exe`` suffixes are not normalized to a known prefix."""
        env = {"GIT_AUTHOR_NAME": "Tester", "MY_SETTING": "enabled"}
        result = filter_env_subset(env, command=command)

        assert result == {}

    def test_empty_env(self) -> None:
        """An empty env produces an empty result."""
        assert filter_env_subset({}) == {}

    def test_excludes_sensitive_command_prefix_keys(self) -> None:
        """Sensitive keys with command-specific prefixes are still excluded."""
        env = {
            "GIT_SECRET_TOKEN": "s3cr3t",
            "GIT_AUTHOR_NAME": "User",
        }
        result = filter_env_subset(env, command="git")

        assert "GIT_AUTHOR_NAME" in result
        assert "GIT_SECRET_TOKEN" not in result

    def test_excludes_cmox_internal_keys(self) -> None:
        """CmdMox internal environment variables are excluded."""
        env = {
            "CMOX_IPC_SOCKET": "ipc-socket-path",
            "CMOX_IPC_TIMEOUT": "5.0",
            "CMOX_REAL_COMMAND_echo": "/usr/bin/echo",
            "GIT_AUTHOR_NAME": "keep",
        }
        result = filter_env_subset(env, command="git")

        assert "CMOX_IPC_SOCKET" not in result
        assert "CMOX_IPC_TIMEOUT" not in result
        assert "CMOX_REAL_COMMAND_echo" not in result
        assert "GIT_AUTHOR_NAME" in result

    @pytest.mark.parametrize(
        ("label", "allowlist", "explicit_keys", "cmox_keys"),
        [
            (
                "allowlist",
                ["CMOX_IPC_SOCKET", "CMD_MOX_DEBUG"],
                None,
                ["CMOX_IPC_SOCKET", "CMD_MOX_DEBUG"],
            ),
            (
                "explicit_keys",
                None,
                ["CMOX_IPC_SOCKET"],
                ["CMOX_IPC_SOCKET"],
            ),
        ],
        ids=["allowlist", "explicit_keys"],
    )
    def test_excludes_cmox_internal_keys_even_when_requested(
        self,
        label: str,
        allowlist: list[str] | None,
        explicit_keys: list[str] | None,
        cmox_keys: list[str],
    ) -> None:
        """CmdMox internal keys are excluded regardless of request method."""
        env = dict.fromkeys(cmox_keys, "value")
        env["GIT_AUTHOR_NAME"] = "keep"

        result = filter_env_subset(
            env,
            command="git",
            allowlist=allowlist,
            explicit_keys=explicit_keys,
        )

        for key in cmox_keys:
            assert key not in result, f"{key} should be excluded even when in {label}"
        assert "GIT_AUTHOR_NAME" in result


class TestFilterEnvSubsetCommandRules:
    """Tests for command-specific filtering rules."""

    @pytest.mark.parametrize("case", _COMMAND_ENV_CASES)
    def test_command_rules_retain_only_their_own_keys(
        self, case: CommandEnvCase
    ) -> None:
        """Each command keeps its own keys and drops everything else."""
        env = {
            case.retained: "keep",
            case.unrelated: "drop",
            case.foreign: "drop",
            case.sensitive: "drop",
        }

        assert filter_env_subset(env, command=case.command) == {
            case.retained: "keep"
        }, f"{case.command} rule should retain only {case.retained}"

    def test_command_table_covers_every_recognised_command(self) -> None:
        """The table names exactly the commands the filter recognizes."""
        table_commands = {case.command for case in _COMMAND_ENV_TABLE}
        recognised = set(COMMAND_ENV_PREFIXES) | set(COMMAND_ENV_KEYS)

        assert table_commands == recognised, (
            "the table should name every recognized command"
        )

    @pytest.mark.parametrize(
        ("command", "retained", "dropped"),
        [
            pytest.param("GO", "GOPATH", "GIT_DIR", id="upper-go"),
            pytest.param("Git", "GIT_DIR", "GOPATH", id="mixed-git"),
        ],
    )
    def test_command_matching_is_case_insensitive(
        self,
        command: str,
        retained: str,
        dropped: str,
    ) -> None:
        """Command names are normalized before their rule is selected."""
        env = {retained: "keep", dropped: "drop"}

        assert filter_env_subset(env, command=command) == {retained: "keep"}, (
            f"{command} should retain {retained} and drop the other keys"
        )

    def test_unknown_command_drops_unrequested_keys(self) -> None:
        """An unrecognized command keeps only allowlisted or requested keys."""
        env = {"GIT_DIR": "x", "APP_MODE": "y", "PATH": "/usr/bin"}

        assert filter_env_subset(env, command="unknown-command") == {}, (
            "unknown commands should drop every unrequested key"
        )


_UNKNOWN_COMMANDS = st.sampled_from([
    "",
    "greet",
    "unknown-command",
    "/usr/bin/git",
    "git.exe",
])


@given(
    env=_ENVS,
    command=_UNKNOWN_COMMANDS,
    allowlist=_KEY_LISTS,
    explicit_keys=_KEY_LISTS,
)
def test_filter_env_subset_bounds_unknown_command_results(
    env: dict[str, str],
    command: str,
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """A command without a known prefix keeps only requested, non-internal keys.

    Invariant: the result is a subset of ``(allowlist | explicit_keys) & env``
    once CmdMox-internal keys are removed.
    """
    result = filter_env_subset(
        env, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    requested = (set(allowlist) | set(explicit_keys)) & set(env)
    permitted = {key for key in requested if not key.startswith(("CMOX_", "CMD_MOX_"))}
    assert set(result) <= permitted, result


@pytest.mark.parametrize("command", _ALL_COMMANDS)
@given(env=_ENVS, allowlist=_KEY_LISTS, explicit_keys=_KEY_LISTS)
def test_filter_env_subset_returns_a_sub_mapping(
    env: dict[str, str],
    command: str,
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """Filtering only ever drops entries; it never adds keys or rewrites values.

    Invariant: ``filter_env_subset(env, ...).items() <= env.items()`` for every
    combination of *command*, *allowlist*, and *explicit_keys*.
    """
    result = filter_env_subset(
        env, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    assert result.items() <= env.items(), result


@pytest.mark.parametrize("command", _ALL_COMMANDS)
@given(env=_ENVS, allowlist=_KEY_LISTS, explicit_keys=_KEY_LISTS)
def test_filter_env_subset_never_leaks_cmdmox_internals(
    env: dict[str, str],
    command: str,
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """CmdMox-internal keys are dropped however they are requested.

    Invariant: no key of the result starts with ``CMOX_`` or ``CMD_MOX_``, even
    when that key is named in *allowlist* or *explicit_keys*.
    """
    result = filter_env_subset(
        env, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    leaked = [key for key in result if key.startswith(("CMOX_", "CMD_MOX_"))]
    assert not leaked, leaked


@pytest.mark.parametrize("command", _ALL_COMMANDS)
@given(env=_ENVS, allowlist=_KEY_LISTS, explicit_keys=_KEY_LISTS)
def test_filter_env_subset_is_idempotent(
    env: dict[str, str],
    command: str,
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """Re-filtering an already filtered subset changes nothing.

    Invariant: ``filter(filter(env, **kw), **kw) == filter(env, **kw)``, which
    follows from the decision being a per-key predicate.
    """
    once = filter_env_subset(
        env, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    twice = filter_env_subset(
        once, command=command, allowlist=allowlist, explicit_keys=explicit_keys
    )
    assert twice == once, twice


@given(env=_ENVS, allowlist=_KEY_LISTS, explicit_keys=_KEY_LISTS)
def test_allowlist_and_explicit_keys_are_equivalent_when_unioned(
    env: dict[str, str],
    allowlist: list[str],
    explicit_keys: list[str],
) -> None:
    """The two override keywords share one precedence and union.

    Invariant: passing *allowlist* and *explicit_keys* separately is equivalent
    to passing their union through either keyword alone.
    """
    separate = filter_env_subset(
        env,
        command="go",
        allowlist=allowlist,
        explicit_keys=explicit_keys,
    )
    as_allowlist = filter_env_subset(
        env,
        command="go",
        allowlist=allowlist + explicit_keys,
    )
    as_explicit = filter_env_subset(
        env,
        command="go",
        explicit_keys=allowlist + explicit_keys,
    )

    assert separate == as_allowlist == as_explicit, (
        "the two override keywords should be equivalent to their union"
    )


@given(env=_ENVS, allowlist=_KEY_LISTS)
def test_go_keeps_only_documented_or_requested_names(
    env: dict[str, str],
    allowlist: list[str],
) -> None:
    """The Go rule admits documented names and explicitly requested keys.

    Invariant: an undocumented Go key survives only when requested, every
    documented name present in *env* survives, and each request re-admits its
    key unless it is CmdMox-internal.
    """
    requested = set(allowlist)
    retained = set(filter_env_subset(env, command="go", allowlist=allowlist))

    undocumented = retained - GO_ENV_KEYS - requested
    assert not undocumented, undocumented

    documented = set(env) & GO_ENV_KEYS
    assert documented <= retained, sorted(documented - retained)

    internal = {key for key in requested if key.startswith(("CMOX_", "CMD_MOX_"))}
    assert (requested & set(env)) - internal <= retained


#: Names that start with ``GO`` but are not documented Go variables, so the
#: exact-name rule must reject them unless a caller requests them.  Sensitive
#: names are excluded so the property can assert an exact retained set.
_GO_LOOKALIKES = (
    st
    .text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789", min_size=2, max_size=8)
    .map(lambda suffix: f"GO{suffix}")
    .filter(
        lambda key: key not in GO_ENV_KEYS and not is_sensitive_recording_env_key(key)
    )
)


@st.composite
def _go_env_with_requests(draw: st.DrawFn) -> tuple[dict[str, str], list[str]]:
    """Draw a Go-flavoured environment and a subset of its keys as requests."""
    documented = draw(
        st.lists(st.sampled_from(sorted(GO_ENV_KEYS)), max_size=4, unique=True)
    )
    lookalikes = draw(st.lists(_GO_LOOKALIKES, max_size=4, unique=True))
    keys = sorted(set(documented) | set(lookalikes))
    env = dict.fromkeys(keys, "value")
    requested = draw(st.lists(st.sampled_from(keys), max_size=4)) if keys else []
    return env, requested


@given(case=_go_env_with_requests())
def test_go_lookalikes_survive_only_when_requested(
    case: tuple[dict[str, str], list[str]],
) -> None:
    """Go-shaped names outside the documented list need an explicit request.

    Invariant: the retained set is exactly the documented names present in the
    environment plus the requested keys, so an unrequested lookalike never
    survives.
    """
    env, requested = case

    retained = set(filter_env_subset(env, command="go", allowlist=requested))

    assert retained == set(env) & (GO_ENV_KEYS | set(requested)), (
        f"unexpected retention for {sorted(retained)}"
    )
