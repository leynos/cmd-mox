Feature: Replay session loads fixtures and matches invocations

  A ReplaySession loads a recorded fixture file and matches incoming
  command invocations against the recorded entries, returning the
  recorded response. Consumed recordings are tracked to ensure
  all recordings are replayed during verification.

  Scenario: replay session loads and matches a recorded invocation
    Given a fixture file with a recording of "git" with args "status"
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    And a replay invocation of "git" with args "status" is matched
    Then the replay match result is a response with stdout "ok\n"

  Scenario: replay session tracks consumed recordings
    Given a fixture file with 2 recordings of "git" with args "status"
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    And a replay invocation of "git" with args "status" is matched
    And a replay invocation of "git" with args "status" is matched again
    Then all replay recordings are consumed
    And replay verify_all_consumed does not raise

  Scenario: replay session returns none for unmatched invocations
    Given a fixture file with a recording of "git" with args "status"
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    And a replay invocation of "curl" with args "example.com" is matched
    Then the replay match result is None

  Scenario: fuzzy mode ignores stdin and env differences
    Given a fixture file with a recording of "git" with args "status" and stdin "input" and env "GIT_DIR" equals ".git"
    And a replay session targeting that fixture in fuzzy mode
    When the replay session is loaded
    And a replay invocation of "git" with args "status" with different stdin and env is matched
    Then the replay match result is a response with stdout "ok\n"

  Scenario: verify_all_consumed raises for unconsumed recordings
    Given a fixture file with a recording of "git" with args "status"
    And a replay session targeting that fixture in strict mode
    When the replay session is loaded
    Then replay verify_all_consumed raises VerificationError
