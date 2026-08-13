"""Operator surface for registered-stdio AutoSkillit daemon orphans."""

from __future__ import annotations

import json
from typing import assert_never


def run_daemon_orphans(*, reap: bool = False, output_json: bool = False) -> None:
    """Report candidates by default and mutate only when ``reap`` is explicit."""
    from autoskillit.execution import (
        find_orphaned_autoskillit_daemons,
        reap_orphaned_autoskillit_daemons,
    )

    candidates = find_orphaned_autoskillit_daemons()
    if output_json:
        results = reap_orphaned_autoskillit_daemons(candidates) if reap else []
        print(
            json.dumps(
                {
                    "candidates": [
                        {
                            "pid": candidate.pid,
                            "launch_id": candidate.launch_id,
                            "state_root": candidate.state_root,
                            "boot_id": candidate.boot_id,
                            "starttime_ticks": candidate.starttime_ticks,
                            "owner_pid": candidate.owner_pid,
                        }
                        for candidate in candidates
                    ],
                    "results": [
                        {
                            "pid": result.pid,
                            "action": result.action,
                            "survivor_pids": list(result.survivor_pids),
                            "access_denied_pids": list(result.access_denied_pids),
                        }
                        for result in results
                    ],
                },
                indent=2,
            )
        )
        return

    if not candidates:
        print("no orphaned AutoSkillit daemons")
        return
    for candidate in candidates:
        print(
            f"orphan: pid={candidate.pid} launch={candidate.launch_id}"
            f" owner_pid={candidate.owner_pid} state_root={candidate.state_root}",
            flush=reap,
        )
    if not reap:
        print("run again with --reap to terminate (session registry data is not deleted)")
        return
    results = reap_orphaned_autoskillit_daemons(candidates)
    for result in results:
        if result.action == "terminated":
            print(f"terminated pid {result.pid}")
        elif result.action == "skipped":
            print(f"skipped pid {result.pid} (identity or orphan evidence changed)")
        elif result.action == "incomplete":
            details: list[str] = []
            if result.survivor_pids:
                details.append(f"survivors: {', '.join(map(str, result.survivor_pids))}")
            if result.access_denied_pids:
                details.append(f"access denied: {', '.join(map(str, result.access_denied_pids))}")
            print(f"incomplete pid {result.pid} ({'; '.join(details)})")
        else:
            assert_never(result.action)
