"""Post-hoc anomaly detection over accumulated ProcSnapshot data.

Runs over the complete snapshot series at flush time, enabling multi-snapshot
pattern detection (e.g., sustained RSS growth, persistent zombies).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum


class AnomalyKind(StrEnum):
    OOM_SPIKE = "oom_spike"
    OOM_CRITICAL = "oom_critical"
    ZOMBIE_DETECTED = "zombie_detected"
    ZOMBIE_PERSISTENT = "zombie_persistent"
    SIGNALS_PENDING = "signals_pending"
    RSS_GROWTH = "rss_growth"
    FD_HIGH = "fd_high"
    D_STATE_SUSTAINED = "d_state_sustained"
    HIGH_CPU_SUSTAINED = "high_cpu_sustained"
    IDENTITY_DRIFT = "identity_drift"
    EMPTY_RESULT_WITH_TOKENS = "empty_result_with_tokens"
    THINKING_ONLY_FINAL_TURN = "thinking_only_final_turn"


class AnomalySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# Sentinel values for outcome anomalies (e.g. EMPTY_RESULT_WITH_TOKENS) that are not
# derived from a live ProcSnapshot.  Downstream consumers must treat these as "no
# snapshot" indicators rather than real pid/seq values.
OUTCOME_ANOMALY_PID_SENTINEL: int = 0
OUTCOME_ANOMALY_SEQ_SENTINEL: int = -1


# Kernel wait-channel values that are normal for a healthy process in
# uninterruptible sleep.  D-state snapshots whose wchan matches any of these
# are NOT counted toward the D_STATE_SUSTAINED threshold.
# "" and "0" guard against missing-data snapshots (unreadable /proc/wchan).
BENIGN_WCHANS: frozenset[str] = frozenset(
    {
        "do_nanosleep",
        "do_epoll_wait",
        "schedule_hrtimeout_range",
        "",  # /proc returns empty when unreadable — treat as benign
        "0",  # kernel reports literal "0" when thread is runnable
    }
)


def _safe_int(value: object, *, default: int) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (ValueError, TypeError):
        return default


def _anomaly(
    kind: AnomalyKind,
    severity: AnomalySeverity,
    detail: dict[str, object],
    snapshot: dict[str, object],
    seq: int,
    pid: int,
) -> dict[str, object]:
    return {
        "ts": datetime.now(UTC).isoformat(),
        "seq": seq,
        "event": "anomaly",
        "kind": str(kind),
        "severity": str(severity),
        "pid": pid,
        "detail": detail,
        "snapshot": snapshot,
    }


def detect_anomalies(
    snapshots: list[dict[str, object]],
    pid: int,
) -> list[dict[str, object]]:
    """Detect anomalies in a series of ProcSnapshot dicts.

    Returns a list of anomaly records, each with ts, seq, event, kind,
    severity, pid, detail, and snapshot fields.
    """
    anomalies: list[dict[str, object]] = []
    if not snapshots:
        return anomalies

    consecutive_zombie = 0
    consecutive_d_state = 0
    consecutive_high_cpu = 0
    prev_sig_pnd: str | None = None
    initial_rss: int | None = None

    for seq, snap in enumerate(snapshots):
        oom_score = snap.get("oom_score", -1)
        state = snap.get("state", "")
        sig_pnd = snap.get("sig_pnd", "")
        vm_rss_kb = snap.get("vm_rss_kb", 0)
        fd_count = snap.get("fd_count", 0)
        fd_soft_limit = snap.get("fd_soft_limit", 0)

        # OOM spike: delta > 200 between consecutive snapshots
        if seq > 0:
            prev_oom = snapshots[seq - 1].get("oom_score", -1)
            if isinstance(oom_score, int) and isinstance(prev_oom, int):
                delta = oom_score - prev_oom
                if delta > 200:
                    anomalies.append(
                        _anomaly(
                            AnomalyKind.OOM_SPIKE,
                            AnomalySeverity.WARNING,
                            {"from": prev_oom, "to": oom_score, "delta": delta},
                            snap,
                            seq,
                            pid,
                        )
                    )

        # OOM critical: oom_score >= 800
        if isinstance(oom_score, int) and oom_score >= 800:
            anomalies.append(
                _anomaly(
                    AnomalyKind.OOM_CRITICAL,
                    AnomalySeverity.CRITICAL,
                    {"oom_score": oom_score},
                    snap,
                    seq,
                    pid,
                )
            )

        # Zombie detection
        if state == "zombie":
            consecutive_zombie += 1
            if consecutive_zombie == 1:
                anomalies.append(
                    _anomaly(
                        AnomalyKind.ZOMBIE_DETECTED,
                        AnomalySeverity.WARNING,
                        {"state": state},
                        snap,
                        seq,
                        pid,
                    )
                )
            if consecutive_zombie == 3:
                anomalies.append(
                    _anomaly(
                        AnomalyKind.ZOMBIE_PERSISTENT,
                        AnomalySeverity.CRITICAL,
                        {"consecutive_count": consecutive_zombie},
                        snap,
                        seq,
                        pid,
                    )
                )
        else:
            consecutive_zombie = 0

        # D-state sustained: process stuck in uninterruptible sleep
        wchan = snap.get("wchan", "")
        if state == "disk-sleep" and isinstance(wchan, str) and wchan not in BENIGN_WCHANS:
            consecutive_d_state += 1
            if consecutive_d_state >= 2:
                anomalies.append(
                    _anomaly(
                        AnomalyKind.D_STATE_SUSTAINED,
                        AnomalySeverity.WARNING,
                        {
                            "state": state,
                            "wchan": wchan,
                            "consecutive_count": consecutive_d_state,
                        },
                        snap,
                        seq,
                        pid,
                    )
                )
        else:
            consecutive_d_state = 0

        # High-CPU sustained: process burning CPU >= 90% (suspected infinite loop)
        cpu_percent = snap.get("cpu_percent", 0.0)
        if isinstance(cpu_percent, (int, float)) and cpu_percent >= 90.0:
            consecutive_high_cpu += 1
            if consecutive_high_cpu >= 2:
                anomalies.append(
                    _anomaly(
                        AnomalyKind.HIGH_CPU_SUSTAINED,
                        AnomalySeverity.WARNING,
                        {
                            "cpu_percent": float(cpu_percent),
                            "consecutive_count": consecutive_high_cpu,
                        },
                        snap,
                        seq,
                        pid,
                    )
                )
        else:
            consecutive_high_cpu = 0

        # Signals pending: transition from all-zeros to non-zero
        _all_zeros = "0000000000000000"
        if isinstance(sig_pnd, str) and isinstance(prev_sig_pnd, str):
            if prev_sig_pnd == _all_zeros and sig_pnd != _all_zeros:
                anomalies.append(
                    _anomaly(
                        AnomalyKind.SIGNALS_PENDING,
                        AnomalySeverity.WARNING,
                        {"from": prev_sig_pnd, "to": sig_pnd},
                        snap,
                        seq,
                        pid,
                    )
                )
        prev_sig_pnd = str(sig_pnd) if sig_pnd else prev_sig_pnd

        # RSS growth tracking
        if isinstance(vm_rss_kb, int) and vm_rss_kb > 0:
            if initial_rss is None:
                initial_rss = vm_rss_kb

        # FD high ratio: fd_count / fd_soft_limit > 0.80
        if isinstance(fd_count, int) and isinstance(fd_soft_limit, int) and fd_soft_limit > 0:
            ratio = fd_count / fd_soft_limit
            if ratio > 0.80:
                anomalies.append(
                    _anomaly(
                        AnomalyKind.FD_HIGH,
                        AnomalySeverity.WARNING,
                        {
                            "fd_count": fd_count,
                            "fd_soft_limit": fd_soft_limit,
                            "ratio": round(ratio, 3),
                        },
                        snap,
                        seq,
                        pid,
                    )
                )

    # RSS growth: total growth exceeds 2x initial over 5+ snapshots
    if initial_rss is not None and initial_rss > 0 and len(snapshots) >= 5:
        last_rss = snapshots[-1].get("vm_rss_kb", 0)
        if isinstance(last_rss, int) and last_rss > 2 * initial_rss:
            anomalies.append(
                _anomaly(
                    AnomalyKind.RSS_GROWTH,
                    AnomalySeverity.WARNING,
                    {
                        "initial_rss_kb": initial_rss,
                        "final_rss_kb": last_rss,
                        "growth_factor": round(last_rss / initial_rss, 2),
                        "snapshot_count": len(snapshots),
                    },
                    snapshots[-1],
                    len(snapshots) - 1,
                    pid,
                )
            )

    return anomalies


def detect_identity_drift(
    snapshots: list[dict[str, object]],
    expected_comm: str,
) -> list[dict[str, object]]:
    """Detect process identity drift: snapshots whose comm != expected_comm.

    This is the architectural immunity check introduced in #806. If PTY wrapping
    somehow bypasses the TraceTarget resolver, every snapshot will carry the wrong
    comm (e.g., 'script' instead of 'claude'). This detector surfaces it immediately
    rather than letting wrong telemetry accumulate silently for months.

    Args:
        snapshots: List of snapshot dicts (each may have a 'comm' field).
        expected_comm: The process name we expect every snapshot to describe.

    Returns:
        List of IDENTITY_DRIFT anomaly records (one per first-seen mismatch).
    """
    anomalies: list[dict[str, object]] = []
    if not snapshots or not expected_comm:
        return anomalies

    for seq, snap in enumerate(snapshots):
        actual_comm = snap.get("comm", "")
        if actual_comm and actual_comm != expected_comm:
            anomalies.append(
                _anomaly(
                    AnomalyKind.IDENTITY_DRIFT,
                    AnomalySeverity.CRITICAL,
                    {
                        "expected_comm": expected_comm,
                        "actual_comm": actual_comm,
                        "seq": seq,
                    },
                    snap,
                    seq,
                    _safe_int(snap.get("pid"), default=0),
                )
            )
            # Report on first occurrence only — one anomaly is enough to diagnose the drift
            break

    return anomalies


def detect_outcome_anomalies(
    token_usage: dict[str, object],
    subtype: str,
    has_thinking_only_turn: bool = False,
) -> list[dict[str, object]]:
    """Detect outcome-level anomalies that require correlating session result with token usage.

    Detects:
    - THINKING_ONLY_FINAL_TURN: final turn contained only thinking blocks (no text/tool output)
    - EMPTY_RESULT_WITH_TOKENS: session produced output_tokens > 0 but subtype is 'empty_result'
    """
    anomalies: list[dict[str, object]] = []
    output_tokens = token_usage.get("output_tokens", 0)
    if has_thinking_only_turn and subtype == "empty_result":
        anomalies.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "seq": OUTCOME_ANOMALY_SEQ_SENTINEL,
                "event": "anomaly",
                "kind": str(AnomalyKind.THINKING_ONLY_FINAL_TURN),
                "severity": str(AnomalySeverity.WARNING),
                "pid": OUTCOME_ANOMALY_PID_SENTINEL,
                "detail": {"output_tokens": output_tokens, "subtype": subtype},
                "snapshot": {},
            }
        )
    elif isinstance(output_tokens, int) and output_tokens > 0 and subtype == "empty_result":
        anomalies.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "seq": OUTCOME_ANOMALY_SEQ_SENTINEL,
                "event": "anomaly",
                "kind": str(AnomalyKind.EMPTY_RESULT_WITH_TOKENS),
                "severity": str(AnomalySeverity.WARNING),
                "pid": OUTCOME_ANOMALY_PID_SENTINEL,
                "detail": {"output_tokens": output_tokens, "subtype": subtype},
                "snapshot": {},
            }
        )
    return anomalies
