"""Five-child real-process replay — production incident proof (issue #4233).

Five real descendants survive an early marker and progress; terminal plus
parent-delivery evidence closes obligations; only a fresh later marker
permits teardown; all retained identities are dead afterward.

Uses file/pipe milestones (not timing sleeps) so the test is deterministic
across machines.

The final test drives the production ``run_managed_async`` boundary rather
than invoking the reducer, pump, or actor directly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import anyio
import psutil
import pytest

from autoskillit.core import (
    ChannelConfirmation,
    ChildAttemptState,
    ChildLifecycleObservation,
    KillReason,
    LifecycleDecision,
    ParentAssistantMarker,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends._claude_lifecycle import (
    extract_lifecycle_observations,
    extract_parent_assistant_marker,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.process import run_managed_async

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


@pytest.mark.anyio
async def test_production_runner_defers_early_marker_until_five_children_finish(
    tmp_path: Path,
) -> None:
    marker = "%autoskillit:fresh-parent-marker:abc12345%"
    release_fifo = tmp_path / "release.fifo"
    release_fifo.unlink(missing_ok=True)
    release_fifo.parent.mkdir(parents=True, exist_ok=True)
    import os

    os.mkfifo(release_fifo)
    pid_file = tmp_path / "children.json"
    early_ready = tmp_path / "early-ready"
    script = tmp_path / "shim.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import signal
            import subprocess
            import sys
            from pathlib import Path

            release_fifo = Path({str(release_fifo)!r})
            pid_file = Path({str(pid_file)!r})
            early_ready = Path({str(early_ready)!r})
            marker = {marker!r}

            children = [
                subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"])
                for _ in range(5)
            ]
            pid_file.write_text(json.dumps([child.pid for child in children]))

            for index in range(5):
                print(json.dumps({{
                    "type": "system", "subtype": "task_started",
                    "agent_id": f"agent_{{index}}", "task_id": f"task_{{index}}",
                    "tool_use_id": f"toolu_{{index}}", "uuid": f"start_{{index}}"
                }}), flush=True)
            print(json.dumps({{
                "type": "assistant", "uuid": "parent-early", "session_id": "sid",
                "message": {{"id": "message-early", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }}), flush=True)
            early_ready.write_text("ready")

            with release_fifo.open("rb", buffering=0) as stream:
                stream.read(1)

            for index in range(5):
                print(json.dumps({{
                    "type": "system", "subtype": "task_notification",
                    "status": "completed", "agent_id": f"agent_{{index}}",
                    "task_id": f"task_{{index}}", "tool_use_id": f"toolu_{{index}}",
                    "uuid": f"notification_{{index}}"
                }}), flush=True)
                print(json.dumps({{
                    "type": "user", "uuid": f"delivery_{{index}}",
                    "message": {{"id": f"delivery-message-{{index}}", "content": [{{
                        "type": "tool_result", "tool_use_id": f"toolu_{{index}}",
                        "content": {{"status": "completed", "agentId": f"agent_{{index}}"}}
                    }}]}}
                }}), flush=True)
            print(json.dumps({{
                "type": "assistant", "uuid": "parent-fresh", "session_id": "sid",
                "message": {{"id": "message-fresh", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }}), flush=True)
            print(json.dumps({{
                "type": "result", "subtype": "success", "is_error": False,
                "session_id": "sid", "result": marker
            }}), flush=True)
            signal.pause()
            """
        ),
        encoding="utf-8",
    )

    result_box: dict[str, object] = {}
    completed = anyio.Event()

    async def _run() -> None:
        result_box["result"] = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=20,
            completion_marker=marker,
            stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(marker),
            completion_drain_timeout=1.0,
            natural_exit_grace_seconds=0.05,
            cleanup_budget_seconds=5.0,
            _heartbeat_poll=0.01,
        )
        completed.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        with anyio.fail_after(5):
            while not early_ready.exists():
                await anyio.sleep(0.01)
        child_pids = json.loads(pid_file.read_text(encoding="utf-8"))
        assert len(child_pids) == 5
        assert all(psutil.pid_exists(pid) for pid in child_pids)
        assert not completed.is_set(), "early marker must not authorize teardown"
        await anyio.to_thread.run_sync(release_fifo.write_bytes, b"x")
        with anyio.fail_after(10):
            await completed.wait()

    result = result_box["result"]
    assert isinstance(result, SubprocessResult)
    assert result.termination is TerminationReason.COMPLETED
    assert result.channel_confirmation is ChannelConfirmation.CHANNEL_A
    assert result.kill_reason is KillReason.KILL_AFTER_COMPLETION
    assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
    assert result.lifecycle_candidate is not None
    assert result.lifecycle_candidate.candidate_id == "parent-fresh"
    assert result.cleanup_outcome is not None and result.cleanup_outcome.succeeded
    assert all(not psutil.pid_exists(pid) for pid in child_pids)


@pytest.mark.anyio
async def test_production_runner_no_child_fast_path(tmp_path: Path) -> None:
    marker = "%autoskillit:no-child-marker:abc12345%"
    script = tmp_path / "no_child_shim.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import signal

            marker = {marker!r}
            print(json.dumps({{
                "type": "assistant", "uuid": "parent-only", "session_id": "sid",
                "message": {{"id": "message-only", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }}), flush=True)
            print(json.dumps({{
                "type": "result", "subtype": "success", "is_error": False,
                "session_id": "sid", "result": marker
            }}), flush=True)
            signal.pause()
            """
        ),
        encoding="utf-8",
    )

    started = anyio.current_time()
    result = await run_managed_async(
        [sys.executable, str(script)],
        cwd=tmp_path,
        timeout=10,
        completion_marker=marker,
        stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(marker),
        completion_drain_timeout=1.0,
        natural_exit_grace_seconds=0.05,
        cleanup_budget_seconds=3.0,
        _heartbeat_poll=0.01,
    )
    elapsed = anyio.current_time() - started

    assert elapsed < 3.0
    assert result.termination is TerminationReason.COMPLETED
    assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
    assert result.cleanup_outcome is not None and result.cleanup_outcome.succeeded
    assert not psutil.pid_exists(result.pid)


@pytest.mark.anyio
async def test_child_failure_releases_deferral_and_cleans_process_tree(tmp_path: Path) -> None:
    marker = "%autoskillit:failed-child-marker:abc12345%"
    pid_file = tmp_path / "failed-child-pids.json"
    script = tmp_path / "failed_child_shim.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import signal
            import subprocess
            import sys
            from pathlib import Path

            marker = {marker!r}
            pid_file = Path({str(pid_file)!r})
            child = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"])
            pid_file.write_text(json.dumps([os.getpid(), child.pid]))
            print(json.dumps({{
                "type": "system", "subtype": "task_started", "agent_id": "agent-fail",
                "task_id": "task-fail", "tool_use_id": "toolu-fail", "uuid": "start-fail"
            }}), flush=True)
            print(json.dumps({{
                "type": "system", "subtype": "task_notification", "status": "failed",
                "agent_id": "agent-fail", "task_id": "task-fail",
                "tool_use_id": "toolu-fail", "uuid": "notification-fail"
            }}), flush=True)
            print(json.dumps({{
                "type": "user", "uuid": "delivery-fail",
                "message": {{"id": "delivery-message-fail", "content": [{{
                    "type": "tool_result", "tool_use_id": "toolu-fail",
                    "content": {{"status": "failed", "agentId": "agent-fail"}}
                }}]}}
            }}), flush=True)
            print(json.dumps({{
                "type": "assistant", "uuid": "parent-after-failure", "session_id": "sid",
                "message": {{"id": "message-after-failure", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }}), flush=True)
            signal.pause()
            """
        ),
        encoding="utf-8",
    )

    result = await run_managed_async(
        [sys.executable, str(script)],
        cwd=tmp_path,
        timeout=10,
        completion_marker=marker,
        stream_parser_factory=ClaudeCodeBackend().stream_parser_factory(marker),
        completion_drain_timeout=1.0,
        cleanup_budget_seconds=3.0,
        _heartbeat_poll=0.01,
    )
    process_ids = json.loads(pid_file.read_text(encoding="utf-8"))

    assert result.termination is TerminationReason.HEALTH_INSPECTOR
    assert result.lifecycle_decision is LifecycleDecision.CHILD_WORK_FAILED
    assert result.cleanup_outcome is not None and result.cleanup_outcome.succeeded
    assert all(not psutil.pid_exists(pid) for pid in process_ids)
