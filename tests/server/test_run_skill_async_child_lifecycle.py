"""Real run_skill proof for async-child-aware completion (issue #4233)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import anyio
import psutil
import pytest

from autoskillit.core import LifecycleDecision, SubprocessResult
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


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


@pytest.mark.anyio
async def test_run_skill_waits_for_child_then_records_tracked_write(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    target = repo / "src" / "lifecycle_probe.txt"
    target.write_text("before\n", encoding="utf-8")
    _git(repo, "init", "-b", "impl-lifecycle-test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    release_fifo = tmp_path / "release.fifo"
    os.mkfifo(release_fifo)
    child_pid_file = tmp_path / "child.pid"
    early_ready = tmp_path / "early-ready"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "claude"
    shim_impl = bin_dir / "claude_impl.py"
    shim_impl.write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import re
            import signal
            import subprocess
            import sys
            from pathlib import Path

            prompt = " ".join(sys.argv[1:])
            match = re.search(r"%%ORDER_UP::[0-9a-f]{{8}}%%", prompt)
            if match is None:
                raise SystemExit("completion marker missing from prompt")
            marker = match.group(0)
            release_fifo = Path({str(release_fifo)!r})
            child_pid_file = Path({str(child_pid_file)!r})
            early_ready = Path({str(early_ready)!r})
            target = Path({str(target)!r})

            child = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"])
            child_pid_file.write_text(str(child.pid))
            print(json.dumps({{
                "type": "system", "subtype": "init", "session_id": "run-skill-sid"
            }}), flush=True)
            print(json.dumps({{
                "type": "system", "subtype": "task_started", "agent_id": "agent-1",
                "task_id": "task-1", "tool_use_id": "toolu_1", "uuid": "start-1"
            }}), flush=True)
            print(json.dumps({{
                "type": "assistant", "uuid": "parent-early", "session_id": "run-skill-sid",
                "message": {{"id": "message-early", "content": [
                    {{"type": "text", "text": marker}}
                ]}}
            }}), flush=True)
            early_ready.write_text("ready")

            with release_fifo.open("rb", buffering=0) as stream:
                stream.read(1)
            target.write_text("after\\n")
            print(json.dumps({{
                "type": "system", "subtype": "task_notification", "status": "completed",
                "agent_id": "agent-1", "task_id": "task-1", "tool_use_id": "toolu_1",
                "uuid": "notification-1"
            }}), flush=True)
            print(json.dumps({{
                "type": "user", "uuid": "delivery-1", "message": {{
                    "id": "delivery-message", "content": [{{
                        "type": "tool_result", "tool_use_id": "toolu_1",
                        "content": {{"status": "completed", "agentId": "agent-1"}}
                    }}]
                }}
            }}), flush=True)
            print(json.dumps({{
                "type": "assistant", "uuid": "parent-fresh", "session_id": "run-skill-sid",
                "message": {{"id": "message-fresh", "content": [
                    {{"type": "text", "text": marker}},
                    {{"type": "tool_use", "id": "write-1", "name": "Write",
                     "input": {{"file_path": str(target)}}}}
                ]}}
            }}), flush=True)
            result = "\\n".join([
                marker,
                f"worktree_path = {{Path.cwd()}}",
                "branch_name = impl-lifecycle-test",
                "has_implementation_progress = true",
            ])
            print(json.dumps({{
                "type": "result", "subtype": "success", "is_error": False,
                "session_id": "run-skill-sid", "result": result
            }}), flush=True)
            signal.pause()
            """
        ),
        encoding="utf-8",
    )
    shim.write_text(
        "#!/bin/bash\n"
        f"exec -a claude {shlex.quote(sys.executable)} "
        f'{shlex.quote(str(shim_impl))} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    runner = _CapturingRunner()
    tool_ctx_kitchen_open.runner = runner
    tool_ctx_kitchen_open.backend = ClaudeCodeBackend()
    tool_ctx_kitchen_open.executor = DefaultHeadlessExecutor(tool_ctx_kitchen_open)
    tool_ctx_kitchen_open.config.run_skill.timeout = 20

    response_box: dict[str, str] = {}
    done = anyio.Event()

    async def _invoke() -> None:
        response_box["json"] = await run_skill(
            "/probe lifecycle",
            str(repo),
            output_dir=".",
            order_id="async-child-lifecycle-test",
        )
        done.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_invoke)
        with anyio.fail_after(8):
            while not early_ready.exists():
                if done.is_set():
                    pytest.fail(
                        "run_skill returned before the lifecycle shim started: "
                        f"{response_box['json']}; command={runner.last_command!r}; "
                        f"PATH={runner.last_path!r}"
                    )
                await anyio.sleep(0.01)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert psutil.pid_exists(child_pid)
        assert not done.is_set(), "run_skill returned on the early parent marker"
        await anyio.to_thread.run_sync(release_fifo.write_bytes, b"x")
        with anyio.fail_after(12):
            await done.wait()

    response = json.loads(response_box["json"])
    assert response["success"] is True
    assert "branch_name = impl-lifecycle-test" in response["result"]
    assert response["write_path_warnings"] == []
    assert target.read_text(encoding="utf-8") == "after\n"
    assert _git(repo, "diff", "--", "src/lifecycle_probe.txt")
    assert runner.last_result is not None
    assert runner.last_result.lifecycle_decision is LifecycleDecision.ELIGIBLE
    assert runner.last_result.cleanup_outcome is not None
    assert runner.last_result.cleanup_outcome.succeeded
    assert not psutil.pid_exists(child_pid)
