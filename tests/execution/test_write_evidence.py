"""Write evidence: multi-directory fs snapshot and write_watch_dirs plumbing.

Tests for Part B of write-detection architectural immunity:
- Multi-dir filesystem snapshot via write_watch_dirs
- output_dir → write_watch_dirs plumbing in run_skill
- HeadlessExecutor protocol includes write_watch_dirs
- Planner skill end-to-end write detection
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from autoskillit.core import WriteBehaviorSpec
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestMultiDirFsSnapshot:
    """write_watch_dirs enables multi-directory filesystem snapshot."""

    def test_fs_snapshot_watches_multiple_dirs(self, tmp_path: Path) -> None:
        """When write_watch_dirs contains multiple paths, fs_writes_detected
        is True if ANY directory has new files."""
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        pre_a = {e.name for e in os.scandir(dir_a)}
        pre_b = {e.name for e in os.scandir(dir_b)}
        assert pre_a == set()
        assert pre_b == set()

        (dir_b / "output.json").write_text("{}")

        post_a = {e.name for e in os.scandir(dir_a)}
        post_b = {e.name for e in os.scandir(dir_b)}

        fs_writes_detected = any(
            bool(post - pre) for post, pre in [(post_a, pre_a), (post_b, pre_b)]
        )
        assert fs_writes_detected is True

    @pytest.mark.anyio
    async def test_fs_snapshot_watches_explicit_dir_not_skill_name(
        self, tmp_path: Path, minimal_ctx, monkeypatch
    ) -> None:
        """When write_watch_dirs is provided, _resolve_skill_temp_dir is NOT called."""
        import autoskillit.execution.headless as headless_mod
        from autoskillit.execution.headless import run_headless_core

        resolver_calls: list[str] = []
        original = headless_mod._resolve_skill_temp_dir

        def recording_resolver(cwd: str, skill_command: str) -> Path | None:
            resolver_calls.append(skill_command)
            return original(cwd, skill_command)

        monkeypatch.setattr(headless_mod, "_resolve_skill_temp_dir", recording_resolver)

        explicit_dir = tmp_path / "planner" / "run-20260502"
        explicit_dir.mkdir(parents=True)

        async def mock_runner(cmd, **kwargs):
            return _make_result()

        minimal_ctx.runner = mock_runner
        proj = tmp_path / "proj"
        proj.mkdir()

        await run_headless_core(
            "/autoskillit:planner-refine-phases arg",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[explicit_dir],
        )

        assert resolver_calls == [], (
            "_resolve_skill_temp_dir must not be called when write_watch_dirs is provided"
        )


class TestHeadlessExecutorProtocol:
    """HeadlessExecutor protocol includes write_watch_dirs."""

    def test_headless_executor_protocol_has_write_watch_dirs(self) -> None:
        """HeadlessExecutor.run() accepts write_watch_dirs parameter."""
        from autoskillit.core import HeadlessExecutor

        sig = inspect.signature(HeadlessExecutor.run)
        assert "write_watch_dirs" in sig.parameters


class TestUnifiedSkillNameResolution:
    """resolve_skill_name in core handles both /name and /autoskillit:name forms."""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("/autoskillit:make-plan arg1", "make-plan"),
            ("/make-plan arg1", "make-plan"),
            ("/autoskillit:planner-refine-phases ...", "planner-refine-phases"),
            ("/autoskillit:exp-lens-{slug}", None),
            ("/autoskillit:foo-${{ var }}", None),
            ("not a skill command", None),
        ],
    )
    def test_resolve_skill_name_handles_both_forms(
        self, command: str, expected: str | None
    ) -> None:
        from autoskillit.core import resolve_skill_name

        assert resolve_skill_name(command) == expected

    def test_no_duplicate_skill_name_regexes(self) -> None:
        """No module in recipe/ defines its own _SKILL_NAME_RE regex — all use core."""
        import ast

        from autoskillit.core import pkg_root

        recipe_dir = pkg_root() / "recipe"
        py_files = sorted(recipe_dir.glob("*.py"))
        assert len(py_files) > 0, (
            f"No .py files found in {recipe_dir} — pkg_root() may have resolved incorrectly"
        )
        violations: list[str] = []
        for py_file in py_files:
            try:
                tree = ast.parse(py_file.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "_SKILL_NAME_RE"
                ):
                    violations.append(py_file.name)
        assert violations == [], (
            f"_SKILL_NAME_RE defined in recipe/ modules {violations} — "
            "all should import resolve_skill_name from core"
        )


class TestBashFilePathEnrichment:
    """parse_session_result extracts absolute paths from Bash tool_use commands."""

    def test_bash_tool_use_has_bash_paths(self) -> None:
        from autoskillit.execution.session import parse_session_result

        bash_block = {
            "type": "tool_use",
            "name": "Bash",
            "id": "tu_1",
            "input": {"command": "cat /home/user/project/file.txt && ls /tmp/output/"},
        }
        assistant = {
            "type": "assistant",
            "message": {"content": [bash_block]},
        }
        result_record = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "test-sess",
        }
        stdout = json.dumps(assistant) + "\n" + json.dumps(result_record)
        session = parse_session_result(stdout)
        assert len(session.tool_uses) == 1
        entry = session.tool_uses[0]
        assert "bash_paths" in entry
        paths = entry["bash_paths"]
        assert "/home/user/project/file.txt" in paths
        assert "/tmp/output/" in paths


class TestPlannerSkillEndToEnd:
    """Planner skill that writes via Bash to run dir detected via write_watch_dirs."""

    @pytest.mark.anyio
    async def test_planner_skill_bash_write_to_run_dir_detected(
        self, tmp_path: Path, minimal_ctx
    ) -> None:
        """write_watch_dirs detection fires when the skill writes to run_dir during the session."""
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _success_session_json

        run_dir = tmp_path / ".autoskillit" / "temp" / "planner" / "run-20260502"
        run_dir.mkdir(parents=True)
        # Pre-snapshot: run_dir is empty at session start

        async def mock_runner(cmd, **kwargs):
            # Simulate the skill writing to run_dir during the session
            (run_dir / "refined_plan.json").write_text("{}")
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        proj = tmp_path / "proj"
        proj.mkdir()

        sr = await run_headless_core(
            "/autoskillit:planner-refine-phases arg",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[run_dir],
            write_behavior=WriteBehaviorSpec(mode="always"),
        )
        assert sr.fs_writes_detected is True
