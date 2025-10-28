"""Aggregate pytest-bdd step definitions for controller features."""

from .assertions import *  # noqa: F403
from .command_config import *  # noqa: F403
from .command_execution import *  # noqa: F403
from .controller_setup import *  # noqa: F403
from .environment import *  # noqa: F403
from .journal import *  # noqa: F403
from .shim_management import *  # noqa: F403

# Re-export all imported step definitions so ``from tests.steps import *``
# makes them available to scenario modules during collection.
__all__ = [
    name
    for name in globals()
    if not name.startswith("_") and name != "annotations"
]
