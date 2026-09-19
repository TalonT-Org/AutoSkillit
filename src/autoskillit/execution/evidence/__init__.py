"""execution/evidence/ — runtime observability signals collected for post-hoc diagnosis.

Re-exports anomaly_detection and linux_tracing. otlp_sink is deliberately not re-exported:
it imports session_log, and session_log.session_log imports anomaly_detection from here.
"""

from autoskillit.execution.evidence.anomaly_detection import (
    AnomalyKind,
    AnomalySeverity,
    detect_anomalies,
)
from autoskillit.execution.evidence.linux_tracing import (
    LINUX_TRACING_AVAILABLE,
    LinuxTracingHandle,
    ProcSnapshot,
    read_boot_id,
    read_starttime_ticks,
    start_linux_tracing,
)

__all__ = [
    "detect_anomalies",
    "AnomalyKind",
    "AnomalySeverity",
    "LINUX_TRACING_AVAILABLE",
    "LinuxTracingHandle",
    "ProcSnapshot",
    "read_boot_id",
    "read_starttime_ticks",
    "start_linux_tracing",
]
