Feature: Ordered expectation verification
  Scenario: unordered invocation of matching command is ignored
    Given an ordered expectation for command "git" with args "fetch"
    And an unordered expectation for command "git" with args "status"
    And the journal contains invocation "git" with args "status"
    And the journal contains invocation "git" with args "fetch"
    When I validate ordered expectations
    Then the ordered verification should succeed
