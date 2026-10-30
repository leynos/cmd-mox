# IPC payload migration note

The next minor release moves IPC serialization out of the domain values.
Existing calls to the conversion methods listed below must migrate to
`cmd_mox.ipc.wire`; the methods are no longer available on the classes. Imports
of the domain classes through `cmd_mox.ipc` remain supported.

| Removed method                   | Replacement                         |
| -------------------------------- | ----------------------------------- |
| `Invocation.to_dict()`           | `invocation_to_payload(invocation)` |
| `Response.to_dict()`             | `response_to_payload(response)`     |
| `Response.from_payload(payload)` | `response_from_payload(payload)`    |
| `PassthroughRequest.to_dict()`   | `passthrough_to_payload(request)`   |
| `PassthroughResult.to_dict()`    | `passthrough_to_payload(result)`    |

_Table: IPC conversion methods and their wire-function replacements._

See the [usage guide](usage-guide.md#ipc-payload-migration) for the complete
migration guidance.
