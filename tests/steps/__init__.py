"""Aggregate pytest-bdd step definitions for controller features."""

from .assertions import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .command_config import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .command_execution import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .controller_replay import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .controller_setup import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .documentation import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .environment import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .journal import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports
from .shim_management import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports

# Re-export all imported step definitions so ``from tests.steps import *``
# makes them available to scenario modules during collection.
__all__: list[str] = [
    name for name in globals() if not name.startswith("_") and name != "annotations"
]
