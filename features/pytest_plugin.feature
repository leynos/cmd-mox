Feature: Pytest plugin
  Scenario: cmd_mox fixture basic usage
    Given a temporary test file using the cmd_mox fixture
    When I run pytest on the file
    Then the run should pass

  Scenario: automatic lifecycle errors explain how to opt out
    Given a pytest test that manually replays with auto lifecycle
    When I run pytest on the file
    Then the failure should explain how to disable auto lifecycle

  Scenario: parallel tests use isolated shim directories and sockets
    Given a pytest suite exercising concurrent cmd_mox tests
    When I run pytest with 2 workers
    Then each worker should use isolated shim directories and sockets

  Scenario: fail-fast mock mismatches fail at call time
    Given a temporary test file using fail-fast mock handling
    When I run pytest on the file
    Then the fail-fast run should fail verification after a rejected call

  Scenario: mismatched mock calls defer to verification by default
    Given a temporary test file using default mock handling
    When I run pytest on the file
    Then the deferred run should answer the call and fail verification
