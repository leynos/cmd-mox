"""Aggregate pytest-bdd step definitions for controller features."""

from .assertions import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .command_config import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .command_execution import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .controller_replay import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .controller_setup import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .documentation import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .environment import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .journal import *  # noqa: F403 - pytest-bdd registers step definitions through star imports
from .shim_management import *  # noqa: F403 - pytest-bdd registers step definitions through star imports

# Re-export all imported step definitions so ``from tests.steps import *``
# makes them available to scenario modules during collection.
__all__: list[str] = [
    name for name in globals() if not name.startswith("_") and name != "annotations"
]
