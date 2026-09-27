"""Context-admission value and persistence contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._type_context_admission import *  # noqa: F403
from ._type_context_admission import __all__ as _context_admission_all
from ._type_context_admission_persistence import *  # noqa: F403
from ._type_context_admission_persistence import __all__ as _context_admission_persistence_all
from ._type_context_admission_persistence_envelope import *  # noqa: F403
from ._type_context_admission_persistence_envelope import (
    __all__ as _context_admission_persistence_envelope_all,
)

if not TYPE_CHECKING:
    __all__ = (
        _context_admission_all
        + _context_admission_persistence_all
        + _context_admission_persistence_envelope_all
    )
