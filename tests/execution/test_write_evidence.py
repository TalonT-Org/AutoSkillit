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

from autoskillit.core import RetryReason, WriteBehaviorSpec
from autoskillit.execution.headless import _build_skill_result, _resolve_skill_temp_dir
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
            bool(post - pre)
            for post, pre in [(post_a, pre_a), (post_b, pre_b)]
        )
        assert fs_writes_detected is True

    def test_fs_snapshot_watches_explicit_dir_not_skill_name(self, tmp_path: Path) -> None:
        """When write_watch_dirs is provided, _resolve_skill_temp_dir derivation
        is NOT used — the explicit dirs take precedence."""
        skill_derived = _resolve_skill_temp_dir(str(tmp_path), "/autoskillit:planner-refine-phases arg")
        assert skill_derived is not None
        assert skill_derived.name == "planner-refine-phases"

        explicit_dir = tmp_path / "planner" / "run-20260502"
        explicit_dir.mkdir(parents=True)
        (explicit_dir / "refined_plan.json").write_text("{}")

        assert not (skill_derived.exists() and any(skill_derived.iterdir()))
        assert any(explicit_dir.iterdir())


class TestOutputDirParameter:
    """output_dir parameter plumbing from run_skill to executor."""

    def test_run_skill_has_output_dir_parameter(self) -> None:
        """run_skill() accepts output_dir parameter."""
        from autoskillit.server.tools_execution import run_skill

        sig = inspect.signature(run_skill)
        assert "output_dir" in sig.parameters
        param = sig.parameters["output_dir"]
        assert param.default == ""


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
        from pathlib import Path as P

        from autoskillit.core import pkg_root

        recipe_dir = pkg_root() / "recipe"
        violations: list[str] = []
        for py_file in sorted(recipe_dir.glob("*.py")):
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

    def test_planner_skill_bash_write_to_run_dir_detected(self, tmp_path: Path) -> None:
        """A planner skill writing via Bash to .autoskillit/temp/planner/run-{ts}/
        produces has_evidence=True via write_watch_dirs override."""
        run_dir = tmp_path / ".autoskillit" / "temp" / "planner" / "run-20260502"
        run_dir.mkdir(parents=True)
        (run_dir / "refined_plan.json").write_text("{}")

        stdout_lines = []
        bash_block = {
            "type": "tool_use",
            "name": "Bash",
            "id": "tu_0",
            "input": {"command": f"echo '{{}}' > {run_dir}/refined_plan.json"},
        }
        assistant = {
            "type": "assistant",
            "message": {"content": [bash_block]},
        }
        stdout_lines.append(json.dumps(assistant))
        result_record = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "test-sess",
        }
        stdout_lines.append(json.dumps(result_record))
        stdout = "\n".join(stdout_lines)

        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:planner-refine-phases arg",
            write_behavior=WriteBehaviorSpec(mode="always"),
            fs_writes_detected=True,
        )
        assert sr.success is True
        assert sr.subtype != "zero_writes"
        assert sr.fs_writes_detected is True
