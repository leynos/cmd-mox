Feature: CmdMox basic functionality
  Scenario: stubbed command execution
    Given a CmdMox controller
    And the command "hi" is stubbed to return "hello"
    When I replay the controller
    And I run the command "hi"
    Then the output should be "hello"
    And the journal should contain 1 invocation of "hi"

  Scenario: mocked command execution
    Given a CmdMox controller
    And the command "hi" is mocked to return "hello"
    When I replay the controller
    And I run the command "hi"
    Then the output should be "hello"
    And the journal should contain 1 invocation of "hi"

  Scenario: spy records invocation
    Given a CmdMox controller
    And the command "hi" is spied to return "hello"
    When I replay the controller
    And I run the command "hi"
    Then the output should be "hello"
    And the spy "hi" should record 1 invocation
