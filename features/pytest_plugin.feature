Feature: Pytest plugin
  Scenario: cmd_mox fixture basic usage
    Given a temporary test file using the cmd_mox fixture
    When I run pytest on the file
    Then the run should pass

  Scenario: parallel tests use isolated shim directories and sockets
    Given a pytest suite exercising concurrent cmd_mox tests
    When I run pytest with 2 workers
    Then each worker should use isolated shim directories and sockets
