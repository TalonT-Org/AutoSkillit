"""Execution-layer protocol definitions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from ._type_results import SkillResult, TestResult, ValidatedAddDir, WriteBehaviorSpec

__all__ = [
    "TestRunner",
    "HeadlessExecutor",
    "OutputPatternResolver",
    "WriteExpectedResolver",
]


@runtime_checkable
class TestRunner(Protocol):
    """Protocol for running a test suite and reporting pass/fail.

    Returns a TestResult with passed, stdout, and stderr from the test run.
    """

    async def run(self, cwd: Path) -> TestResult: ...


@runtime_checkable
class HeadlessExecutor(Protocol):
    """Protocol for running headless Claude Code sessions."""

    async def run(
        self,
        skill_command: str,
        cwd: str,
        *,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        add_dirs: Sequence[ValidatedAddDir] = (),
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        expected_output_patterns: Sequence[str] = (),
        write_behavior: WriteBehaviorSpec | None = None,
        completion_marker: str = "",
        recipe_name: str = "",
        recipe_content_hash: str = "",
        recipe_composite_hash: str = "",
        recipe_version: str = "",
        allowed_write_prefix: str = "",
        readonly_skill: bool = False,
        write_watch_dirs: Sequence[Path] = (),
    ) -> SkillResult: ...

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
        on_spawn: Callable[[int, int], None] | None = None,
        allowed_write_prefix: str = "",
    ) -> SkillResult: ...


@runtime_checkable
class OutputPatternResolver(Protocol):
    """Protocol for resolving expected output patterns from a skill command."""

    def __call__(self, skill_command: str) -> Sequence[str]: ...


@runtime_checkable
class WriteExpectedResolver(Protocol):
    """Protocol for resolving write-expectation metadata from skill contracts."""

    def __call__(self, skill_command: str) -> WriteBehaviorSpec: ...
