"""Public-API facade for fleet dispatch — keeps imports stable (REQ-IMP-001).

After the #4851 decomposition, the dispatch engine lives in
``fleet/dispatch/`` as a per-phase shard package. This module remains a thin
re-export layer so the public surface (``from autoskillit.fleet import
execute_dispatch, DispatchSpawnFailed, _write_pid``) and the test patch paths
(``monkeypatch.setattr('autoskillit.fleet._api.<sym>', ...)``) continue to
resolve unchanged.

Canonical re-export set (compiled via Step 17 grep audit):

* ``DispatchSpawnFailed``, ``_write_pid``, ``execute_dispatch`` — public triple
  (also reachable via ``fleet/__init__.py``).
* ``_run_dispatch`` — heavy patch target in tests/fleet/test_api.py
  (5 sites: lines 194, 225, 265, 300, 338).
* ``parse_l3_result_block`` — 60+ monkeypatch sites across the dispatch test
  suite.
* ``_extract_captures``, ``_normalize_capture_spec`` — re-exports of capture
  helpers that tests route through the dispatch facade.
* ``retain_dispatch_tracker_authority``, ``load_dispatch_progress``,
  ``bind_dispatch_launch_contract`` — re-exports of checkpoint-bridge helpers
  used by ``_run_dispatch``.
"""

from __future__ import annotations

from autoskillit.fleet._capture import _extract_captures, _normalize_capture_spec

# Public triple (issue #4851 / REQ-IMP-001)
from autoskillit.fleet._checkpoint_bridge import (
    bind_dispatch_launch_contract,
    load_dispatch_progress,
    retain_dispatch_tracker_authority,
)
from autoskillit.fleet.dispatch._api import (
    DispatchSpawnFailed,
    _run_dispatch,
    execute_dispatch,
)
from autoskillit.fleet.dispatch._pid import _write_pid
from autoskillit.fleet.result_parser import parse_l3_result_block

__all__ = [
    # Public triple
    "DispatchSpawnFailed",
    "execute_dispatch",
    "_write_pid",
    # Test patch surface
    "_run_dispatch",
    "parse_l3_result_block",
    "_extract_captures",
    "_normalize_capture_spec",
    "retain_dispatch_tracker_authority",
    "load_dispatch_progress",
    "bind_dispatch_launch_contract",
]
