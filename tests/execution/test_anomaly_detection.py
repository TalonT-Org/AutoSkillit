"""Tests for post-hoc anomaly detection over ProcSnapshot data."""

from __future__ import annotations

from autoskillit.execution.anomaly_detection import (
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
    assert len(anomalies) >= 1
    anomaly = anomalies[0]
    assert "ts" in anomaly
    assert "seq" in anomaly
    assert anomaly["event"] == "anomaly"
    assert "kind" in anomaly
    assert "severity" in anomaly
    assert "detail" in anomaly
    assert "snapshot" in anomaly
    assert anomaly["pid"] == 1234
