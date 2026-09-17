"""Committed fixtures for audit substitution probes and server-floor tests.

The historical incident (REQ-004/REQ-006/REQ-007) was preserved here as a
single source of truth so the regression suite never silently skips when a
runtime ``.autoskillit/temp/rectify/incident_evidence.txt`` snapshot is
unavailable.
"""

from __future__ import annotations

from autoskillit.core import ProbedRequirement

INCIDENT_REQUIREMENT: str = (
    "For a CPU-active child that never stops, child_deferral_ceiling=1.0, "
    "assert kill happens within ~1s of ceiling expiry."
)
INCIDENT_EVIDENCE: str = (
    "Uses a persistently-active child (_has_active_child_processes mocked to "
    "always return True) with child_deferral_ceiling=1.0."
)
LITERAL_EVIDENCE: str = (
    'Starts subprocess.Popen(["sh", "-c", "sleep 30 & wait"]) and asserts with '
    "psutil that the real child process remains active until termination."
)

INCIDENT_DIFF: str = '''\
"""Child-process liveness is simulated via a mock on
``_has_active_child_processes`` rather than relying on real psutil CPU-percent
sampling."""
+    monkeypatch.setattr(
+        _patch_process__termination,
+        "_has_active_child_processes",
+        lambda pid: True,
+    )
'''

INCIDENT_REQUIREMENTS: tuple[ProbedRequirement, ...] = (
    ProbedRequirement(
        requirement_id="REQ-004",
        requirement_text=(
            "child_deferral_ceiling must be honored exactly for any CPU-active "
            "child process until termination is requested."
        ),
        evidence_summary=(
            "The test monkeypatches _has_active_child_processes to always "
            "return True instead of observing real psutil CPU sampling."
        ),
    ),
    ProbedRequirement(
        requirement_id="REQ-006",
        requirement_text=(
            "For a CPU-active child that never stops, child_deferral_ceiling=1.0, "
            "assert kill happens within ~1s of ceiling expiry."
        ),
        evidence_summary=INCIDENT_EVIDENCE,
    ),
    ProbedRequirement(
        requirement_id="REQ-007",
        requirement_text=(
            "Real subprocess.Popen and psutil must drive the child-process "
            "liveness assertion end-to-end."
        ),
        evidence_summary=(
            "Test relies on a monkeypatched lambda rather than spawning a "
            "real sh subprocess and asserting psutil pid_exists."
        ),
    ),
)
