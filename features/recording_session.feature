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
    And the fixture env_subset contains "MY_SETTING"

  Scenario: recording session generates fixture metadata
    Given a recording session targeting a temporary fixture file
    When the session is started
    And an invocation of "echo" with args "hello" is recorded
    And the session is finalized
    Then the fixture metadata contains the current platform
    And the fixture metadata contains a valid ISO8601 timestamp
    And the fixture metadata contains the Python version
