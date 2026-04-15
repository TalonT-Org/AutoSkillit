"""Shared test helper utilities."""

from __future__ import annotations

import sys


def _flush_structlog_proxy_caches() -> None:
    """Reconnect autoskillit module-level loggers to the current structlog config.

    Scans ALL module attributes (not just 'logger'/'_logger') so that loggers
    stored under any name (e.g. '_log' in execution.quota) are repaired.
    """
    import structlog
    import structlog._config as _sc

    current_procs = structlog.get_config()["processors"]
    for mod_name in list(sys.modules):
        if not mod_name.startswith("autoskillit"):
            continue
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for lg in vars(mod).values():
            if isinstance(lg, _sc.BoundLoggerLazyProxy):
                lg.__dict__.pop("bind", None)
            elif hasattr(lg, "_processors"):
                lg._processors = current_procs
