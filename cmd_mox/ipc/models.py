"""Compatibility re-exports for the former IPC model module.

Existing imports from this module resolve to the values in
:mod:`cmd_mox.domain`; new code may import those values there directly.
"""

from cmd_mox.domain import Invocation, PassthroughRequest, PassthroughResult, Response

__all__ = ["Invocation", "PassthroughRequest", "PassthroughResult", "Response"]
