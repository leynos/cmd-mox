Feature: CommandDouble.record() fluent API

  The .record() method on CommandDouble enables automatic capture of
  passthrough spy invocations to reusable JSON fixture files.

  Scenario: fluent API creates a recording session on a passthrough spy
    Given a CmdMox controller
    And a spy for "git" with passthrough enabled
    When record is called with a fixture path
    Then the spy has a recording session attached
    And the recording session is started

  Scenario: record without passthrough raises ValueError
    Given a CmdMox controller
    And a spy for "git" without passthrough
    When record is called without passthrough it raises ValueError
