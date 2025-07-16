Feature: CmdMox basic functionality
  Scenario: stubbed command execution
    Given a CmdMox controller
    And the command "hi" is stubbed to return "hello"
    When I replay the controller
    And I run the command "hi"
    Then the output should be "hello"
    And the journal should contain 1 invocation of "hi"
