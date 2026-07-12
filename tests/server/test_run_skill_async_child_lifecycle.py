"""Real run_skill proof for async-child-aware completion (issue #4233)."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import anyio
import psutil
import pytest

from autoskillit.core import (
    ChannelConfirmation,
    CompletionCandidateSource,
    KillReason,
    LifecycleDecision,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.linux_tracing import read_starttime_ticks
from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


SpawnedIdentity = tuple[int, int, float]


class _CapturingRunner(DefaultSubprocessRunner):
    def __init__(self) -> None:
        self.last_result: SubprocessResult | None = None
        self.last_command: tuple[str, ...] = ()
        self.last_path = ""

    async def __call__(self, *args, **kwargs) -> SubprocessResult:
        self.last_command = tuple(args[0])
        self.last_path = str(kwargs.get("env", {}).get("PATH", ""))
        self.last_result = await super().__call__(*args, **kwargs)
        return self.last_result


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _identity_is_alive(identity: SpawnedIdentity) -> bool:
    pid, starttime_ticks, fallback_create_time = identity
    if starttime_ticks > 0:
        return read_starttime_ticks(pid) == starttime_ticks
    try:
        return psutil.Process(pid).create_time() == fallback_create_time
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _load_identities(identity_file: Path) -> dict[int, SpawnedIdentity]:
    if not identity_file.is_file():
        return {}
    records = json.loads(identity_file.read_text(encoding="utf-8"))
    return {
        int(pid): (int(pid), int(starttime_ticks), float(create_time))
        for pid, starttime_ticks, create_time in records
    }


async def _cleanup_matching_identities(identities: dict[int, SpawnedIdentity]) -> None:
    """Best-effort fallback that never signals an identity-mismatched PID."""
    ordered = tuple(reversed(tuple(identities.values())))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for identity in ordered:
            if not _identity_is_alive(identity):
                continue
            try:
                os.kill(identity[0], sig)
            except ProcessLookupError:
                pass
        with anyio.move_on_after(1.0):
            while any(_identity_is_alive(identity) for identity in ordered):
                await anyio.sleep(0.01)
        if not any(_identity_is_alive(identity) for identity in ordered):
            return


@pytest.mark.anyio
async def test_run_skill_waits_for_child_then_records_tracked_write(
    tool_ctx,
    tmp_path: Path,
    monkeypatch,
) -> None:
    dynamic_id = uuid.uuid4().hex[:10]
    fixture_source_repo = tmp_path / "fixture_source_repo"
    fixture_impl_worktree = tmp_path / "fixture_impl_worktree"
    fixture_source_repo.mkdir()
    (fixture_source_repo / "src").mkdir()
    source_target = fixture_source_repo / "src" / "lifecycle_probe.txt"
    source_target.write_text("before\n", encoding="utf-8")
    (fixture_source_repo / ".gitignore").write_text(".autoskillit/\n", encoding="utf-8")
    _git(fixture_source_repo, "init", "-b", "main")
    _git(fixture_source_repo, "config", "user.email", "test@example.com")
    _git(fixture_source_repo, "config", "user.name", "Test")
    _git(fixture_source_repo, "add", ".")
    _git(fixture_source_repo, "commit", "-m", "baseline")

    fixture_branch = f"impl-lifecycle-{dynamic_id}"
    _git(
        fixture_source_repo,
        "worktree",
        "add",
        "-b",
        fixture_branch,
        str(fixture_impl_worktree),
        "main",
    )
    fixture_target = fixture_impl_worktree / "src" / "lifecycle_probe.txt"
    plan_dir = fixture_impl_worktree / ".autoskillit" / "temp" / "make-plan"
    plan_dir.mkdir(parents=True)
    fixture_plan = plan_dir / f"lifecycle_plan_{dynamic_id}.md"
    fixture_plan.write_text(
        "Dry-walkthrough verified = TRUE\n\n# Linked worktree lifecycle proof\n",
        encoding="utf-8",
    )

    source_main_sha = _git(fixture_source_repo, "rev-parse", "main")
    source_status_before = _git(fixture_source_repo, "status", "--short")
    impl_sha_before = _git(fixture_impl_worktree, "rev-parse", "HEAD")
    impl_status_before = _git(fixture_impl_worktree, "status", "--short")
    assert source_main_sha == impl_sha_before
    assert source_status_before == ""
    assert impl_status_before == ""

    identity_file = tmp_path / f"captured-identities-{dynamic_id}.json"
    early_ready = tmp_path / f"early-ready-{dynamic_id}"
    child_progress = tmp_path / f"child-progress-{dynamic_id}"
    child_release = tmp_path / f"child-release-{dynamic_id}"
    bin_dir = tmp_path / f"bin-{dynamic_id}"
    bin_dir.mkdir()
    child_impl = bin_dir / "progress_child.py"
    child_impl.write_text(
        textwrap.dedent(
            """
            import sys
            import time
            from pathlib import Path

            early_ready = Path(sys.argv[1])
            progress = Path(sys.argv[2])
            release = Path(sys.argv[3])
            deadline = time.monotonic() + 30
            while not early_ready.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("early marker milestone timed out")
                time.sleep(0.01)
            progress.write_text("progressed-after-early-marker", encoding="utf-8")
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("child release milestone timed out")
                time.sleep(0.01)
            """
        ),
        encoding="utf-8",
    )

    shim = bin_dir / "claude"
    shim_impl = bin_dir / "claude_impl.py"
    shim_impl.write_text(
        textwrap.dedent(
            f"""
            import ctypes
            import json
            import os
            import re
            import subprocess
            import sys
            import time
            from pathlib import Path

            import psutil

            if sys.platform == "linux":
                ctypes.CDLL(None).prctl(15, b"claude", 0, 0, 0)

            identity_file = Path({str(identity_file)!r})
            early_ready = Path({str(early_ready)!r})
            child_progress = Path({str(child_progress)!r})
            child_release = Path({str(child_release)!r})
            child_impl = Path({str(child_impl)!r})
            python_executable = Path({sys.executable!r})

            prompt = " ".join(sys.argv[1:])
            marker_match = re.search(
                r"%%ORDER_UP::[0-9a-f]{{8}}%%",
                os.environ.get("AUTOSKILLIT_COMPLETION_MARKER", "") or prompt,
            )
            if marker_match is None:
                raise SystemExit("completion marker missing from prompt")
            marker = marker_match.group(0)
            marker_id = marker.removeprefix("%%ORDER_UP::").removesuffix("%%")
            session_id = f"run-skill-{{marker_id}}"
            agent_id = f"agent-{{marker_id}}"
            task_id = f"task-{{marker_id}}"
            tool_use_id = f"toolu-{{marker_id}}"

            plan_match = re.search(r"(/[^\\s]+\\.md)", prompt)
            if plan_match is None:
                raise SystemExit("absolute plan path missing from prompt")
            plan = Path(plan_match.group(1)).resolve()
            cwd = Path.cwd().resolve()
            if plan.parent != cwd / ".autoskillit" / "temp" / "make-plan":
                raise SystemExit(f"plan is not in linked worktree: {{plan}}")
            target = cwd / "src" / "lifecycle_probe.txt"
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=cwd,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()

            def identity(pid):
                stat_path = Path(f"/proc/{{pid}}/stat")
                starttime_ticks = 0
                if stat_path.is_file():
                    stat = stat_path.read_text(encoding="utf-8")
                    starttime_ticks = int(stat.rsplit(")", 1)[1].split()[19])
                return [pid, starttime_ticks, psutil.Process(pid).create_time()]

            child = subprocess.Popen([
                str(python_executable),
                str(child_impl),
                str(early_ready),
                str(child_progress),
                str(child_release),
            ])
            identity_file.write_text(
                json.dumps([identity(os.getpid()), identity(child.pid)]),
                encoding="utf-8",
            )
            print(json.dumps({{
                "type": "system", "subtype": "init", "session_id": session_id
            }}), flush=True)
            print(json.dumps({{
                "type": "system", "subtype": "task_started", "agent_id": agent_id,
                "task_id": task_id, "tool_use_id": tool_use_id,
                "uuid": f"declaration-{{marker_id}}"
            }}), flush=True)
            print(json.dumps({{
                "type": "assistant", "uuid": f"parent-early-{{marker_id}}",
                "session_id": session_id,
                "message": {{"id": f"message-early-{{marker_id}}", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }}), flush=True)
            early_ready.write_text("ready", encoding="utf-8")

            deadline = time.monotonic() + 30
            while not child_progress.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("child progress milestone timed out")
                time.sleep(0.01)
            print(json.dumps({{
                "type": "system", "subtype": "task_progress", "agent_id": agent_id,
                "task_id": task_id, "tool_use_id": tool_use_id,
                "uuid": f"progress-{{marker_id}}"
            }}), flush=True)
            while not child_release.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("child release milestone timed out")
                time.sleep(0.01)
            child.wait(timeout=5)

            target.write_text("after\\n", encoding="utf-8")
            print(json.dumps({{
                "type": "assistant", "uuid": f"write-{{marker_id}}",
                "session_id": session_id,
                "message": {{"id": f"message-write-{{marker_id}}", "content": [{{
                    "type": "tool_use", "id": f"write-tool-{{marker_id}}", "name": "Write",
                    "input": {{"file_path": str(target), "content": "after\\n"}}
                }}]}}
            }}), flush=True)
            print(json.dumps({{
                "type": "system", "subtype": "task_notification", "status": "completed",
                "agent_id": agent_id, "task_id": task_id, "tool_use_id": tool_use_id,
                "uuid": f"terminal-{{marker_id}}"
            }}), flush=True)
            print(json.dumps({{
                "type": "user", "uuid": f"delivery-{{marker_id}}",
                "message": {{"id": f"delivery-message-{{marker_id}}", "content": [{{
                    "type": "tool_result", "tool_use_id": tool_use_id,
                    "content": {{"status": "completed", "agentId": agent_id}}
                }}]}}
            }}), flush=True)
            result_text = "\\n".join([
                marker,
                f"worktree_path = {{cwd}}",
                f"branch_name = {{branch}}",
                "has_implementation_progress = true",
            ])
            print(json.dumps({{
                "type": "assistant", "uuid": f"parent-fresh-{{marker_id}}",
                "session_id": session_id,
                "message": {{"id": f"message-fresh-{{marker_id}}", "content": [
                    {{"type": "text", "text": result_text}}
                ]}}
            }}), flush=True)
            print(json.dumps({{
                "type": "result", "subtype": "success", "is_error": False,
                "session_id": session_id, "result": result_text
            }}), flush=True)
            while True:
                time.sleep(1)
            """
        ),
        encoding="utf-8",
    )
    shim.write_text(
        f'#!/bin/bash\nexec {shlex.quote(sys.executable)} {shlex.quote(str(shim_impl))} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    assert isinstance(tool_ctx.backend, ClaudeCodeBackend)
    assert isinstance(tool_ctx.executor, DefaultHeadlessExecutor)
    assert tool_ctx.skill_resolver is not None
    assert tool_ctx.session_skill_manager is not None
    runner = _CapturingRunner()
    tool_ctx.runner = runner
    tool_ctx.gate.enable()
    tool_ctx.config.run_skill.timeout = 40

    response_box: dict[str, str] = {}
    done = anyio.Event()
    captured_identities: dict[int, SpawnedIdentity] = {}
    skill_command = f"/autoskillit:implement-worktree-no-merge {fixture_plan.resolve()}"

    async def _invoke() -> None:
        response_box["json"] = await run_skill(
            skill_command,
            str(fixture_impl_worktree),
            output_dir=str(fixture_impl_worktree),
            order_id=f"async-child-lifecycle-{dynamic_id}",
        )
        done.set()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_invoke)
            with anyio.fail_after(20):
                while not child_progress.exists():
                    if done.is_set():
                        pytest.fail(
                            "run_skill returned before post-early child progress: "
                            f"{response_box['json']}; command={runner.last_command!r}; "
                            f"PATH={runner.last_path!r}"
                        )
                    await anyio.sleep(0.01)
            captured_identities.update(_load_identities(identity_file))
            assert len(captured_identities) == 2
            assert all(_identity_is_alive(identity) for identity in captured_identities.values())
            assert child_progress.read_text(encoding="utf-8") == "progressed-after-early-marker"
            assert not done.is_set(), "run_skill returned on the early parent marker"
            child_release.write_text("release", encoding="utf-8")
            with anyio.fail_after(20):
                await done.wait()

        response = json.loads(response_box["json"])
        assert response["success"] is True
        assert f"worktree_path = {fixture_impl_worktree}" in response["result"]
        assert f"branch_name = {fixture_branch}" in response["result"]
        assert response["write_path_warnings"] == []
        assert response["write_call_count"] == 1
        assert fixture_target.read_text(encoding="utf-8") == "after\n"

        stdout_records = (
            [
                json.loads(line)
                for line in runner.last_result.stdout.splitlines()
                if line.lstrip().startswith("{")
            ]
            if runner.last_result is not None
            else []
        )
        subtypes = [record.get("subtype") for record in stdout_records]
        assert subtypes.count("task_started") == 1
        assert subtypes.count("task_progress") == 1
        assert subtypes.count("task_notification") == 1
        delivery_records = [record for record in stdout_records if record.get("type") == "user"]
        assert len(delivery_records) == 1
        write_blocks = [
            block
            for record in stdout_records
            if record.get("type") == "assistant"
            for block in record.get("message", {}).get("content", [])
            if isinstance(block, dict) and block.get("name") == "Write"
        ]
        assert len(write_blocks) == 1
        assert write_blocks[0]["input"] == {
            "file_path": str(fixture_target),
            "content": "after\n",
        }

        impl_sha_after = _git(fixture_impl_worktree, "rev-parse", "HEAD")
        impl_diff = _git(fixture_impl_worktree, "diff", "--", "src/lifecycle_probe.txt")
        impl_status_after = _git(fixture_impl_worktree, "status", "--short")
        assert impl_sha_after == impl_sha_before
        assert "-before" in impl_diff and "+after" in impl_diff
        assert impl_status_after == "M src/lifecycle_probe.txt"
        assert _git(fixture_impl_worktree, "branch", "--show-current") == fixture_branch

        assert _git(fixture_source_repo, "rev-parse", "main") == source_main_sha
        assert _git(fixture_source_repo, "status", "--short") == source_status_before
        assert source_target.read_text(encoding="utf-8") == "before\n"
        assert _git(fixture_source_repo, "diff", "--", "src/lifecycle_probe.txt") == ""

        result = runner.last_result
        assert result is not None
        assert result.termination is TerminationReason.COMPLETED
        assert result.channel_confirmation is ChannelConfirmation.CHANNEL_A
        assert result.kill_reason is KillReason.KILL_AFTER_COMPLETION
        assert result.lifecycle_decision is LifecycleDecision.ELIGIBLE
        assert result.eligible_source is CompletionCandidateSource.CHANNEL_A
        assert result.lifecycle_candidate is not None
        assert result.lifecycle_candidate.sources == (CompletionCandidateSource.CHANNEL_A,)
        assert tuple(sighting.source for sighting in result.sightings) == (
            CompletionCandidateSource.CHANNEL_A,
            CompletionCandidateSource.CHANNEL_A,
        )
        snapshot = result.lifecycle_snapshot
        assert snapshot is not None
        assert snapshot.active_children == ()
        assert snapshot.awaiting_delivery == ()
        assert snapshot.unresolved_terminal == ()
        assert len(snapshot.completed_children) == 1
        completed = snapshot.completed_children[0]
        assert (completed.task_id, completed.agent_id, completed.tool_use_id) == (
            f"task-{result.session_id.removeprefix('run-skill-')}",
            f"agent-{result.session_id.removeprefix('run-skill-')}",
            f"toolu-{result.session_id.removeprefix('run-skill-')}",
        )
        assert result.cleanup_outcome is not None
        assert result.cleanup_outcome.succeeded
        assert result.cleanup_outcome.retained_identities == ()
        assert result.cleanup_outcome.unknown_identities == ()
        assert all(not _identity_is_alive(identity) for identity in captured_identities.values())
    finally:
        captured_identities.update(_load_identities(identity_file))
        await _cleanup_matching_identities(captured_identities)
