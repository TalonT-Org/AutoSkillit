"""L0 foundation sub-package: types, logging, and I/O primitives.

Re-exports the full public surface so callers can do
``from autoskillit.core import get_logger`` etc.  Submodules are loaded
lazily on first attribute access (PEP 562 via lazy-loader).
"""

import lazy_loader as lazy

__getattr__, _dir, __all__ = lazy.attach_stub(__name__, __file__)
del _dir  # replaced below so dir() reflects the filtered __all__

_PRIVATE_REEXPORTS = frozenset(
    {
        "_InstallLock",
        "_retire_old_versions",
        "_collect_disabled_feature_tags",
        "_AUTOSKILLIT_GITIGNORE_ENTRIES",
        "_COMMITTED_BY_DESIGN",
    }
)
__all__ = [n for n in __all__ if n not in _PRIVATE_REEXPORTS]


def __dir__() -> list[str]:
    return __all__
