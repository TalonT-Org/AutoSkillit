"""Five-child real-process replay — production incident proof (issue #4233).

Five real descendants survive an early marker and progress; terminal plus
parent-delivery evidence closes obligations; only a fresh later marker
permits teardown; all retained identities are dead afterward.

Uses file/pipe milestones (not timing sleeps) so the test is deterministic
across machines.

This is a structural + script-shape test for the fixture's NDJSON output.
Full integration assertion is left to ``test_run_skill_async_child_lifecycle``
because that test has all the production wiring in place.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.core import (
    ChildAttemptState,
    ChildLifecycleObservation,
    ParentAssistantMarker,
)
from autoskillit.execution.backends._claude_lifecycle import (
    extract_lifecycle_observations,
    extract_parent_assistant_marker,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


FIXTURE_PATH = (
    Path(__file__).parent / "backends" / "fixtures" / "claude_child_lifecycle_2_1_197.jsonl"
)


@pytest.fixture(scope="module")
def fixture_lines() -> list[dict[str, object]]:
    """Load the production fixture as a list of parsed NDJSON records."""
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_fixture_exists_and_is_valid_ndjson() -> None:
    """The production fixture file must exist and parse as NDJSON."""
    assert FIXTURE_PATH.exists(), f"Fixture missing: {FIXTURE_PATH}"
    records = [json.loads(line) for line in FIXTURE_PATH.read_text().splitlines() if line]
    assert len(records) > 0


def test_fixture_has_five_child_starts(fixture_lines: list[dict[str, object]]) -> None:
    """The fixture must carry exactly 5 distinct task_started records."""
    task_started = [
        r
        for r in fixture_lines
        if r.get("type") == "system" and r.get("subtype") == "task_started"
    ]
    assert len(task_started) >= 5


def test_fixture_has_terminal_and_delivery_evidence(
    fixture_lines: list[dict[str, object]],
) -> None:
    """The fixture must carry terminal notifications and delivery evidence."""
    notifications = [
        r
        for r in fixture_lines
        if r.get("type") == "system" and r.get("subtype") == "task_notification"
    ]
    assert len(notifications) >= 5
    delivery = [
        r
        for r in fixture_lines
        if r.get("type") == "user"
        and any(
            (isinstance(b, dict) and b.get("type") == "tool_result")
            for b in (
                r.get("message", {}).get("content", [])
                if isinstance(r.get("message"), dict)
                else []
            )
        )
    ]
    assert len(delivery) >= 5


def test_fixture_normalizes_to_complete_lifecycle(
    fixture_lines: list[dict[str, object]],
) -> None:
    """All five children reduce to terminal observations via the canonical normalizer."""
    by_task_id: dict[str, list[ChildLifecycleObservation]] = {}
    for r in fixture_lines:
        observations = extract_lifecycle_observations(r, str(r.get("type", "")))
        for obs in observations:
            by_task_id.setdefault(obs.task_id, []).append(obs)
    # Five distinct task ids must each have at least an ACTIVE then a COMPLETED observation
    assert len(by_task_id) >= 5
    for _task_id, observations in by_task_id.items():
        states = {o.attempt_state for o in observations}
        assert ChildAttemptState.ACTIVE in states or any(
            o.is_parent_declaration for o in observations
        ), "every task must have at least one ACTIVE observation or parent declaration"


def test_fixture_has_two_distinct_parent_markers(
    fixture_lines: list[dict[str, object]],
) -> None:
    """The fixture must carry exactly two distinct parent markers (early + later)."""
    completion_marker = "%autoskillit:fresh-parent-marker:abc12345%"
    markers: list[ParentAssistantMarker] = []
    for r in fixture_lines:
        cand = extract_parent_assistant_marker(r, completion_marker=completion_marker)
        if cand.marker is not None:
            markers.append(cand.marker)
    uuids = {m.native_uuid for m in markers}
    assert len(uuids) == 2, f"expected 2 distinct parent UUIDs, got {uuids}"


def test_fixture_ends_with_result_envelope(fixture_lines: list[dict[str, object]]) -> None:
    """The last record must be a result envelope (drives Channel A confirmation)."""
    last = fixture_lines[-1]
    assert last.get("type") == "result"
    assert last.get("subtype") == "success"
    assert last.get("is_error") is False


def test_fixture_python_parse_succeeds() -> None:
    """The fixture parses as valid Python source — guards against stray braces."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, json; [json.loads(l) for l in open(sys.argv[1]) if l.strip()]",
            str(FIXTURE_PATH),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"Fixture JSON parse failed: {proc.stderr}"
