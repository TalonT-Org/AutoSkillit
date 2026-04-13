"""Tests for post-hoc anomaly detection over ProcSnapshot data."""

from __future__ import annotations

import pytest

from autoskillit.execution.anomaly_detection import (
    BENIGN_WCHANS,
    AnomalyKind,
    AnomalySeverity,
    detect_anomalies,
)


def _snap(
    *,
    state: str = "sleeping",
    vm_rss_kb: int = 100000,
    oom_score: int = 50,
    fd_count: int = 10,
    fd_soft_limit: int = 1024,
    sig_pnd: str = "0000000000000000",
    sig_blk: str = "0000000000000000",
    sig_cgt: str = "0000000000000000",
    threads: int = 4,
    wchan: str = "",
    ctx_switches_voluntary: int = 500,
    ctx_switches_involuntary: int = 20,
    cpu_percent: float = 0.0,
) -> dict[str, object]:
    return {
        "state": state,
        "vm_rss_kb": vm_rss_kb,
        "oom_score": oom_score,
        "fd_count": fd_count,
        "fd_soft_limit": fd_soft_limit,
        "sig_pnd": sig_pnd,
        "sig_blk": sig_blk,
        "sig_cgt": sig_cgt,
        "threads": threads,
        "wchan": wchan,
        "ctx_switches_voluntary": ctx_switches_voluntary,
        "ctx_switches_involuntary": ctx_switches_involuntary,
        "cpu_percent": cpu_percent,
    }


def test_detect_oom_spike():
    """OOM score delta > 200 between consecutive snapshots."""
    snaps = [_snap(oom_score=100), _snap(oom_score=350)]
    anomalies = detect_anomalies(snaps, pid=1234)
    oom_spikes = [a for a in anomalies if a["kind"] == AnomalyKind.OOM_SPIKE]
    assert len(oom_spikes) == 1
    assert oom_spikes[0]["detail"]["delta"] == 250


def test_detect_oom_critical():
    """OOM score >= 800 triggers critical anomaly."""
    snaps = [_snap(oom_score=850)]
    anomalies = detect_anomalies(snaps, pid=1234)
    oom_critical = [a for a in anomalies if a["kind"] == AnomalyKind.OOM_CRITICAL]
    assert len(oom_critical) == 1
    assert oom_critical[0]["severity"] == AnomalySeverity.CRITICAL


def test_detect_zombie_state():
    """Snapshot with state='zombie' triggers zombie_detected."""
    snaps = [_snap(state="zombie")]
    anomalies = detect_anomalies(snaps, pid=1234)
    zombies = [a for a in anomalies if a["kind"] == AnomalyKind.ZOMBIE_DETECTED]
    assert len(zombies) == 1


def test_detect_zombie_persistent():
    """Three consecutive zombie snapshots trigger zombie_persistent."""
    snaps = [_snap(state="zombie") for _ in range(3)]
    anomalies = detect_anomalies(snaps, pid=1234)
    persistent = [a for a in anomalies if a["kind"] == AnomalyKind.ZOMBIE_PERSISTENT]
    assert len(persistent) == 1
    assert persistent[0]["severity"] == AnomalySeverity.CRITICAL


def test_detect_signals_pending():
    """sig_pnd transitions from all-zeros to non-zero."""
    snaps = [
        _snap(sig_pnd="0000000000000000"),
        _snap(sig_pnd="0000000000000004"),
    ]
    anomalies = detect_anomalies(snaps, pid=1234)
    sig_pending = [a for a in anomalies if a["kind"] == AnomalyKind.SIGNALS_PENDING]
    assert len(sig_pending) == 1


def test_detect_rss_growth():
    """RSS grows > 2x initial over 5+ snapshots."""
    snaps = [
        _snap(vm_rss_kb=100000),
        _snap(vm_rss_kb=120000),
        _snap(vm_rss_kb=150000),
        _snap(vm_rss_kb=180000),
        _snap(vm_rss_kb=250000),
    ]
    anomalies = detect_anomalies(snaps, pid=1234)
    rss_growth = [a for a in anomalies if a["kind"] == AnomalyKind.RSS_GROWTH]
    assert len(rss_growth) == 1
    assert rss_growth[0]["detail"]["growth_factor"] == 2.5


def test_detect_fd_high_ratio():
    """fd_count / fd_soft_limit > 0.80 triggers fd_high anomaly."""
    # High ratio: 850/1024 = 0.83
    snaps = [_snap(fd_count=850, fd_soft_limit=1024)]
    anomalies = detect_anomalies(snaps, pid=1234)
    fd_high = [a for a in anomalies if a["kind"] == AnomalyKind.FD_HIGH]
    assert len(fd_high) == 1

    # Low ratio: 500/65535 = 0.008 — no anomaly
    snaps = [_snap(fd_count=500, fd_soft_limit=65535)]
    anomalies = detect_anomalies(snaps, pid=1234)
    fd_high = [a for a in anomalies if a["kind"] == AnomalyKind.FD_HIGH]
    assert len(fd_high) == 0


def test_no_anomalies_for_normal_session():
    """Realistic stable session produces no anomalies."""
    snaps = [_snap(vm_rss_kb=100000 + i * 100, oom_score=50) for i in range(10)]
    anomalies = detect_anomalies(snaps, pid=1234)
    assert len(anomalies) == 0


def test_anomaly_record_structure():
    """Each anomaly record has the required fields."""
    snaps = [_snap(oom_score=900)]
    anomalies = detect_anomalies(snaps, pid=1234)
    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert "ts" in anomaly
    assert "seq" in anomaly
    assert anomaly["event"] == "anomaly"
    assert "kind" in anomaly
    assert "severity" in anomaly
    assert "detail" in anomaly
    assert "snapshot" in anomaly
    assert anomaly["pid"] == 1234


# ---------------------------------------------------------------------------
# REQ-ENUM-001, REQ-ENUM-002
# ---------------------------------------------------------------------------


def test_anomaly_kind_has_d_state_sustained():
    """AnomalyKind.D_STATE_SUSTAINED is defined with the correct string value."""
    assert AnomalyKind.D_STATE_SUSTAINED == "d_state_sustained"


def test_anomaly_kind_has_high_cpu_sustained():
    """AnomalyKind.HIGH_CPU_SUSTAINED is defined with the correct string value."""
    assert AnomalyKind.HIGH_CPU_SUSTAINED == "high_cpu_sustained"


def test_benign_wchans_is_central_frozenset():
    """BENIGN_WCHANS is a frozenset containing the three issue-cited values."""
    assert isinstance(BENIGN_WCHANS, frozenset)
    assert "do_nanosleep" in BENIGN_WCHANS
    assert "do_epoll_wait" in BENIGN_WCHANS
    assert "schedule_hrtimeout_range" in BENIGN_WCHANS


def test_d_state_sustained_fires_on_two_consecutive():
    """Two consecutive disk-sleep snapshots with a non-benign wchan fire exactly one anomaly."""
    snaps = [
        _snap(state="disk-sleep", wchan="ext4_file_write_iter"),
        _snap(state="disk-sleep", wchan="ext4_file_write_iter"),
    ]
    anomalies = detect_anomalies(snaps, pid=999)
    d_state = [a for a in anomalies if a["kind"] == AnomalyKind.D_STATE_SUSTAINED]
    assert len(d_state) == 1
    assert d_state[0]["seq"] == 1


def test_d_state_sustained_no_fire_on_single_snapshot():
    """A single disk-sleep snapshot yields zero D_STATE_SUSTAINED anomalies."""
    snaps = [_snap(state="disk-sleep", wchan="ext4_file_write_iter")]
    anomalies = detect_anomalies(snaps, pid=999)
    d_state = [a for a in anomalies if a["kind"] == AnomalyKind.D_STATE_SUSTAINED]
    assert len(d_state) == 0


def test_d_state_sustained_counter_resets_on_intervening_sleep():
    """Sequence [disk-sleep, sleeping, disk-sleep] yields zero D_STATE_SUSTAINED anomalies."""
    snaps = [
        _snap(state="disk-sleep", wchan="ext4_file_write_iter"),
        _snap(state="sleeping"),
        _snap(state="disk-sleep", wchan="ext4_file_write_iter"),
    ]
    anomalies = detect_anomalies(snaps, pid=999)
    d_state = [a for a in anomalies if a["kind"] == AnomalyKind.D_STATE_SUSTAINED]
    assert len(d_state) == 0


# ---------------------------------------------------------------------------
# REQ-WCHAN-002, REQ-TEST-005 — benign wchans are skipped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wchan", ["do_nanosleep", "do_epoll_wait", "schedule_hrtimeout_range"])
def test_d_state_sustained_benign_wchan_skipped(wchan: str) -> None:
    """Two disk-sleep snapshots with a benign wchan yield no D_STATE_SUSTAINED anomaly."""
    snaps = [
        _snap(state="disk-sleep", wchan=wchan),
        _snap(state="disk-sleep", wchan=wchan),
    ]
    anomalies = detect_anomalies(snaps, pid=999)
    d_state = [a for a in anomalies if a["kind"] == AnomalyKind.D_STATE_SUSTAINED]
    assert len(d_state) == 0


def test_d_state_sustained_detail_includes_wchan():
    """The emitted D_STATE_SUSTAINED anomaly detail contains the triggering wchan value."""
    wchan_val = "ext4_file_write_iter"
    snaps = [
        _snap(state="disk-sleep", wchan=wchan_val),
        _snap(state="disk-sleep", wchan=wchan_val),
    ]
    anomalies = detect_anomalies(snaps, pid=999)
    d_state = [a for a in anomalies if a["kind"] == AnomalyKind.D_STATE_SUSTAINED]
    assert len(d_state) == 1
    assert d_state[0]["detail"]["wchan"] == wchan_val


def test_high_cpu_sustained_fires_on_two_consecutive():
    """Two consecutive snapshots with cpu_percent=95.0 produce exactly one HIGH_CPU_SUSTAINED."""
    snaps = [_snap(cpu_percent=95.0), _snap(cpu_percent=95.0)]
    anomalies = detect_anomalies(snaps, pid=999)
    high_cpu = [a for a in anomalies if a["kind"] == AnomalyKind.HIGH_CPU_SUSTAINED]
    assert len(high_cpu) == 1


def test_high_cpu_sustained_no_fire_on_single_snapshot():
    """A single high-CPU snapshot yields zero HIGH_CPU_SUSTAINED anomalies."""
    snaps = [_snap(cpu_percent=95.0)]
    anomalies = detect_anomalies(snaps, pid=999)
    high_cpu = [a for a in anomalies if a["kind"] == AnomalyKind.HIGH_CPU_SUSTAINED]
    assert len(high_cpu) == 0


def test_high_cpu_sustained_fires_at_exact_threshold():
    """cpu_percent=90.0 fires; cpu_percent=89.9 does not."""
    snaps_at = [_snap(cpu_percent=90.0), _snap(cpu_percent=90.0)]
    anomalies_at = detect_anomalies(snaps_at, pid=999)
    high_cpu_at = [a for a in anomalies_at if a["kind"] == AnomalyKind.HIGH_CPU_SUSTAINED]
    assert len(high_cpu_at) == 1

    snaps_below = [_snap(cpu_percent=89.9), _snap(cpu_percent=89.9)]
    anomalies_below = detect_anomalies(snaps_below, pid=999)
    high_cpu_below = [a for a in anomalies_below if a["kind"] == AnomalyKind.HIGH_CPU_SUSTAINED]
    assert len(high_cpu_below) == 0


def test_high_cpu_sustained_counter_resets_on_intervening_idle():
    """Sequence [95%, 10%, 95%] yields zero HIGH_CPU_SUSTAINED anomalies."""
    snaps = [_snap(cpu_percent=95.0), _snap(cpu_percent=10.0), _snap(cpu_percent=95.0)]
    anomalies = detect_anomalies(snaps, pid=999)
    high_cpu = [a for a in anomalies if a["kind"] == AnomalyKind.HIGH_CPU_SUSTAINED]
    assert len(high_cpu) == 0


def test_high_cpu_sustained_detail_includes_cpu_percent():
    """The emitted HIGH_CPU_SUSTAINED anomaly detail contains the cpu_percent value."""
    snaps = [_snap(cpu_percent=95.5), _snap(cpu_percent=95.5)]
    anomalies = detect_anomalies(snaps, pid=999)
    high_cpu = [a for a in anomalies if a["kind"] == AnomalyKind.HIGH_CPU_SUSTAINED]
    assert len(high_cpu) == 1
    assert high_cpu[0]["detail"]["cpu_percent"] == 95.5


def test_no_anomalies_for_normal_session_still_holds():
    """Normal session with cpu_percent=0.0 default still produces no anomalies."""
    snaps = [_snap(vm_rss_kb=100000 + i * 100, oom_score=50) for i in range(10)]
    anomalies = detect_anomalies(snaps, pid=1234)
    assert len(anomalies) == 0


# ---------------------------------------------------------------------------
# Test 1.7 — Anomaly-detection liveness canary
# ---------------------------------------------------------------------------


def test_anomaly_canary_fires_on_synthetic_stream():
    """detect_anomalies fires OOM_CRITICAL on a stream with oom_score=100→900 spike.

    Test 1.7: liveness canary — fails loudly if anomaly detection is silently gutted.
    Universal anomaly_count==0 across production sessions was unreadable before #806.
    """
    snaps = [
        _snap(oom_score=100),
        _snap(oom_score=900),  # exceeds OOM_CRITICAL threshold (>=800)
    ]
    anomalies = detect_anomalies(snaps, pid=999)
    oom_critical = [a for a in anomalies if a["kind"] == AnomalyKind.OOM_CRITICAL]
    assert len(oom_critical) >= 1, (
        "detect_anomalies must fire OOM_CRITICAL when oom_score >= 800. "
        "If this test fails, anomaly detection has been silently disabled."
    )


# ---------------------------------------------------------------------------
# Test 1.8 — Identity-drift detection fires when comm disagrees
# ---------------------------------------------------------------------------


def test_identity_drift_anomaly_fires_when_comm_mismatches():
    """detect_identity_drift fires IDENTITY_DRIFT when snap.comm != expected_comm.

    Test 1.8: architectural immunity check. If PTY wrapping ever bypasses the
    resolver, the drift detector surfaces it rather than letting it rot for months.
    """
    from autoskillit.execution.anomaly_detection import AnomalyKind, detect_identity_drift

    # Use plain dicts (as flush_session_log passes to detect_anomalies)
    # Simulate: expected process is 'claude' but every snapshot has comm='script'
    snap_dicts = [{"comm": "script", "vm_rss_kb": 2048, "oom_score": 10} for _ in range(2)]
    anomalies = detect_identity_drift(snap_dicts, expected_comm="claude")
    assert len(anomalies) >= 1, (
        "detect_identity_drift must fire when snap.comm != expected_comm. "
        "This is the architectural immunity check for PTY wrapper tracer drift."
    )
    drift_anomalies = [a for a in anomalies if a["kind"] == AnomalyKind.IDENTITY_DRIFT]
    assert drift_anomalies, (
        f"Expected IDENTITY_DRIFT anomaly kind. Got: {[a['kind'] for a in anomalies]}"
    )


def test_identity_drift_kind_exists():
    """AnomalyKind.IDENTITY_DRIFT must exist as a StrEnum member."""
    assert hasattr(AnomalyKind, "IDENTITY_DRIFT"), (
        "AnomalyKind must have IDENTITY_DRIFT member for PTY wrapper drift detection"
    )
    assert AnomalyKind.IDENTITY_DRIFT == "identity_drift"
