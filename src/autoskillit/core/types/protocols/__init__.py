"""Service-boundary protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._type_protocols_backend import *  # noqa: F403
from ._type_protocols_backend import __all__ as _protocols_backend_all
from ._type_protocols_execution import *  # noqa: F403
from ._type_protocols_execution import __all__ as _protocols_execution_all
from ._type_protocols_github import *  # noqa: F403
from ._type_protocols_github import __all__ as _protocols_github_all
from ._type_protocols_infra import *  # noqa: F403
from ._type_protocols_infra import __all__ as _protocols_infra_all
from ._type_protocols_logging import *  # noqa: F403
from ._type_protocols_logging import __all__ as _protocols_logging_all
from ._type_protocols_recipe import *  # noqa: F403
from ._type_protocols_recipe import __all__ as _protocols_recipe_all
from ._type_protocols_workspace import *  # noqa: F403
from ._type_protocols_workspace import __all__ as _protocols_workspace_all

if not TYPE_CHECKING:
    __all__ = (
        _protocols_backend_all
        + _protocols_execution_all
        + _protocols_github_all
        + _protocols_infra_all
        + _protocols_logging_all
        + _protocols_recipe_all
        + _protocols_workspace_all
    )
