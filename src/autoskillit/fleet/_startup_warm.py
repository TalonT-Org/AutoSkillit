"""Eager imports for failure paths used by IL-2 and IL-3 entry points."""

from __future__ import annotations

from importlib import import_module

from autoskillit.core import get_logger

WARM_MODULE_NAMES: tuple[str, ...] = (
    "autoskillit.core",
    "autoskillit.execution",
    "autoskillit.execution.process._process_kill",
    "autoskillit.execution.session_log",
    "autoskillit.fleet",
    "autoskillit.fleet._label_cleanup",
    "autoskillit.fleet.state",
)

logger = get_logger(__name__)


def warm_failure_path_imports() -> None:
    """Import modules reached only while handling failures; never raise."""
    for module_name in WARM_MODULE_NAMES:
        try:
            import_module(module_name)
        except Exception:
            logger.debug("startup_warm_import_failed", module=module_name, exc_info=True)
