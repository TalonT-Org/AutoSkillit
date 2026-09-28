"""Core type contracts re-exported through cohesive group facades.

Groups: foundation, install, github, skill, audit, recipe, constants, results,
execution, launch, context_admission, protocols.
See types/AGENTS.md for layering and import rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# isort: off
from .foundation import *  # noqa: F403
from .install import *  # noqa: F403
from .github import *  # noqa: F403
from .skill import *  # noqa: F403
from .audit import *  # noqa: F403
from .recipe import *  # noqa: F403
from .constants import *  # noqa: F403
from .results import *  # noqa: F403
from .execution import *  # noqa: F403
from .launch import *  # noqa: F403
from .context_admission import *  # noqa: F403
from .protocols import *  # noqa: F403
# isort: on

if TYPE_CHECKING:
    from .audit import _MAX_ASSOCIATION_FILES as _MAX_ASSOCIATION_FILES
    from .audit import _MAX_REFERENCED_ARTIFACTS_PER_CALL as _MAX_REFERENCED_ARTIFACTS_PER_CALL
    from .audit import _PLAN_ASSOCIATION_DOMAIN as _PLAN_ASSOCIATION_DOMAIN
    from .audit import _PLAN_ASSOCIATION_KEYS as _PLAN_ASSOCIATION_KEYS

if not TYPE_CHECKING:
    from .audit import __all__ as _audit_all
    from .constants import __all__ as _constants_all
    from .context_admission import __all__ as _context_admission_all
    from .execution import __all__ as _execution_all
    from .foundation import __all__ as _foundation_all
    from .github import __all__ as _github_all
    from .install import __all__ as _install_all
    from .launch import __all__ as _launch_all
    from .protocols import __all__ as _protocols_all
    from .recipe import __all__ as _recipe_all
    from .results import __all__ as _results_all
    from .skill import __all__ as _skill_all

    __all__ = (
        _foundation_all
        + _install_all
        + _github_all
        + _skill_all
        + _audit_all
        + _recipe_all
        + _constants_all
        + _results_all
        + _execution_all
        + _launch_all
        + _context_admission_all
        + _protocols_all
    )
