Feature: Windows platform smoke tests
  CmdMox should exercise its core workflows on Windows using batch shims.

  Scenario: Windows shims support mocks and passthrough spies
    Given windows shim launchers are enabled
    And the platform override is "win32"
    And I set environment variable "PATHEXT" to ".COM;.EXE"
    And a CmdMox controller
    And the command "cmd-mock" is mocked to return "windows mock"
    And the command "whoami" is spied to passthrough
    When I replay the controller
    Then PATHEXT should include ".CMD"
    And the IPC socket should be a Windows named pipe
    And I run the command "cmd-mock"
    Then the output should be "windows mock"
    And the shim for "cmd-mock" should end with ".cmd"
    When I run the command "whoami"
    Then the spy "whoami" should have been called
    When I verify the controller
    Then the spy "whoami" call count should be 1
    And PATHEXT should equal ".COM;.EXE"
