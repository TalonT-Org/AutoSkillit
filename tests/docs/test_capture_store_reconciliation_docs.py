"""Verify docs/safety/hooks.md documents capture-store reconciliation.

The test ratchets documentation of the scan phase, its adoption gates, the
contention retry, and the stats/reclamation CLI so a future edit cannot
silently drop the section.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SAFETY_HOOKS = REPO_ROOT / "docs/safety/hooks.md"
DECISION_0006 = REPO_ROOT / "docs/decisions/0006-output-containment.md"

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]


def _extract_section(content: str, heading: str) -> str:
    level = heading.index(" ")
    pattern = re.compile(
        rf"^{re.escape(heading)}\n(.+?)(?=^#{{1,{level}}} |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    return match.group(1) if match else ""


@pytest.fixture(scope="module")
def hooks_md() -> str:
    return SAFETY_HOOKS.read_text(encoding="utf-8")


def test_hooks_md_has_scan_phase_section(hooks_md: str) -> None:
    assert "#### Directory-reconciliation scan phase" in hooks_md


def test_hooks_md_scan_phase_documents_budget_field(hooks_md: str) -> None:
    section = _extract_section(hooks_md, "#### Directory-reconciliation scan phase")
    assert "max_directory_entries_scanned" in section
    assert "_orphan_scan.py" in section
    assert ".orphan-scan-cursor" in section
    assert "publish_private_file" in section


@pytest.mark.parametrize(
    "gate_marker",
    [
        r"shell_\[0-9a-f\]\{16\}\\\.log",
        "lstat",
        "#4319",
        "#4321",
        "DELETING",
        "LEGACY_CLEANUP_ONLY",
        "#4440",
    ],
)
def test_hooks_md_scan_phase_documents_adoption_gates(hooks_md: str, gate_marker: str) -> None:
    section = _extract_section(hooks_md, "#### Directory-reconciliation scan phase")
    assert re.search(gate_marker, section), f"{gate_marker!r} not documented in scan-phase section"


def test_hooks_md_has_lock_contention_retry_section(hooks_md: str) -> None:
    assert "#### Bounded lock-contention retry" in hooks_md


def test_hooks_md_contention_section_documents_retry_mechanics(hooks_md: str) -> None:
    section = _extract_section(hooks_md, "#### Bounded lock-contention retry")
    for required in (
        "EAGAIN",
        "EWOULDBLOCK",
        "max_duration_seconds",
        "LOCK_CONTENDED",
        "no new configuration knob",
    ):
        assert required in section


def test_hooks_md_has_stats_and_reclamation_cli_section(hooks_md: str) -> None:
    assert "#### Stats and reclamation CLI" in hooks_md


def test_hooks_md_cli_section_documents_shared_adapter(hooks_md: str) -> None:
    section = _extract_section(hooks_md, "#### Stats and reclamation CLI")
    for required in (
        "capture_store_stats",
        "autoskillit capture-store",
        "--reclaim",
        "reconcile_capture_store",
    ):
        assert required in section


def test_decision_0006_acknowledges_orphan_and_contention_resolution() -> None:
    content = DECISION_0006.read_text(encoding="utf-8")
    section = _extract_section(content, "## Resolved")
    assert "orphan" in section.lower()
    assert "lock contention" in section.lower() or "contention" in section.lower()
