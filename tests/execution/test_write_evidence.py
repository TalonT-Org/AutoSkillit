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

from autoskillit.core import HeadlessExecutor, WriteBehaviorSpec
from tests.conftest import _make_result
from tests.execution.conftest import _mock_backend

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
        import autoskillit.execution.headless._headless_execute as _exec_mod
        from autoskillit.execution.headless import run_headless_core

        resolver_calls: list[str] = []
        original = _exec_mod._resolve_skill_temp_dir

        def recording_resolver(cwd: str, skill_command: str) -> Path | None:
            resolver_calls.append(skill_command)
            return original(cwd, skill_command)

        monkeypatch.setattr(_exec_mod, "_resolve_skill_temp_dir", recording_resolver)

        explicit_dir = tmp_path / "planner" / "run-20260502"
        explicit_dir.mkdir(parents=True)

        async def mock_runner(cmd, **kwargs):
            return _make_result()

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
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
            (
                "/autoskillit:exp-lens-{slug}",
                None,
            ),  # regex stops at '{'; remainder.startswith("{") → None
            (
                "/autoskillit:foo-${{ var }}",
                None,
            ),  # regex stops at '$'; remainder.startswith("${{") → None
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


class TestSubdirSnapshot:
    """Stat-based snapshot detects writes in pre-existing subdirectories."""

    def test_detects_write_in_preexisting_subdir(self, tmp_path: Path) -> None:
        from autoskillit.execution.headless import _stat_snapshot

        watch_dir = tmp_path / "planner_run"
        sub_dir = watch_dir / "refine_contexts"
        sub_dir.mkdir(parents=True)
        (sub_dir / "context_P1.json").write_text("{}")

        pre = _stat_snapshot(watch_dir)

        (sub_dir / "P1_result.json").write_text('{"assignments": []}')

        post = _stat_snapshot(watch_dir)

        assert post.keys() - pre.keys() == {"refine_contexts/P1_result.json"}

    def test_detects_write_in_nested_subdir(self, tmp_path: Path) -> None:
        from autoskillit.execution.headless import _stat_snapshot

        watch_dir = tmp_path / "planner_run"
        nested = watch_dir / "work_packages" / "wp_sentinels"
        nested.mkdir(parents=True)

        pre = _stat_snapshot(watch_dir)

        (nested / "P1_result.json").write_text("{}")

        post = _stat_snapshot(watch_dir)

        assert post.keys() - pre.keys() == {"work_packages/wp_sentinels/P1_result.json"}

    @pytest.mark.anyio
    async def test_run_headless_core_detects_subdir_write(
        self, tmp_path: Path, minimal_ctx
    ) -> None:
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _success_session_json

        watch_dir = tmp_path / "output"
        sub_dir = watch_dir / "results"
        sub_dir.mkdir(parents=True)
        (sub_dir / "existing.json").write_text("{}")

        async def mock_runner(cmd, **kwargs):
            if cmd[0] == "git":
                return _make_result(returncode=1, stdout="")
            (sub_dir / "new_output.json").write_text('{"data": true}')
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
        proj = tmp_path / "proj"
        proj.mkdir()

        result = await run_headless_core(
            "/autoskillit:test-skill",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[watch_dir],
        )
        assert result.evidence.fs_writes_detected is True

    @pytest.mark.anyio
    async def test_run_headless_core_empty_subdir_no_false_positive(
        self, tmp_path: Path, minimal_ctx
    ) -> None:
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _success_session_json

        watch_dir = tmp_path / "output"
        sub_dir = watch_dir / "results"
        sub_dir.mkdir(parents=True)
        (sub_dir / "existing.json").write_text("{}")

        async def mock_runner(cmd, **kwargs):
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
        proj = tmp_path / "proj"
        proj.mkdir()

        result = await run_headless_core(
            "/autoskillit:test-skill",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[watch_dir],
        )
        assert result.evidence.fs_writes_detected is False


class TestStatSnapshot:
    """Stat-based snapshot detects creation, modification, and deletion."""

    def test_stat_snapshot_detects_modified_file(self, tmp_path: Path) -> None:
        from autoskillit.execution.headless import _stat_snapshot

        watch_dir = tmp_path / "output"
        watch_dir.mkdir()
        target = watch_dir / "plan.md"
        target.write_text("original content")

        pre = _stat_snapshot(watch_dir)
        # Detection relies on size difference, not mtime — no sleep needed.
        target.write_text("modified content — longer text means different size")

        post = _stat_snapshot(watch_dir)
        assert pre != post
        assert "plan.md" in pre
        assert "plan.md" in post
        assert pre["plan.md"] != post["plan.md"]

    def test_stat_snapshot_detects_new_file(self, tmp_path: Path) -> None:
        """Stat-based snapshot still detects new file creation."""
        from autoskillit.execution.headless import _stat_snapshot

        watch_dir = tmp_path / "output"
        watch_dir.mkdir()
        (watch_dir / "existing.md").write_text("content")

        pre = _stat_snapshot(watch_dir)
        (watch_dir / "new_file.md").write_text("new content")
        post = _stat_snapshot(watch_dir)

        assert set(post.keys()) - set(pre.keys()) == {"new_file.md"}

    def test_stat_snapshot_detects_deleted_file(self, tmp_path: Path) -> None:
        """Stat-based snapshot detects file deletion."""
        from autoskillit.execution.headless import _stat_snapshot

        watch_dir = tmp_path / "output"
        watch_dir.mkdir()
        target = watch_dir / "ephemeral.md"
        target.write_text("content")

        pre = _stat_snapshot(watch_dir)
        target.unlink()
        post = _stat_snapshot(watch_dir)

        assert "ephemeral.md" in pre
        assert "ephemeral.md" not in post

    def test_stat_snapshot_return_type_encodes_file_state(self, tmp_path: Path) -> None:
        """Structural contract: snapshot values must be tuples, not bare strings."""
        from autoskillit.execution.headless import _stat_snapshot

        watch_dir = tmp_path / "output"
        watch_dir.mkdir()
        (watch_dir / "file.md").write_text("content")

        snapshot = _stat_snapshot(watch_dir)
        for path, state in snapshot.items():
            assert isinstance(path, str), "Keys must be relative path strings"
            assert isinstance(state, tuple), "Values must be (mtime_ns, size) tuples"
            assert len(state) == 2, "State tuple must have exactly 2 elements"
            assert isinstance(state[0], int), "mtime_ns must be int"
            assert isinstance(state[1], int), "size must be int"

    @pytest.mark.anyio
    async def test_run_headless_core_detects_existing_file_modification(
        self, tmp_path: Path, minimal_ctx
    ) -> None:
        """run_headless_core sets fs_writes_detected=True when existing file is modified."""
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _success_session_json

        watch_dir = tmp_path / "output"
        watch_dir.mkdir()
        existing_file = watch_dir / "plan.md"
        existing_file.write_text("original plan content")

        async def mock_runner(cmd, **kwargs):
            if cmd[0] == "git":
                return _make_result(returncode=1, stdout="")
            existing_file.write_text("modified plan — dry walkthrough verified = TRUE")
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
        proj = tmp_path / "proj"
        proj.mkdir()

        result = await run_headless_core(
            "/autoskillit:test-skill",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[watch_dir],
        )
        assert result.evidence.fs_writes_detected is True


class TestSnapshotTypeContract:
    """Structural contract: snapshot function must return state-bearing type, not path-only."""

    def test_snapshot_return_type_is_dict_not_set(self) -> None:
        """Guard against regression to set[str] return type."""
        import inspect as _inspect

        from autoskillit.execution.headless._headless_helpers import _stat_snapshot

        sig = _inspect.signature(_stat_snapshot)
        ret = sig.return_annotation
        assert "dict" in str(ret).lower()


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
            if cmd[0] == "git":
                return _make_result(returncode=1, stdout="")
            (run_dir / "refined_plan.json").write_text("{}")
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
        proj = tmp_path / "proj"
        proj.mkdir()

        sr = await run_headless_core(
            "/autoskillit:planner-refine-phases arg",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[run_dir],
            write_behavior=WriteBehaviorSpec(mode="always"),
        )
        assert sr.evidence.fs_writes_detected is True


class TestLateCreatedDirectoryDetection:
    """write_watch_dirs detects writes in directories created during the session (T-WE-FS-FN)."""

    @pytest.mark.anyio
    async def test_late_created_dir_write_detected(self, tmp_path: Path, minimal_ctx) -> None:
        """Directory that didn't exist at session start but is created with files must be detected.

        T-WE-FS-FN: fails under old code (missing key → None → skip comparison);
        passes under new code (missing dir → {} → comparison fires).
        """
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _success_session_json

        watch_dir = tmp_path / "output"
        # watch_dir does NOT exist at session start

        async def mock_runner(cmd, **kwargs):
            if cmd[0] == "git":
                return _make_result(returncode=1, stdout="")
            watch_dir.mkdir(parents=True)
            (watch_dir / "result.json").write_text('{"done": true}')
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
        proj = tmp_path / "proj"
        proj.mkdir()

        result = await run_headless_core(
            "/autoskillit:test-skill",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[watch_dir],
        )
        assert result.evidence.fs_writes_detected is True, (
            "fs_writes_detected must be True when session creates a nonexistent watch directory"
        )


class TestSentinelDisambiguation:
    """Sentinel disambiguation: None (OSError) vs {} (missing dir) (T-WE-FS-SENTINEL)."""

    @pytest.mark.anyio
    async def test_oserror_pre_scan_skips_comparison(self, tmp_path: Path, minimal_ctx) -> None:
        """OSError pre-scan → None sentinel → comparison skipped → fs_writes_detected=False."""
        from unittest.mock import patch

        from autoskillit.execution.headless import run_headless_core
        from autoskillit.execution.headless._headless_helpers import (
            _stat_snapshot as _real_snap,
        )
        from tests.execution.conftest import _success_session_json

        watch_dir = tmp_path / "output"
        watch_dir.mkdir()

        call_count: list[int] = [0]

        def _mock_snap(d):
            call_count[0] += 1
            if call_count[0] == 1 and d == watch_dir:
                raise OSError("simulated scan failure")
            return _real_snap(d)

        async def mock_runner(cmd, **kwargs):
            if cmd[0] == "git":
                return _make_result(returncode=1, stdout="")
            (watch_dir / "new_file.json").write_text("{}")
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
        proj = tmp_path / "proj"
        proj.mkdir()

        with patch("autoskillit.execution.headless._headless_execute._stat_snapshot", _mock_snap):
            result = await run_headless_core(
                "/autoskillit:test-skill",
                str(proj),
                minimal_ctx,
                write_watch_dirs=[watch_dir],
            )

        assert result.evidence.fs_writes_detected is False, (
            "OSError in pre-scan must not produce a false positive — comparison must be skipped"
        )

    @pytest.mark.anyio
    async def test_missing_dir_pre_scan_empty_dict_sentinel_detects_new_files(
        self, tmp_path: Path, minimal_ctx
    ) -> None:
        """Missing dir at pre-scan → {} sentinel → comparison fires → fs_writes_detected=True.

        Contrast with OSError (None sentinel) which skips comparison.
        T-WE-FS-SENTINEL: fails under old code (missing key → None → skip);
        passes under new code ({} → comparison fires).
        """
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _success_session_json

        watch_dir = tmp_path / "output"
        # watch_dir does NOT exist at session start

        async def mock_runner(cmd, **kwargs):
            if cmd[0] == "git":
                return _make_result(returncode=1, stdout="")
            watch_dir.mkdir(parents=True)
            (watch_dir / "result.json").write_text('{"done": true}')
            return _make_result(returncode=0, stdout=_success_session_json("done"))

        minimal_ctx.runner = mock_runner
        minimal_ctx.backend = _mock_backend()
        proj = tmp_path / "proj"
        proj.mkdir()

        result = await run_headless_core(
            "/autoskillit:test-skill",
            str(proj),
            minimal_ctx,
            write_watch_dirs=[watch_dir],
        )
        assert result.evidence.fs_writes_detected is True, (
            "Missing dir at pre-scan must detect new files created by the session"
        )
