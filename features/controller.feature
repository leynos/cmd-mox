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

  Scenario: spy assertion helpers
    Given a CmdMox controller
    And the command "hi" is spied to return "hello"
    And the command "bye" is spied to return "goodbye"
    When I replay the controller
    And I run the command "hi" with arguments "foo bar"
    When I verify the controller
    Then the spy "hi" should have been called
    And the spy "hi" should have been called with arguments "foo bar"
    And the spy "bye" should not have been called

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

  Scenario: times alias maps to times_called
    Given a CmdMox controller
    And the command "first" is mocked to return "one" times 2
    And the command "second" is mocked to return "two" times called 2
    When I replay the controller
    And I run the command "first"
    And I run the command "first"
    And I run the command "second"
    And I run the command "second"
    When I verify the controller
    Then the mock "first" should record 2 invocation
    And the mock "second" should record 2 invocation

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

  Scenario: any_order mock can run before ordered expectation
    Given a CmdMox controller
    And the command "first" is mocked with args "a" returning "one" in order
    And the command "second" is mocked with args "b" returning "two" any order
    When I replay the controller
    And I run the command "second" with arguments "b"
    And I run the command "first" with arguments "a"
    When I verify the controller
    Then the journal order should be second,first

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
    And the spy "echo" call count should be 1

  Scenario: passthrough spy handles missing command
    Given a CmdMox controller
    And the command "bogus" is spied to passthrough
    When I replay the controller
    And I run the command "bogus" expecting failure
    Then the exit code should be 127
    And the stderr should contain "not found"
    When I verify the controller
    Then the spy "bogus" should record 1 invocation

  Scenario: passthrough spy handles permission error
    Given a CmdMox controller
    And the command "dummycmd" is spied to passthrough
    And the command "dummycmd" resolves to a non-executable file
    When I replay the controller
    And I run the command "dummycmd" expecting failure
    Then the exit code should be 126
    And the stderr should contain "not executable"
    When I verify the controller
    Then the spy "dummycmd" should record 1 invocation

  Scenario: passthrough spy handles timeout
    Given a CmdMox controller
    And the command "echo" is spied to passthrough
    And the command "echo" will timeout
    When I replay the controller
    And I run the command "echo" expecting failure
    Then the exit code should be 124
    And the stderr should contain "timeout after 30 seconds"
    When I verify the controller
    Then the spy "echo" should record 1 invocation

  Scenario: mock matches arguments with comparators
    Given a CmdMox controller
    And the command "flex" is mocked to return "ok" with comparator args
    When I replay the controller
    And I run the command "flex" with arguments "anything 123 foo7 barbar bazooka HELLO"
    Then the output should be "ok"
    When I verify the controller
    Then the journal should contain 1 invocation of "flex"

  Scenario: journal captures invocation details
    Given a CmdMox controller
    And the command "rec" is stubbed to return "ok"
    When I replay the controller
    And I run the command "rec" with arguments "alpha beta" using stdin "payload" and env var "EXTRA"="42"
    And I set environment variable "EXTRA" to "99"
    When I verify the controller
    Then the journal entry for "rec" should record arguments "alpha beta" stdin "payload" env var "EXTRA"="42"
    And the journal entry for "rec" should record stdout "ok" stderr "" exit code 0

  Scenario: journal prunes excess entries
    Given a CmdMox controller with max journal size 2
    And the command "alpha" is stubbed to return "ok"
    And the command "beta" is stubbed to return "ok"
    And the command "gamma" is stubbed to return "ok"
    When I replay the controller
    And I run the command "alpha"
    And I run the command "beta"
    And I run the command "gamma"
    When I verify the controller
    Then the journal should contain 1 invocation of "beta"
    And the journal should contain 1 invocation of "gamma"
    And the journal should contain 0 invocation of "alpha"
    And the journal order should be beta,gamma

  Scenario: invalid max journal size is rejected
    Given creating a CmdMox controller with max journal size -1 fails
