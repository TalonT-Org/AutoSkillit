"""AutoSkillit server for orchestrating skill-driven workflows."""

import logging
from importlib.metadata import version

__version__ = version("autoskillit")
logging.getLogger(__name__).addHandler(logging.NullHandler())  # noqa: TID251
