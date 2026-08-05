"""``autoskillit capture-store`` — stats and one-time bulk reclamation.

Companion to the doctor battery's read-only capture-store check
(``cli/doctor/_doctor_capture_store.py``): both call
``hooks._capture._reconcile.capture_store_stats`` so neither surface can
drift from what a real reconciliation pass would find. ``--reclaim`` is the
one-time bulk path for pre-existing debris — the SessionStart sweep's
directory-reconciliation scan phase keeps new debris from ever accumulating
again, so this command exists for backlog already on disk, not as a
standing operational dependency.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.hooks._capture import _reconcile as _capture_reconcile
from autoskillit.hooks._capture._types import CleanupBlocker, CleanupProgress, SweepBudgetSpec

# Generous relative to the standing budgets (RUNNER_TAIL_BUDGET,
# SESSION_START_BUDGET): this command runs once, deliberately, from a
# terminal — not on every command or session start — so it can afford
# seconds-scale passes and a large per-pass ceiling on every dimension.
RECLAIM_BUDGET = SweepBudgetSpec(
    max_records_inspected=4096,
    max_replay_bytes=4 * 1024 * 1024,
    max_attempts=1024,
    max_transitions=4096,
    max_cursor_writes=1024,
    max_duration_seconds=5.0,
    max_directory_entries_scanned=4096,
)

# Hard circuit-breaker, not a tuning knob: a clean pass (no due records, no
# adoptable orphans) breaks out long before this many iterations for any
# backlog size this command is meant for; it exists only to guarantee
# termination if something is genuinely wrong (e.g. persistent errors this
# loop doesn't otherwise detect as fatal).
_MAX_RECLAIM_PASSES = 5000


def _print_stats(stats: _capture_reconcile.CaptureStoreStats) -> None:
    if stats.blocker is CleanupBlocker.STORE_ABSENT:
        print("capture-store: no store yet — nothing has been captured in this project")
        return
    if stats.blocker is not CleanupBlocker.NONE:
        print(f"capture-store: stats unavailable (blocker={stats.blocker.value})")
        return
    print(
        "capture-store ledger: "
        f"live={stats.live_records} eligible={stats.eligible_records} "
        f"deleting={stats.deleting_records} ledger_bytes={stats.ledger_bytes}"
    )
    print(
        "capture-store directory: "
        f"files={stats.directory_files} unledgered_aged={stats.unledgered_aged_files} "
        f"unledgered_aged_bytes={stats.unledgered_aged_bytes}"
    )


def run_capture_store(*, reclaim: bool = False) -> None:
    """Report capture-store ledger/directory statistics, or reclaim with ``reclaim=True``."""
    project_cwd = str(Path.cwd())
    stats = _capture_reconcile.capture_store_stats(project_cwd)
    _print_stats(stats)
    if not reclaim:
        return
    if stats.blocker not in (CleanupBlocker.NONE, CleanupBlocker.STORE_ABSENT):
        print(f"capture-store: cannot reclaim — {stats.blocker.value}")
        return
    print("capture-store: reclaiming...")
    converged = False
    for pass_index in range(1, _MAX_RECLAIM_PASSES + 1):
        outcome = _capture_reconcile.reconcile_capture_store(project_cwd, RECLAIM_BUDGET)
        print(
            f"  pass {pass_index}: deleted={outcome.deleted} transitions={outcome.transitions} "
            f"remaining_due={outcome.remaining_due} errors={outcome.errors} "
            f"blocker={outcome.blocker.value}"
        )
        if outcome.errors:
            print(
                "capture-store: reclaim stopped — error this pass "
                f"(blocker={outcome.blocker.value})"
            )
            break
        if outcome.remaining_due == 0 and outcome.progress is CleanupProgress.NONE:
            converged = True
            break
    else:
        print(
            f"capture-store: reclaim stopped after {_MAX_RECLAIM_PASSES} "
            "passes without convergence"
        )
    if converged:
        print("capture-store: converged")
    _print_stats(_capture_reconcile.capture_store_stats(project_cwd))
