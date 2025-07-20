Feature: Pytest plugin
  Scenario: cmd_mox fixture basic usage
    Given a temporary test file using the cmd_mox fixture
    When I run pytest on the file
    Then the run should pass
