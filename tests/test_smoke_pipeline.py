"""Smoke-test pipeline: structural validation and end-to-end execution tests.

Exercises the full orchestration path: script loading, step routing, tool
dispatch, capture/context threading, retry logic, bugfix loop pattern, and merge.

**Running tests:**

- Structural tests (no API): ``task test-all`` (included automatically)
- Smoke execution test (requires API): ``task test-smoke``
  - Requires ``ANTHROPIC_API_KEY`` in the environment
  - Expected duration: ~60-90 seconds
  - Excluded from ``task test-all`` to avoid API costs in routine development
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from autoskillit import server
from autoskillit.config import AutomationConfig, TestCheckConfig
from autoskillit.recipe.io import builtin_recipes_dir
from autoskillit.server import (
    classify_fix,
    list_recipes,
    load_recipe,
    merge_worktree,
    run_cmd,
    run_python,
    run_skill,
    run_skill_retry,
    test_check,
    validate_recipe,
)

test_check.__test__ = False  # type: ignore[attr-defined]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SCRIPT = builtin_recipes_dir() / "smoke-test.yaml"

_TOOL_MAP = {
    "run_cmd": run_cmd,
    "run_skill": run_skill,
    "run_skill_retry": run_skill_retry,
    "test_check": test_check,
    "merge_worktree": merge_worktree,
    "classify_fix": classify_fix,
}


class SmokeExecutor:
    """Minimal pipeline executor for smoke-test validation.

    Parses YAML step definitions, interpolates context variables, dispatches
    to server tools, captures results, and routes based on success/failure/on_result.
    """

    def __init__(self, steps: dict, inputs: dict) -> None:
        self.steps = steps
        self.inputs = inputs
        self.context: dict[str, str] = {}
        self.visited: list[str] = []

    async def run(self, start: str = "setup", max_steps: int = 30) -> tuple[str | None, str]:
        """Execute the pipeline from *start*, returning (terminal_step, message)."""
        current: str | None = start
        for _ in range(max_steps):
            if current is None:
                return None, "Routing returned None — pipeline stalled."
            self.visited.append(current)
            step_def = self.steps[current]

            if step_def.get("action") == "stop":
                return current, step_def.get("message", "")

            if step_def.get("action") == "route":
                current = step_def.get("on_success")
                continue

            if "python" in step_def:
                result = await self._execute_python(step_def)
                current = self._route(step_def, result)
                continue

            result = await self._execute(step_def)
            self._capture(step_def, result)
            current = self._route(step_def, result)
        return None, "Max steps exceeded."

    async def _execute(self, step_def: dict) -> dict:
        """Dispatch to the appropriate tool, handling retries."""
        tool_name = step_def["tool"]
        raw_args = step_def.get("with", {})
        args = self._interpolate(raw_args)

        if "retry" in step_def:
            return await self._run_with_retry(step_def, args)

        tool_fn = _TOOL_MAP[tool_name]
        raw_result = await tool_fn(**args)
        return json.loads(raw_result)

    async def _execute_python(self, step_def: dict) -> dict:
        """Dispatch a python: step to the run_python MCP tool."""
        raw_args = step_def.get("with", {})
        args = self._interpolate(raw_args)
        callable_path = step_def["python"]
        raw_result = await run_python(callable=callable_path, args=args)
        return json.loads(raw_result)

    async def _run_with_retry(self, step_def: dict, args: dict) -> dict:
        """Execute a tool with retry logic based on the retry block."""
        retry = step_def["retry"]
        max_attempts = retry.get("max_attempts", 3)
        retry_field = retry["on"]
        tool_fn = _TOOL_MAP[step_def["tool"]]

        # Always execute at least once; max_attempts controls additional retries.
        # max_attempts=0: run once, if retry_field fires → exhausted immediately.
        # max_attempts=N: run once, then retry up to N-1 more times.
        raw_result = await tool_fn(**args)
        result = json.loads(raw_result)
        if not result.get(retry_field, False):
            return result  # succeeded on first try

        for _ in range(max_attempts - 1):
            raw_result = await tool_fn(**args)
            result = json.loads(raw_result)
            if not result.get(retry_field, False):
                return result

        return result  # exhausted — always defined

    def _interpolate(self, with_args: dict) -> dict:
        """Resolve ${{ inputs.X }} and ${{ context.X }} references."""
        resolved = {}
        for key, value in with_args.items():
            if isinstance(value, str):
                resolved[key] = re.sub(
                    r"\$\{\{\s*(inputs|context)\.(\w+)\s*\}\}",
                    lambda m: (self.inputs if m.group(1) == "inputs" else self.context)[
                        m.group(2)
                    ],
                    value,
                )
            else:
                resolved[key] = value
        return resolved

    def _capture(self, step_def: dict, result: dict) -> None:
        """Extract key=value pairs from result text into context."""
        capture = step_def.get("capture", {})
        if not capture:
            return
        result_text = result.get("result", "")
        if isinstance(result_text, str):
            for ctx_key, pattern in capture.items():
                match = re.search(r"\$\{\{\s*result\.(\w+)\s*\}\}", pattern)
                if match:
                    field = match.group(1)
                    kv_match = re.search(rf"(?:^|\n)\s*{field}\s*[=:]\s*(.+)", result_text)
                    if kv_match:
                        self.context[ctx_key] = kv_match.group(1).strip()

    def _route(self, step_def: dict, result: dict) -> str | None:
        """Determine the next step based on result and routing rules."""
        if "on_result" in step_def:
            on_result = step_def["on_result"]
            field = on_result["field"]
            value = result.get(field)
            routes = on_result.get("routes", {})
            if value in routes:
                return routes[value]
            return step_def.get("on_failure")

        # If a retry block declared on_exhausted and the retry condition still fires,
        # route to on_exhausted rather than on_failure.
        retry = step_def.get("retry")
        if retry and retry.get("on_exhausted") and result.get(retry.get("on", ""), False):
            return retry["on_exhausted"]

        if self._is_success(step_def, result):
            return step_def.get("on_success")
        return step_def.get("on_failure")

    def _is_success(self, step_def: dict, result: dict) -> bool:
        """Tool-specific success predicate."""
        tool = step_def["tool"]
        if tool == "test_check":
            return result.get("passed", False)
        if tool in ("merge_worktree", "classify_fix"):
            return "error" not in result
        return result.get("success", True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def smoke_script_path() -> Path:
    return SMOKE_SCRIPT


@pytest.fixture()
def smoke_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp project dir for smoke tests.

    Bundled recipes (including smoke-test) are discovered via recipe_parser,
    so no recipe files need to be copied into the project dir.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def smoke_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "smoke_ws"
    ws.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=ws,
        check=True,
        capture_output=True,
        env=env,
    )
    (ws / ".autoskillit-workspace").touch()
    return ws


# ---------------------------------------------------------------------------
# Structural Validation Tests (no API required)
# ---------------------------------------------------------------------------


class TestSmokeScriptValidation:
    """Validate the smoke-test pipeline YAML structure and executor logic."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for smoke script validation tests."""

    async def test_script_validates(self, smoke_script_path: Path) -> None:
        result = json.loads(await validate_recipe(script_path=str(smoke_script_path)))
        assert result["valid"] is True
        assert result["errors"] == []

    async def test_script_discoverable(self, smoke_project: Path) -> None:
        result = json.loads(await list_recipes())
        names = [s["name"] for s in result["recipes"]]
        assert "smoke-test" in names

    async def test_script_loads_with_expected_structure(self, smoke_project: Path) -> None:
        result = json.loads(await load_recipe(name="smoke-test"))
        assert "content" in result
        assert "suggestions" in result
        pipeline = yaml.safe_load(result["content"])
        assert "steps" in pipeline
        assert "ingredients" in pipeline
        assert "kitchen_rules" in pipeline
        expected_steps = {
            "setup",
            "seed_task",
            "create_branch",
            "investigate",
            "rectify",
            "implement",
            "test",
            "assess",
            "classify",
            "merge",
            "check_summary",
            "create_summary",
            "done",
            "escalate",
        }
        assert set(pipeline["steps"].keys()) == expected_steps
        assert pipeline["steps"]["setup"]["tool"] == "run_cmd"
        assert pipeline["steps"]["investigate"]["tool"] == "run_skill"
        assert pipeline["steps"]["implement"]["tool"] == "run_skill_retry"
        assert pipeline["steps"]["test"]["tool"] == "test_check"
        assert pipeline["steps"]["merge"]["tool"] == "merge_worktree"
        assert pipeline["steps"]["classify"]["tool"] == "classify_fix"
        assert pipeline["steps"]["create_branch"]["tool"] == "run_cmd"
        assert (
            pipeline["steps"]["check_summary"]["python"]
            == "autoskillit.smoke_utils.check_bug_report_non_empty"
        )
        assert pipeline["steps"]["create_summary"]["tool"] == "run_skill"

    def test_executor_interpolation(self) -> None:
        executor = SmokeExecutor(steps={}, inputs={"workspace": "/tmp/ws"})
        executor.context["plan_path"] = "/tmp/ws/plan.md"
        result = executor._interpolate(
            {"cwd": "${{ inputs.workspace }}", "path": "${{ context.plan_path }}"}
        )
        assert result == {"cwd": "/tmp/ws", "path": "/tmp/ws/plan.md"}

    def test_executor_routing_success_failure(self) -> None:
        executor = SmokeExecutor(steps={}, inputs={})
        step = {"tool": "run_cmd", "on_success": "next", "on_failure": "escalate"}
        assert executor._route(step, {"success": True}) == "next"
        assert executor._route(step, {"success": False}) == "escalate"

    def test_executor_routing_on_result(self) -> None:
        executor = SmokeExecutor(steps={}, inputs={})
        step = {
            "tool": "classify_fix",
            "on_result": {
                "field": "restart_scope",
                "routes": {
                    "full_restart": "investigate",
                    "partial_restart": "implement",
                },
            },
            "on_failure": "escalate",
        }
        assert executor._route(step, {"restart_scope": "full_restart"}) == "investigate"
        assert executor._route(step, {"restart_scope": "partial_restart"}) == "implement"

    def test_executor_capture(self) -> None:
        executor = SmokeExecutor(steps={}, inputs={})
        step = {"capture": {"plan_path": "${{ result.plan_path }}"}}
        result = {"success": True, "result": "Done.\nplan_path=/tmp/ws/plan.md\n"}
        executor._capture(step, result)
        assert executor.context["plan_path"] == "/tmp/ws/plan.md"

    async def test_executor_retry_logic(self) -> None:
        call_count = 0

        async def mock_run_skill_retry(**kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps(
                    {"success": False, "needs_retry": True, "result": "context full"}
                )
            return json.dumps(
                {"success": True, "needs_retry": False, "result": "worktree_path=/tmp/wt"}
            )

        step_def = {
            "tool": "run_skill_retry",
            "with": {"skill_command": "test", "cwd": "/tmp"},
            "retry": {"max_attempts": 3, "on": "needs_retry", "on_exhausted": "escalate"},
        }
        executor = SmokeExecutor(steps={}, inputs={})
        with patch.dict(_TOOL_MAP, {"run_skill_retry": mock_run_skill_retry}):
            result = await executor._execute(step_def)
        assert call_count == 2
        assert result["success"] is True

    async def test_executor_max_attempts_zero_routes_to_on_exhausted(self) -> None:
        """With max_attempts=0, the first needs_retry result must route to on_exhausted."""
        steps = {
            "implement": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge plan.md"},
                "retry": {"max_attempts": 0, "on": "needs_retry", "on_exhausted": "retry_wt"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "done",
                "on_failure": "done",
            },
            "retry_wt": {"action": "stop", "message": "reached retry_wt"},
            "done": {"action": "stop", "message": "reached done"},
        }
        call_log: list[dict] = []

        async def mock_run_skill_retry(**kwargs: object) -> str:
            call_log.append(dict(kwargs))
            return json.dumps(
                {
                    "success": False,
                    "needs_retry": True,
                    "result": "context limit",
                    "retry_reason": "resume",
                    "session_id": "",
                    "subtype": "error_max_turns",
                    "is_error": True,
                    "exit_code": -1,
                    "stderr": "",
                    "token_usage": None,
                }
            )

        with patch.dict(_TOOL_MAP, {"run_skill_retry": mock_run_skill_retry}):
            executor = SmokeExecutor(steps, inputs={})
            terminal_step, message = await executor.run(start="implement")

        assert len(call_log) == 1, "Tool must be called exactly once with max_attempts=0"
        assert terminal_step == "retry_wt", (
            f"Expected on_exhausted route to 'retry_wt', got '{terminal_step}': {message}"
        )

    async def test_script_has_collect_on_branch_input(self, smoke_project: Path) -> None:
        result = json.loads(await load_recipe(name="smoke-test"))
        pipeline = yaml.safe_load(result["content"])
        inputs = pipeline["ingredients"]
        assert "collect_on_branch" in inputs
        assert inputs["collect_on_branch"]["default"] == "true"
        assert "original_base_branch" in inputs
        assert inputs["original_base_branch"]["default"] == "main"

    async def test_assess_step_references_bug_report(self) -> None:
        pipeline = yaml.safe_load(SMOKE_SCRIPT.read_text())
        assess_cmd = pipeline["steps"]["assess"]["with"]["skill_command"]
        assert "bug_report.json" in assess_cmd

    def test_pipeline_summary_skill_exists(self) -> None:
        from autoskillit.workspace.skills import SkillResolver

        resolver = SkillResolver()
        names = [s.name for s in resolver.list_all()]
        assert "pipeline-summary" in names

    def test_pipeline_summary_contract_declared(self) -> None:
        contracts_path = PROJECT_ROOT / "src" / "autoskillit" / "recipe" / "skill_contracts.yaml"
        contracts = yaml.safe_load(contracts_path.read_text())
        assert "pipeline-summary" in contracts["skills"]
        skill = contracts["skills"]["pipeline-summary"]
        required_inputs = [i["name"] for i in skill["inputs"] if i.get("required", False)]
        assert "bug_report_path" in required_inputs
        assert "feature_branch" in required_inputs
        assert "target_branch" in required_inputs
        assert "workspace" in required_inputs


# ---------------------------------------------------------------------------
# Smoke Execution Tests (API required)
# ---------------------------------------------------------------------------


class TestSmokePipelineExecution:
    """Full end-to-end pipeline execution with real API calls.

    Skipped unless SMOKE_TEST=1 is set (``task test-smoke`` sets this).
    """

    pytestmark = pytest.mark.skipif(not os.environ.get("SMOKE_TEST"), reason="SMOKE_TEST not set")

    @pytest.fixture(autouse=True)
    def _smoke_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = AutomationConfig(
            test_check=TestCheckConfig(command=["python", "-c", "pass"], timeout=30),
        )
        monkeypatch.setattr(server, "_config", cfg)

    async def _run_pipeline(self, workspace: Path, script_path: Path) -> tuple[str | None, str]:
        raw = script_path.read_text()
        pipeline = yaml.safe_load(raw)
        executor = SmokeExecutor(
            steps=pipeline["steps"],
            inputs={
                "workspace": str(workspace),
                "base_branch": "main",
                "collect_on_branch": "true",
                "original_base_branch": "main",
            },
        )
        terminal, message = await executor.run()
        return terminal, message

    @pytest.mark.smoke
    async def test_happy_path(self, smoke_workspace: Path, smoke_script_path: Path) -> None:
        terminal, _message = await asyncio.wait_for(
            self._run_pipeline(smoke_workspace, smoke_script_path),
            timeout=180,
        )
        assert terminal == "done"
