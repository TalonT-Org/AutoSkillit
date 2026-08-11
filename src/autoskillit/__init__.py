"""AutoSkillit server for orchestrating skill-driven workflows."""

import logging
from importlib.metadata import version
from pathlib import Path

__version__ = version("autoskillit")
logging.getLogger(__name__).addHandler(logging.NullHandler())  # noqa: TID251


def consume_exploration_request_record(
    project_root: Path,
    expected_tool_name: str,
    token: str,
) -> str | None:
    """Consume a one-shot exploration request through the package gateway."""
    from autoskillit.hooks import consume_exploration_request_record as _consume

    return _consume(project_root, expected_tool_name, token)
