"""Shared type vocabulary and execution identity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._type_dimensions import *  # noqa: F403
from ._type_dimensions import __all__ as _dimensions_all
from ._type_enums import *  # noqa: F403
from ._type_enums import __all__ as _enums_all
from ._type_enums_context_admission import *  # noqa: F403
from ._type_enums_context_admission import __all__ as _enums_context_admission_all
from ._type_exceptions import *  # noqa: F403
from ._type_exceptions import __all__ as _exceptions_all
from ._type_execution_identity import *  # noqa: F403
from ._type_execution_identity import __all__ as _execution_identity_all
from ._type_exploration import *  # noqa: F403
from ._type_exploration import __all__ as _exploration_all

if not TYPE_CHECKING:
    __all__ = (
        _dimensions_all
        + _enums_all
        + _enums_context_admission_all
        + _exceptions_all
        + _execution_identity_all
        + _exploration_all
    )
