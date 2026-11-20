Feature: Recording session captures passthrough interactions

  A RecordingSession collects invocation/response pairs from passthrough spy
  executions and persists them as versioned JSON fixture files with filtered
  environment variables.

  Scenario: recording session persists fixture to disk
    Given a recording session targeting a temporary fixture file
    When the session is started
    And an invocation of "git" with args "status" is recorded
    And the session is finalized
    Then the fixture file exists on disk
    And the fixture contains 1 recording
    And the recording command is "git"
    And the recording args are "status"

  Scenario: environment variables are filtered to safe subset
    Given a recording session with allowlist "MY_SETTING"
    When the session is started
    And an invocation with sensitive and system env vars is recorded
    And the session is finalized
    Then the fixture env_subset does not contain "SECRET_TOKEN"
    And the fixture env_subset does not contain "PATH"
    And the fixture env_subset does not contain "SAFE_VAR"
    And the fixture env_subset contains "MY_SETTING"

  Scenario: recording session generates fixture metadata
    Given a recording session targeting a temporary fixture file
    When the session is started
    And an invocation of "echo" with args "hello" is recorded
    And the session is finalized
    Then the fixture metadata contains the current platform
    And the fixture metadata contains a valid ISO8601 timestamp
    And the fixture metadata contains the Python version

  Scenario: finalizing an unstarted session raises a lifecycle error
    Given a recording session targeting a temporary fixture file
    When the session is finalized without being started
    Then a lifecycle error is raised
    And the fixture file does not exist

  Scenario: the command filter matches command names case-insensitively
    Given a recording session with command filter "GIT"
    When the session is started
    And an invocation of "git" with args "status" is recorded
    And an invocation of "echo" with args "hello" is recorded
    And the session is finalized
    Then the fixture contains 1 recording
    And the recording command is "git"
    And the saved fixture JSON contains 1 recording

  Scenario: a failed atomic replacement preserves the previous fixture
    Given a recording session targeting an existing fixture file
    And the fixture replacement is configured to fail
    When the session is started
    And an invocation of "git" with args "status" is recorded
    And finalizing the session is attempted
    Then the fixture save fails with an atomic write error
    And the saved fixture JSON contains 0 recordings

  Scenario: loading a fixture with an older schema version migrates it
    Given a 0.9.0 fixture file on disk
    When the fixture file is loaded
    Then the loaded fixture has version "1.0.0"
    And the loaded fixture contains 1 recording
    And the loaded fixture metadata is preserved
