"""CLI runner for the ``autoskillit codex-orphans`` command.

Report-default / mutate-flag shape mirrors ``_capture_store.py``.
Doctor Check 44 (``_check_orphaned_codex_processes``) surfaces the same
orphans read-only; this command adds the ``--reap`` mutating path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime


def run_codex_orphans(*, reap: bool = False, output_json: bool = False) -> None:
    """Report orphaned codex TUI processes, or terminate them with ``--reap``."""
    from autoskillit.execution import (
        find_orphaned_codex_processes,
        reap_orphaned_codex_processes,
    )

    orphans = find_orphaned_codex_processes()

    if output_json:
        results = reap_orphaned_codex_processes(orphans) if reap else []
        doc = {
            "orphans": [
                {
                    "pid": o.pid,
                    "fd0_target": o.fd0_target,
                    "exe_target": o.exe_target,
                    "started_at": datetime.fromtimestamp(o.started_at, tz=UTC).isoformat(),
                }
                for o in orphans
            ],
            "reaped": [
                {
                    "pid": r.pid,
                    "action": r.action,
                    "survivor_pids": list(r.survivor_pids),
                    "access_denied_pids": list(r.access_denied_pids),
                }
                for r in results
            ],
        }
        print(json.dumps(doc, indent=2))
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
    for r in results:
        if r.action == "terminated":
            print(f"terminated pid {r.pid}")
        elif r.action == "skipped":
            print(f"skipped pid {r.pid} (no longer matches the orphan signature)")
        elif r.action == "incomplete":
            parts: list[str] = []
            if r.survivor_pids:
                parts.append(f"survivors: {', '.join(str(p) for p in r.survivor_pids)}")
            if r.access_denied_pids:
                parts.append(f"access denied: {', '.join(str(p) for p in r.access_denied_pids)}")
            print(f"incomplete pid {r.pid} ({'; '.join(parts)})")
