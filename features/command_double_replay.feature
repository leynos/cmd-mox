Feature: CommandDouble.replay() fluent API

  The .replay() method on CommandDouble attaches a ready-to-use replay fixture
  to a spy during test setup.

  Scenario: fluent API creates a strict replay session on a spy
    Given a CmdMox controller
    And a replay fixture for "git"
    When replay is called on a "git" spy
    Then the spy has a replay session attached
    And the replay session is loaded
    And the replay session uses strict matching

  Scenario: replay can use fuzzy matching
    Given a CmdMox controller
    And a replay fixture for "git"
    When replay is called on a "git" spy with strict disabled
    Then the spy has a replay session attached
    And the replay session uses fuzzy matching

  Scenario: replay cannot be combined with passthrough
    Given a CmdMox controller
    And a replay fixture for "git"
    And a "git" spy with passthrough enabled
    When replay is combined with passthrough it raises ValueError
