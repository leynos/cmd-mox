Feature: CmdMox basic functionality
  Scenario: stubbed command execution
    Given a CmdMox controller
    And the command "hi" is stubbed to return "hello"
    When I replay the controller
    And I run the command "hi"
    Then the output should be "hello"
    When I verify the controller
    Then the journal should contain 1 invocation of "hi"

  Scenario: mocked command execution
    Given a CmdMox controller
    And the command "hi" is mocked to return "hello"
    When I replay the controller
    And I run the command "hi"
    Then the output should be "hello"
    When I verify the controller
    Then the journal should contain 1 invocation of "hi"

  Scenario: spy records invocation
    Given a CmdMox controller
    And the command "hi" is spied to return "hello"
    When I replay the controller
    And I run the command "hi"
    Then the output should be "hello"
    When I verify the controller
    Then the spy "hi" should record 1 invocation

  Scenario: journal preserves invocation order
    Given a CmdMox controller
    And the command "foo" is mocked to return "one" times 2
    And the command "bar" is spied to return "two"
    When I replay the controller
    And I run the command "foo"
    And I run the command "bar"
    And I run the command "foo"
    When I verify the controller
    Then the journal order should be foo,bar,foo
    And the mock "foo" should record 2 invocation
    And the spy "bar" should record 1 invocation

  Scenario: context manager usage
    Given a CmdMox controller
    And the command "hi" is stubbed to return "hello"
    When I run the command "hi" using a with block
    Then the output should be "hello"
    Then the journal should contain 1 invocation of "hi"

  Scenario: stub runs dynamic handler
    Given a CmdMox controller
    And the command "dyn" is stubbed to run a handler
    When I replay the controller
    And I run the command "dyn"
    Then the output should be "handled"
    When I verify the controller
    Then the journal should contain 1 invocation of "dyn"

  Scenario: ordered mocks match arguments
    Given a CmdMox controller
    And the command "first" is mocked with args "a" returning "one" in order
    And the command "second" is mocked with args "b" returning "two" in order
    When I replay the controller
    And I run the command "first" with arguments "a"
    And I run the command "second" with arguments "b"
    When I verify the controller
    Then the journal order should be first,second

  Scenario: environment variables can be injected
    Given a CmdMox controller
    And the command "envcmd" is stubbed with env var "HELLO"="WORLD"
    When I replay the controller
    And I run the command "envcmd"
    Then the output should be "WORLD"
    When I verify the controller
    Then the journal should contain 1 invocation of "envcmd"

  Scenario: passthrough spy executes real command
    Given a CmdMox controller
    And the command "echo" is spied to passthrough
    When I replay the controller
    And I run the command "echo" with arguments "hello"
    Then the output should be "hello"
    When I verify the controller
    Then the spy "echo" should record 1 invocation
