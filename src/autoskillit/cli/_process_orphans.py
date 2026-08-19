"""CLI runner for the ``autoskillit process-orphans`` command.

Report-default / mutate-flag shape mirrors ``_codex_orphans.py``/``_daemon_orphans.py``.
This is the manual/ops entry point ``doctor``'s tether check points operators at —
the automatic path is the tether sweep wired into every boot/open chokepoint
(``server/_lifespan.py``, ``open_kitchen``, cook startup).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime


def run_process_orphans(*, reap: bool = False, output_json: bool = False) -> None:
    """Report tether-tracked children whose guardian is dead or ceiling has passed.

    ``--reap`` invokes the same identity-verified sweep the automatic
    chokepoints use — no separate kill path exists.
    """
    from autoskillit.execution import (
        default_tether_dir,
        find_orphaned_tethers,
        format_orphaned_tether_fields,
    )

    tether_dir = default_tether_dir()
    orphaned = find_orphaned_tethers(tether_dir)

    if output_json:
        report = None
        if reap:
            from autoskillit.execution import sweep_orphaned_tethers

            report = sweep_orphaned_tethers(tether_dir)
        doc = {
            "orphans": [
                {
                    "tether_path": o.tether_path,
                    "child_pid": o.record.child_pid,
                    "origin": o.record.origin,
                    "reason": o.reason,
                    "not_after": datetime.fromtimestamp(o.record.not_after, tz=UTC).isoformat(),
                }
                for o in orphaned
            ],
            "swept": (
                [
                    {
                        "tether_path": out.tether_path,
                        "child_pid": out.child_pid,
                        "outcome": out.outcome,
                    }
                    for out in report.outcomes
                ]
                if report is not None
                else []
            ),
        }
        print(json.dumps(doc, indent=2))
        return

    if not orphaned:
        print("no orphaned process tethers")
        return

    # Print target lines before invoking the sweep so the operator record of
    # what was targeted exists even if signaling wedges.
    for o in orphaned:
        print(
            f"orphan: {format_orphaned_tether_fields(o.record, o.reason)}",
            flush=reap,
        )

    if not reap:
        print("run again with --reap to terminate and remove the stale tethers")
        return

    from autoskillit.execution import sweep_orphaned_tethers

    report = sweep_orphaned_tethers(tether_dir)
    for outcome in report.outcomes:
        if outcome.outcome in ("reaped_orphan", "reaped_ceiling"):
            print(f"terminated pid {outcome.child_pid} ({outcome.outcome})")
        elif outcome.outcome == "kill_failed":
            print(f"incomplete pid {outcome.child_pid} (kill did not confirm death)")
