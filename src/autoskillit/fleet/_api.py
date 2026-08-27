"""Public-API facade for the fleet dispatch engine (REQ-IMP-001).

After the #4851 decomposition, the dispatch engine lives in
``fleet/dispatch/`` as a per-phase shard package. This module re-exports the
stable symbols so callers can keep doing ``from autoskillit.fleet._api import
...`` and so test patches via
``monkeypatch.setattr('autoskillit.fleet._api.<sym>', ...)`` are honoured —
the dispatch internals resolve these names through this facade at call time.
"""

from __future__ import annotations

from autoskillit.fleet._capture import _extract_captures, _normalize_capture_spec
from autoskillit.fleet._checkpoint_bridge import (
    bind_dispatch_launch_contract,
    load_dispatch_progress,
    retain_dispatch_tracker_authority,
)
from autoskillit.fleet._native_shell_capture import resolve_dispatch_timeout
from autoskillit.fleet.dispatch._api import (
    DispatchSpawnFailed,
    _run_dispatch,
    execute_dispatch,
)
from autoskillit.fleet.dispatch._pid import _write_pid
from autoskillit.fleet.result_parser import parse_l3_result_block

__all__ = [
    "DispatchSpawnFailed",
    "execute_dispatch",
    "_write_pid",
    "_run_dispatch",
    "parse_l3_result_block",
    "_extract_captures",
    "_normalize_capture_spec",
    "retain_dispatch_tracker_authority",
    "load_dispatch_progress",
    "bind_dispatch_launch_contract",
    "resolve_dispatch_timeout",
]
