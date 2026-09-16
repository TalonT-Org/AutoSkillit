"""CLI runner for the ``autoskillit codex-orphans`` command.

Report-default / mutate-flag shape mirrors ``_capture_store.py``.
Doctor Check 44 (``_check_orphaned_codex_processes``) surfaces the same
orphans read-only; this command adds the ``--reap`` mutating path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from autoskillit.execution import CodexOrphanReapResult, OrphanedCodexProcess


def _codex_orphans_json_document(
    orphans: list[OrphanedCodexProcess],
    results: list[CodexOrphanReapResult],
) -> dict[str, object]:
    return {
        "orphans": [
            {
                "pid": orphan.pid,
                "fd0_target": orphan.fd0_target,
                "exe_target": orphan.exe_target,
                "started_at": datetime.fromtimestamp(orphan.started_at, tz=UTC).isoformat(),
            }
            for orphan in orphans
        ],
        "reaped": [
            {
                "pid": result.pid,
                "action": result.action,
                "observation_complete": result.observation_complete,
                "survivor_pids": list(result.survivor_pids),
                "access_denied_pids": list(result.access_denied_pids),
            }
            for result in results
        ],
    }


def _render_codex_reap_results(results: list[CodexOrphanReapResult]) -> None:
    for result in results:
        if result.action == "terminated":
            print(f"terminated pid {result.pid}")
        elif result.action == "skipped":
            print(f"skipped pid {result.pid} (no longer matches the orphan signature)")
        elif result.action == "incomplete":
            parts: list[str] = []
            if result.survivor_pids:
                parts.append(f"survivors: {', '.join(str(p) for p in result.survivor_pids)}")
            if result.access_denied_pids:
                parts.append(
                    f"access denied: {', '.join(str(p) for p in result.access_denied_pids)}"
                )
            if not result.observation_complete:
                parts.append("observation incomplete")
            print(f"incomplete pid {result.pid} ({'; '.join(parts)})")
        else:
            assert_never(result.action)


def run_codex_orphans(*, reap: bool = False, output_json: bool = False) -> None:
    """Report orphaned codex TUI processes, or terminate them with ``--reap``."""
    from autoskillit.execution import (
        find_orphaned_codex_processes,
        reap_orphaned_codex_processes,
    )

    orphans = find_orphaned_codex_processes()

    if output_json:
        results = reap_orphaned_codex_processes(orphans) if reap else []
        print(json.dumps(_codex_orphans_json_document(orphans, results), indent=2))
        return

    if not orphans:
        print("no orphaned codex processes")
        return

    # Print target lines before invoking the reaper so the operator record of
    # what was targeted exists even if signaling wedges.
    for o in orphans:
        iso = datetime.fromtimestamp(o.started_at, tz=UTC).isoformat()
        print(
            f"orphan: pid={o.pid} started={iso} fd0={o.fd0_target} exe={o.exe_target or '?'}",
            flush=reap,
        )

    if not reap:
        print(
            "run again with --reap to terminate"
            " (persisted session data is not deleted; resume later with codex resume)"
        )
        return

    results = reap_orphaned_codex_processes(orphans)
    _render_codex_reap_results(results)
