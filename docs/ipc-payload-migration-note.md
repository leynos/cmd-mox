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

Payloads are decoded strictly. `response_from_payload(payload)` remains as a
compatibility name for `decode_response(payload)`, and both raise
`cmd_mox.ipc.wire.WireFormatError` when a field has the wrong type, a required
field is missing, an unknown field is present, or a passthrough directive is
malformed. They no longer coerce or silently drop values.
`request_from_payload` and the `json_utils.validate_invocation_payload` /
`validate_passthrough_payload` aliases are removed; use `decode_invocation`,
`decode_passthrough_result`, or `decode_passthrough_request` instead.
`WireFormatError` messages name a field path and an expected type only, so they
are safe to log.

See the [usage guide](usage-guide.md#ipc-payload-migration) for the complete
migration guidance.
