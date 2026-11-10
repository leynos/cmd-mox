Feature: Documentation
  Scenario: user's guide lists public API exports
    Given the CmdMox user's guide is available
    Then the user's guide API reference lists all public cmd_mox symbols


  Scenario: published examples are declared and runnable
    Given the published CmdMox documents
    Then every code fence in them declares how it is checked
    And the README quick start passes when run as a reader would run it
