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

  Scenario: loading a fixture with an older schema version migrates it
    Given a 0.9.0 fixture file on disk
    When the fixture file is loaded
    Then the loaded fixture has version "1.0.0"
    And the loaded fixture contains 1 recording
    And the loaded fixture metadata is preserved

  Scenario: recorded fixture is readable only by its owner
    Given a recording session targeting "fixtures/git_status.json"
    When the session records a "git" invocation and finalizes
    Then the fixture file loads with 1 recording
    And the fixture file is readable and writable only by its owner
    And the fixture directory contains only the fixture file

  Scenario: failed fixture save keeps the previous fixture
    Given an existing fixture "fixtures/git_status.json" with 1 recording
    And the final rename of fixture writes will fail
    When a new session records 2 invocations to the same fixture and finalizes
    Then finalizing fails with an atomic write error at stage "replace"
    And the fixture file still loads with 1 recording
    And the fixture directory contains only the fixture file
