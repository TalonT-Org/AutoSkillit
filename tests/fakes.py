"""Protocol-based test fakes for autoskillit.

Single authoritative module for in-memory test doubles that satisfy protocols
defined in ``core/_type_protocols.py``. Imports only from L0 (``autoskillit.core``).
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from autoskillit.core.types import (
    CIRunScope,
    CIWatcher,
    DatabaseReader,
    HeadlessExecutor,
    MergeQueueWatcher,
    RecipeRepository,
    SkillResult,
    SubprocessResult,
    SubprocessRunner,
    TerminationReason,
    TestResult,
    TestRunner,
    WriteBehaviorSpec,
)

# ---------------------------------------------------------------------------
# Shared side-effect resolution helper
# ---------------------------------------------------------------------------


def _resolve_side_effect(effect: Any) -> Any:
    """Resolve a side-effect value: raise exceptions, call callables, or return as-is.

    Handles async callables by running them on the current event loop.
    """
    if isinstance(effect, BaseException):
        raise effect
    if isinstance(effect, type) and issubclass(effect, BaseException):
        raise effect()
    if callable(effect):
        result = effect()
        if asyncio.iscoroutine(result):
            return asyncio.get_event_loop().run_until_complete(result)
        return result
    return effect


# ---------------------------------------------------------------------------
# HeadlessExecutor fake
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ExecutorCall:
    """Record of a single ``HeadlessExecutor.run()`` invocation."""

    skill_command: str
    cwd: str
    model: str = ""
    step_name: str = ""
    kitchen_id: str = ""
    order_id: str = ""
    add_dirs: tuple[Any, ...] = ()
    timeout: float | None = None
    stale_threshold: float | None = None
    idle_output_timeout: float | None = None
    expected_output_patterns: tuple[str, ...] = ()
    write_behavior: Any | None = None
    completion_marker: str = ""
    recipe_name: str = ""
    recipe_content_hash: str = ""
    recipe_composite_hash: str = ""
    recipe_version: str | None = None


@dataclasses.dataclass
class DispatchFoodTruckCall:
    """Record of a single ``HeadlessExecutor.dispatch_food_truck()`` invocation."""

    orchestrator_prompt: str
    cwd: str
    completion_marker: str = ""
    model: str = ""
    step_name: str = ""
    kitchen_id: str = ""
    order_id: str = ""
    campaign_id: str = ""
    dispatch_id: str = ""
    project_dir: str = ""
    timeout: float | None = None
    stale_threshold: float | None = None
    idle_output_timeout: float | None = None
    env_extras: Mapping[str, str] | None = None
    requires_packs: Sequence[str] = ()
    on_spawn: Callable[[int], None] | None = None


_DEFAULT_SKILL_RESULT = SkillResult(
    success=True,
    result="ok",
    session_id="",
    subtype="success",
    is_error=False,
    exit_code=0,
    needs_retry=False,
    retry_reason="none",
    stderr="",
    token_usage=None,
)


class InMemoryHeadlessExecutor(HeadlessExecutor):
    """In-memory test double for :class:`HeadlessExecutor`.

    Supports a FIFO queue via :meth:`push` and records every call in
    :attr:`calls`.
    """

    def __init__(self, default_result: SkillResult | None = None) -> None:
        self._default = default_result or _DEFAULT_SKILL_RESULT
        self._queue: deque[SkillResult] = deque()
        self.calls: list[ExecutorCall] = []
        self.dispatch_calls: list[DispatchFoodTruckCall] = []

    def push(self, result: SkillResult) -> None:
        """Enqueue a result to be returned by the next :meth:`run` call."""
        self._queue.append(result)

    async def run(
        self,
        skill_command: str,
        cwd: str,
        *,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        add_dirs: Sequence[Any] = (),
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        expected_output_patterns: Sequence[str] = (),
        write_behavior: WriteBehaviorSpec | None = None,
        completion_marker: str = "",
        recipe_name: str = "",
        recipe_content_hash: str = "",
        recipe_composite_hash: str = "",
        recipe_version: str | None = None,
    ) -> SkillResult:
        self.calls.append(
            ExecutorCall(
                skill_command=skill_command,
                cwd=cwd,
                model=model,
                step_name=step_name,
                kitchen_id=kitchen_id,
                order_id=order_id,
                add_dirs=tuple(add_dirs),
                timeout=timeout,
                stale_threshold=stale_threshold,
                idle_output_timeout=idle_output_timeout,
                expected_output_patterns=tuple(expected_output_patterns),
                write_behavior=write_behavior,
                completion_marker=completion_marker,
                recipe_name=recipe_name,
                recipe_content_hash=recipe_content_hash,
                recipe_composite_hash=recipe_composite_hash,
                recipe_version=recipe_version,
            )
        )
        if self._queue:
            return dataclasses.replace(self._queue.popleft())
        # Return a defensive copy so callers mutating fields (e.g. run_skill
        # setting order_id) don't pollute the shared default across tests.
        return dataclasses.replace(self._default)

    async def dispatch_food_truck(
        self,
        orchestrator_prompt: str,
        cwd: str,
        *,
        completion_marker: str,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        campaign_id: str = "",
        dispatch_id: str = "",
        project_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
        requires_packs: Sequence[str] = (),
        on_spawn: Callable[[int], None] | None = None,
    ) -> SkillResult:
        self.dispatch_calls.append(
            DispatchFoodTruckCall(
                orchestrator_prompt=orchestrator_prompt,
                cwd=cwd,
                completion_marker=completion_marker,
                model=model,
                step_name=step_name,
                kitchen_id=kitchen_id,
                order_id=order_id,
                campaign_id=campaign_id,
                dispatch_id=dispatch_id,
                project_dir=project_dir,
                timeout=timeout,
                stale_threshold=stale_threshold,
                idle_output_timeout=idle_output_timeout,
                env_extras=env_extras,
                requires_packs=requires_packs,
                on_spawn=on_spawn,
            )
        )
        if self._queue:
            return dataclasses.replace(self._queue.popleft())
        return dataclasses.replace(self._default)


# ---------------------------------------------------------------------------
# TestRunner fake
# ---------------------------------------------------------------------------


class InMemoryTestRunner(TestRunner):
    """In-memory test double for :class:`TestRunner`.

    Pops pre-configured results from a deque; falls back to a passing
    ``TestResult`` when the deque is exhausted.
    """

    def __init__(self, results: list[TestResult] | None = None) -> None:
        self._results: deque[TestResult] = deque(results or [])
        self._call_count = 0
        self.calls: list[Path] = []

    async def run(self, cwd: Path) -> TestResult:
        self._call_count += 1
        self.calls.append(cwd)
        if self._results:
            return self._results.popleft()
        return TestResult(passed=True, stdout="", stderr="")

    @property
    def call_count(self) -> int:
        return self._call_count


# ---------------------------------------------------------------------------
# RecipeRepository fake
# ---------------------------------------------------------------------------


class InMemoryRecipeRepository(RecipeRepository):
    """In-memory test double for :class:`RecipeRepository`."""

    def __init__(self) -> None:
        self._recipes: dict[str, Any] = {}
        self._validated: dict[str, dict[str, Any]] = {}
        self._path_validated: dict[str, dict[str, Any]] = {}
        self._all_recipes: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []

    # -- test setup helpers --

    def add_recipe(self, name: str, data: Any) -> None:
        self._recipes[name] = data

    def set_validated(self, name: str, result: dict[str, Any]) -> None:
        self._validated[name] = result

    def set_path_validated(self, path: str, result: dict[str, Any]) -> None:
        self._path_validated[path] = result

    def set_all(self, data: dict[str, Any]) -> None:
        self._all_recipes = data

    # -- protocol methods --

    def find(self, name: str, project_dir: Path) -> Any:
        self.calls.append({"method": "find", "name": name, "project_dir": project_dir})
        return self._recipes.get(name)

    def list(self, project_dir: Path) -> Any:
        self.calls.append({"method": "list", "project_dir": project_dir})
        return list(self._recipes.keys())

    def load_and_validate(
        self,
        name: str,
        project_dir: Any,
        *,
        suppressed: Sequence[str] | None = None,
        resolved_defaults: dict[str, str] | None = None,
        ingredient_overrides: dict[str, str] | None = None,
        temp_dir: Path | None = None,
        temp_dir_relpath: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "load_and_validate",
                "name": name,
                "project_dir": project_dir,
                "suppressed": suppressed,
                "resolved_defaults": resolved_defaults,
                "ingredient_overrides": ingredient_overrides,
                "temp_dir": temp_dir,
                "temp_dir_relpath": temp_dir_relpath,
            }
        )
        return self._validated.get(name, {"valid": False, "error": "not configured"})

    def validate_from_path(
        self, script_path: Any, temp_dir_relpath: str = ".autoskillit/temp"
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "validate_from_path",
                "script_path": script_path,
                "temp_dir_relpath": temp_dir_relpath,
            }
        )
        key = str(script_path)
        return self._path_validated.get(key, {"valid": False, "error": "not configured"})

    def list_all(self, project_dir: Any | None = None) -> dict[str, Any]:
        self.calls.append({"method": "list_all", "project_dir": project_dir})
        return self._all_recipes

    async def apply_triage_gate(
        self,
        result: dict[str, Any],
        recipe_name: str,
        recipe_info: Any,
        temp_dir: Path,
        logger: Any,
        triage_fn: Callable[..., Awaitable[Sequence[dict[str, Any]]]] | None = None,
    ) -> dict[str, Any]:
        return result


# ---------------------------------------------------------------------------
# CIWatcher fake
# ---------------------------------------------------------------------------


class InMemoryCIWatcher(CIWatcher):
    """In-memory test double for :class:`CIWatcher`."""

    def __init__(
        self,
        wait_result: dict[str, Any] | None = None,
        status_result: dict[str, Any] | None = None,
    ) -> None:
        self._wait_result = wait_result or {
            "run_id": 0,
            "conclusion": "success",
            "failed_jobs": [],
        }
        self._status_result = status_result or {"runs": []}
        self.wait_calls: list[dict[str, Any]] = []
        self.status_calls: list[dict[str, Any]] = []
        self.wait_side_effect: Any | None = None
        self.status_side_effect: Any | None = None

    async def wait(
        self,
        branch: str,
        *,
        repo: str | None = None,
        scope: CIRunScope = CIRunScope(),
        timeout_seconds: int = 300,
        lookback_seconds: int = 120,
        cwd: str = "",
    ) -> dict[str, Any]:
        self.wait_calls.append(
            {
                "branch": branch,
                "repo": repo,
                "scope": scope,
                "timeout_seconds": timeout_seconds,
                "lookback_seconds": lookback_seconds,
                "cwd": cwd,
            }
        )
        if self.wait_side_effect is not None:
            return _resolve_side_effect(self.wait_side_effect)
        return self._wait_result

    async def status(
        self,
        branch: str,
        *,
        repo: str | None = None,
        run_id: int | None = None,
        scope: CIRunScope = CIRunScope(),
        cwd: str = "",
    ) -> dict[str, Any]:
        self.status_calls.append(
            {
                "branch": branch,
                "repo": repo,
                "run_id": run_id,
                "scope": scope,
                "cwd": cwd,
            }
        )
        if self.status_side_effect is not None:
            return _resolve_side_effect(self.status_side_effect)
        return self._status_result


# ---------------------------------------------------------------------------
# MergeQueueWatcher fake
# ---------------------------------------------------------------------------


class InMemoryMergeQueueWatcher(MergeQueueWatcher):
    """In-memory test double for :class:`MergeQueueWatcher`."""

    def __init__(
        self,
        wait_result: dict[str, Any] | None = None,
        toggle_result: dict[str, Any] | None = None,
    ) -> None:
        self._wait_result = wait_result or {
            "success": True,
            "pr_state": "merged",
            "reason": "PR merged",
        }
        self._toggle_result = toggle_result or {"success": True, "toggled": True}
        self.wait_calls: list[dict[str, Any]] = []
        self.toggle_calls: list[dict[str, Any]] = []
        self.wait_side_effect: Any | None = None
        self.toggle_side_effect: Any | None = None

    async def wait(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
        timeout_seconds: int = 600,
        poll_interval: int = 15,
        stall_grace_period: int = 60,
        max_stall_retries: int = 3,
        not_in_queue_confirmation_cycles: int = 2,
        max_inconclusive_retries: int = 5,
    ) -> dict[str, Any]:
        self.wait_calls.append(
            {
                "pr_number": pr_number,
                "target_branch": target_branch,
                "repo": repo,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "poll_interval": poll_interval,
            }
        )
        if self.wait_side_effect is not None:
            return _resolve_side_effect(self.wait_side_effect)
        return self._wait_result

    async def toggle(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
    ) -> dict[str, Any]:
        self.toggle_calls.append(
            {
                "pr_number": pr_number,
                "target_branch": target_branch,
                "repo": repo,
                "cwd": cwd,
            }
        )
        if self.toggle_side_effect is not None:
            return _resolve_side_effect(self.toggle_side_effect)
        return self._toggle_result


# ---------------------------------------------------------------------------
# DatabaseReader fake
# ---------------------------------------------------------------------------


class InMemoryDatabaseReader(DatabaseReader):
    """In-memory test double for :class:`DatabaseReader`."""

    def __init__(self, query_result: dict[str, Any] | None = None) -> None:
        self._query_result = query_result or {
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
        self.calls: list[dict[str, Any]] = []
        self.side_effect: Any | None = None

    def query(
        self,
        db_path: str,
        sql: str,
        params: list | dict,  # type: ignore[type-arg]
        timeout_sec: int,
        max_rows: int,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "db_path": db_path,
                "sql": sql,
                "params": params,
                "timeout_sec": timeout_sec,
                "max_rows": max_rows,
            }
        )
        if self.side_effect is not None:
            return _resolve_side_effect(self.side_effect)
        return self._query_result


# ---------------------------------------------------------------------------
# SubprocessRunner fake (moved from conftest.py)
# ---------------------------------------------------------------------------


class MockSubprocessRunner(SubprocessRunner):
    """Test double for SubprocessRunner. Queues predetermined results.

    Inherits from SubprocessRunner (Protocol) so mypy verifies the __call__
    signature matches the protocol at class definition, not just at call sites.

    call_args_list stores (cmd, cwd, timeout, kwargs) tuples.
    IMPORTANT: Assert [N][1] (cwd) when testing cwd propagation.
    """

    def __init__(self) -> None:
        self._queue: deque[SubprocessResult] = deque()
        self._default = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=99999,
        )
        self.call_args_list: list[tuple] = []  # type: ignore[type-arg]

    def push(self, result: SubprocessResult) -> None:
        """Queue a result to be returned by the next __call__."""
        self._queue.append(result)

    def set_default(self, result: SubprocessResult) -> None:
        """Set the result returned when the queue is empty."""
        self._default = result

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        **kwargs: object,
    ) -> SubprocessResult:
        self.call_args_list.append((cmd, cwd, timeout, kwargs))
        if self._queue:
            return self._queue.popleft()
        return self._default
