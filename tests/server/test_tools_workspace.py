"""Tests for autoskillit server workspace tools."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from autoskillit.config import (
    AutomationConfig,
    ReadDbConfig,
    ResetWorkspaceConfig,
    SafetyConfig,
)
from autoskillit.core.types import AUTOSKILLIT_PRIVATE_ENV_VARS
from autoskillit.server.tools_status import read_db
from autoskillit.server.tools_workspace import reset_test_dir, reset_workspace, test_check
from autoskillit.workspace import CleanupResult
from tests.conftest import _make_result

test_check.__test__ = False  # type: ignore[attr-defined]


class TestResetWorkspace:
    """T6, T7: reset_workspace preserves configured dirs, requires marker."""

    @pytest.fixture(autouse=True)
    def _set_reset_command(self, tool_ctx):
        """Configure reset_workspace with a command for these tests."""
        tool_ctx.config = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(
                command=["make", "clean"],
                preserve_dirs={".cache", "reports"},
            )
        )

    @pytest.mark.anyio
    async def test_rejects_without_marker(self, tmp_path):
        """reset_workspace rejects directory without marker."""
        workspace = tmp_path / "unmarked"
        workspace.mkdir()
        result = json.loads(await reset_workspace(test_dir=str(workspace)))
        assert "error" in result
        assert "marker" in result["error"].lower()

    @pytest.mark.anyio
    async def test_rejects_nonexistent_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        result = json.loads(await reset_workspace(test_dir=str(workspace)))
        assert "does not exist" in result["error"]

    @pytest.mark.anyio
    async def test_preserves_configured_dirs(self, tool_ctx, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        (workspace / ".cache").mkdir()
        (workspace / ".cache" / "data.db").touch()
        (workspace / "reports").mkdir()
        (workspace / "reports" / "report.json").touch()
        (workspace / "output.txt").touch()
        (workspace / "temp_dir").mkdir()
        (workspace / "temp_dir" / "file.txt").touch()

        tool_ctx.runner.push(_make_result(0, "", ""))

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["success"] is True
        assert ".cache" in result["skipped"]
        assert "reports" in result["skipped"]
        assert "output.txt" in result["deleted"]
        assert "temp_dir" in result["deleted"]

        assert (workspace / ".cache" / "data.db").exists()
        assert (workspace / "reports" / "report.json").exists()
        assert not (workspace / "output.txt").exists()
        assert not (workspace / "temp_dir").exists()

    @pytest.mark.anyio
    async def test_reset_command_failure(self, tool_ctx, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        tool_ctx.runner.push(_make_result(1, "", "command not found"))

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert "error" in result
        assert result["error"] == "reset command failed"
        assert result["exit_code"] == 1

    @pytest.mark.anyio
    async def test_runs_correct_reset_command(self, tool_ctx, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        tool_ctx.runner.push(_make_result(0, "", ""))

        await reset_workspace(test_dir=str(workspace))

        call_args = tool_ctx.runner.call_args_list[0][0]
        assert call_args == [
            "make",
            "clean",
        ]


class TestTestCheck:
    """test_check returns unambiguous PASS/FAIL with cross-validation."""

    @pytest.mark.anyio
    async def test_passes_on_clean_run(self, tool_ctx):
        """returncode=0 with passing summary -> passed=True."""
        tool_ctx.runner.push(_make_result(0, "= 100 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_fails_on_nonzero_exit(self, tool_ctx):
        """returncode=1 -> passed=False regardless of output."""
        tool_ctx.runner.push(_make_result(1, "= 3 failed, 97 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_cross_validates_exit_code_against_output(self, tool_ctx):
        """returncode=0 but output contains 'failed' -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "= 3 failed, 8538 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_does_not_expose_summary(self, tool_ctx):
        """test_check returns passed + output — no summary, no output_file."""
        tool_ctx.runner.push(
            _make_result(0, "= 100 passed =\nTest output saved to: /tmp/out.txt\n", "")
        )
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert "summary" not in result
        assert "output_file" not in result
        assert set(result.keys()) == {"passed", "output"}

    @pytest.mark.anyio
    async def test_cross_validates_error_in_output(self, tool_ctx):
        """returncode=0 but output contains 'error' -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "= 1 error, 99 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_xfailed_not_treated_as_failure(self, tool_ctx):
        """xfailed tests are expected failures — exit code 0, should pass."""
        tool_ctx.runner.push(_make_result(0, "= 8552 passed, 3 xfailed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_xpassed_not_treated_as_failure(self, tool_ctx):
        """xpassed tests are unexpected passes — exit code 0, should pass."""
        tool_ctx.runner.push(_make_result(0, "= 99 passed, 1 xpassed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_mixed_xfail_with_real_failure(self, tool_ctx):
        """Real failure + xfailed — should still fail on the real failure."""
        tool_ctx.runner.push(_make_result(0, "= 1 failed, 2 xfailed, 97 passed =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_skipped_with_exit_zero_passes(self, tool_ctx):
        """Skipped tests with exit 0 — parser trusts exit code."""
        tool_ctx.runner.push(_make_result(0, "= 97 passed, 3 skipped =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_warnings_not_treated_as_failure(self, tool_ctx):
        """Warnings with exit 0 — should pass."""
        tool_ctx.runner.push(_make_result(0, "= 100 passed, 5 warnings =\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_bare_q_failures_detected(self, tool_ctx):
        """Bare -q failure line (rc=0 due to PIPESTATUS bug) -> passed=False."""
        tool_ctx.runner.push(_make_result(0, "3 failed, 97 passed in 2.31s\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_cwa_empty_output_fails(self, tool_ctx):
        """CWA: rc=0 but empty stdout -> passed=False (cannot confirm pass)."""
        tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is False

    @pytest.mark.anyio
    async def test_bare_q_clean_passes(self, tool_ctx):
        """Bare -q all-passing output: rc=0 and summary found -> passed=True."""
        tool_ctx.runner.push(_make_result(0, "100 passed in 1.50s\n", ""))
        result = json.loads(await test_check(worktree_path="/tmp/wt"))
        assert result["passed"] is True

    @pytest.mark.anyio
    async def test_test_check_resolves_relative_worktree_path(self, tool_ctx):
        """test_check must apply os.path.realpath() to worktree_path so that relative
        paths are resolved against os.getcwd() consistently, matching reset_test_dir
        and reset_workspace behavior."""
        relative_path = "../some_worktree"
        expected_resolved = os.path.realpath(relative_path)

        await test_check(worktree_path=relative_path)

        cwd = tool_ctx.runner.call_args_list[-1].kwargs["cwd"]
        assert str(cwd) == expected_resolved, (
            f"Expected cwd={expected_resolved!r}, got {str(cwd)!r}. "
            "test_check must apply os.path.realpath() to worktree_path."
        )

    @pytest.mark.anyio
    async def test_test_check_does_not_pass_headless_env_to_subprocess(
        self, tool_ctx, monkeypatch
    ):
        """When AUTOSKILLIT_HEADLESS is set in the calling process (simulating a headless
        MCP server), test_check must not pass it to the subprocess runner."""
        # Simulate running inside a headless session — set all private vars
        for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
            monkeypatch.setenv(var, "1")

        await test_check(worktree_path="/tmp/wt")

        assert tool_ctx.runner.call_args_list, "Runner was not called"
        kwargs = tool_ctx.runner.call_args_list[-1].kwargs
        env = kwargs.get("env")
        assert env is not None, "test_check must pass an explicit env= to the runner"
        for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
            assert var not in env, (
                f"{var} must not appear in env passed to subprocess by test_check"
            )


class TestResetGuard:
    """Marker-file-based reset guard for destructive operations."""

    @pytest.mark.anyio
    async def test_reset_test_dir_refuses_without_marker(self, tool_ctx, tmp_path):
        """Directory without marker file is refused."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert "error" in result
        assert "marker" in result["error"].lower() or "reset guard" in result["error"].lower()

    @pytest.mark.anyio
    async def test_reset_test_dir_allows_with_marker(self, tool_ctx, tmp_path):
        """Directory with marker file is cleared."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "some_file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert not (target / "some_file.txt").exists()

    @pytest.mark.anyio
    async def test_reset_test_dir_preserves_marker(self, tool_ctx, tmp_path):
        """Reset preserves the marker file so the workspace is reusable."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "data.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True
        assert (target / ".autoskillit-workspace").is_file()

    @pytest.mark.anyio
    async def test_reset_workspace_refuses_without_marker(self, tool_ctx, tmp_path):
        """reset_workspace also checks for marker."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        target = tmp_path / "workspace"
        target.mkdir()
        result = json.loads(await reset_workspace(test_dir=str(target)))
        assert "error" in result

    @pytest.mark.anyio
    async def test_reset_workspace_allows_with_marker(self, tool_ctx, tmp_path):
        """reset_workspace clears when marker is present."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# autoskillit workspace\n")
        (target / "file.txt").touch()
        tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await reset_workspace(test_dir=str(target)))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_custom_marker_name(self, tool_ctx, tmp_path):
        """Config can override marker file name."""
        tool_ctx.config = AutomationConfig(safety=SafetyConfig(reset_guard_marker=".my-workspace"))
        target = tmp_path / "workspace"
        target.mkdir()
        (target / ".my-workspace").touch()
        (target / "file.txt").touch()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_force_overrides_marker_check(self, tool_ctx, tmp_path):
        """force=True on reset_test_dir bypasses marker requirement."""
        target = tmp_path / "workspace"
        target.mkdir()
        (target / "file.txt").touch()
        # No marker, but force=True
        result = json.loads(await reset_test_dir(test_dir=str(target), force=True))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_rejects_nonexistent(self, tool_ctx, tmp_path):
        result = json.loads(await reset_test_dir(test_dir=str(tmp_path / "nope")))
        assert "does not exist" in result["error"]

    def test_safety_config_has_reset_guard_marker(self):
        """SafetyConfig has reset_guard_marker field."""
        cfg = SafetyConfig()
        assert cfg.reset_guard_marker == ".autoskillit-workspace"


class TestConfigDefaults:
    """Verify config defaults match expected values."""

    def test_default_preserve_dirs(self):
        cfg = AutomationConfig()
        assert cfg.reset_workspace.preserve_dirs == set()

    def test_default_test_command(self):
        cfg = AutomationConfig()
        assert cfg.test_check.command == ["task", "test-check"]

    def test_default_classify_fix_empty_prefixes(self):
        cfg = AutomationConfig()
        assert cfg.classify_fix.path_prefixes == []


@pytest.mark.usefixtures("tool_ctx")
class TestReadDb:
    """Integration tests for read_db tool with real SQLite databases."""

    @pytest.fixture
    def sample_db(self, tmp_path):
        """Create a sample SQLite database for testing."""
        import sqlite3

        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT, age INTEGER)")
        conn.execute("INSERT INTO users VALUES (1, 'Alice', 30)")
        conn.execute("INSERT INTO users VALUES (2, 'Bob', 25)")
        conn.execute("INSERT INTO users VALUES (3, 'Charlie', 35)")
        conn.commit()
        conn.close()
        return db

    @pytest.mark.anyio
    async def test_simple_select(self, sample_db):
        result = json.loads(await read_db(db_path=str(sample_db), query="SELECT * FROM users"))
        assert result["row_count"] == 3
        assert result["column_names"] == ["id", "name", "age"]
        assert len(result["rows"]) == 3
        assert result["truncated"] is False

    @pytest.mark.anyio
    async def test_parameterized_query(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT name FROM users WHERE age > ?",
                params="[28]",
            )
        )
        assert result["row_count"] == 2
        names = [r["name"] for r in result["rows"]]
        assert "Alice" in names
        assert "Charlie" in names

    @pytest.mark.anyio
    async def test_named_params(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT name FROM users WHERE age = :age",
                params='{"age": 25}',
            )
        )
        assert result["row_count"] == 1
        assert result["rows"][0]["name"] == "Bob"

    @pytest.mark.anyio
    async def test_empty_result(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT * FROM users WHERE age > 100",
            )
        )
        assert result["row_count"] == 0
        assert result["rows"] == []
        assert result["column_names"] == ["id", "name", "age"]

    @pytest.mark.anyio
    async def test_rejects_insert(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="INSERT INTO users VALUES (4, 'Dave', 40)",
            )
        )
        assert "error" in result
        err_lower = result["error"].lower()
        assert "forbidden" in err_lower or "select" in err_lower or "not authorized" in err_lower

    @pytest.mark.anyio
    async def test_rejects_drop(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="DROP TABLE users",
            )
        )
        assert "error" in result

    @pytest.mark.anyio
    async def test_rejects_attach(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="ATTACH DATABASE ':memory:' AS other",
            )
        )
        assert "error" in result

    @pytest.mark.anyio
    async def test_nonexistent_db(self, tmp_path):
        result = json.loads(
            await read_db(
                db_path=str(tmp_path / "nonexistent.db"),
                query="SELECT 1",
            )
        )
        assert "error" in result
        assert "does not exist" in result["error"] or "not found" in result["error"].lower()

    @pytest.mark.anyio
    async def test_not_a_file(self, tmp_path):
        result = json.loads(
            await read_db(
                db_path=str(tmp_path),
                query="SELECT 1",
            )
        )
        assert "error" in result

    @pytest.mark.anyio
    async def test_invalid_params_json(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT * FROM users",
                params="not json",
            )
        )
        assert "error" in result
        assert "params" in result["error"].lower()

    @pytest.mark.anyio
    async def test_gated_when_disabled(self, sample_db, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT 1",
            )
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()

    @pytest.mark.anyio
    async def test_max_rows_truncation(self, sample_db, tool_ctx):
        tool_ctx.config = AutomationConfig(read_db=ReadDbConfig(max_rows=2))
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT * FROM users",
            )
        )
        assert result["row_count"] == 2
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_blob_base64_encoding(self, tmp_path):
        import base64
        import sqlite3

        db = tmp_path / "blob.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE data (id INTEGER, content BLOB)")
        conn.execute("INSERT INTO data VALUES (1, ?)", (b"\x00\x01\x02\xff",))
        conn.commit()
        conn.close()
        result = json.loads(
            await read_db(
                db_path=str(db),
                query="SELECT * FROM data",
            )
        )
        assert base64.b64decode(result["rows"][0]["content"]) == b"\x00\x01\x02\xff"

    @pytest.mark.anyio
    async def test_query_timeout(self, sample_db, tool_ctx):
        tool_ctx.config = AutomationConfig(read_db=ReadDbConfig(timeout=1))
        # Cross join 3 rows^18 = ~387 million rows — guaranteed to exceed 1s timeout
        slow_query = "SELECT count(*) FROM " + ", ".join(f"users t{i}" for i in range(18))
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query=slow_query,
            )
        )
        assert "error" in result
        assert "timeout" in result["error"].lower()

    @pytest.mark.anyio
    async def test_sql_error_returns_error(self, sample_db):
        result = json.loads(
            await read_db(
                db_path=str(sample_db),
                query="SELECT nonexistent_column FROM users",
            )
        )
        assert "error" in result


@pytest.mark.anyio
async def test_tools_status_routes_through_db_reader(tool_ctx, tmp_path) -> None:
    """read_db routes through ctx.db_reader.query()."""
    import sqlite3 as _sqlite3

    tool_ctx.db_reader = MagicMock()
    tool_ctx.db_reader.query.return_value = {"rows": [], "count": 0}

    db_path = str(tmp_path / "test.db")
    # Create an empty sqlite db so path-exists check passes
    _sqlite3.connect(db_path).close()
    await read_db(db_path, "SELECT 1")
    tool_ctx.db_reader.query.assert_called_once()
    call_kwargs = tool_ctx.db_reader.query.call_args
    assert "SELECT 1" in str(call_kwargs)


@pytest.mark.anyio
async def test_reset_test_dir_returns_partial_failure_json(tool_ctx, tmp_path):
    """reset_test_dir returns structured JSON on partial failure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".autoskillit-workspace").write_text("# marker\n")
    (workspace / "ok_file").touch()

    mock_result = CleanupResult(
        deleted=["ok_file"],
        failed=[("bad_dir", "PermissionError: denied")],
        skipped=[],
    )
    tool_ctx.workspace_mgr = type(
        "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
    )()
    result = json.loads(await reset_test_dir(test_dir=str(workspace), force=False))

    assert result["success"] is False
    assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]
    assert "ok_file" in result["deleted"]


@pytest.mark.anyio
async def test_reset_workspace_returns_partial_failure_json(tool_ctx, tmp_path):
    """reset_workspace returns structured JSON on partial failure."""
    tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".autoskillit-workspace").write_text("# marker\n")

    tool_ctx.runner.push(_make_result(0, "", ""))

    mock_result = CleanupResult(
        deleted=["ok_file"],
        failed=[("bad_dir", "PermissionError: denied")],
        skipped=[".cache"],
    )
    tool_ctx.workspace_mgr = type(
        "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
    )()
    result = json.loads(await reset_workspace(test_dir=str(workspace)))

    assert result["success"] is False
    assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]


class TestTestCheckTiming:
    """test_check records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_test_check_step_name_records_timing(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        await test_check("/tmp/wt", step_name="test_check")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "test_check" for e in report)

    @pytest.mark.anyio
    async def test_test_check_empty_step_name_skips_timing(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "= 10 passed =\n", ""))
        await test_check("/tmp/wt")
        assert tool_ctx.timing_log.get_report() == []


class TestResetTestDirTiming:
    """reset_test_dir records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_reset_test_dir_step_name_records_timing(self, tool_ctx, tmp_path):
        marker = tmp_path / ".autoskillit-workspace"
        marker.write_text("")
        mock_result = CleanupResult(deleted=[], failed=[], skipped=[])
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        await reset_test_dir(str(tmp_path), step_name="reset")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "reset" for e in report)

    @pytest.mark.anyio
    async def test_reset_test_dir_empty_step_name_skips_timing(self, tool_ctx, tmp_path):
        marker = tmp_path / ".autoskillit-workspace"
        marker.write_text("")
        mock_result = CleanupResult(deleted=[], failed=[], skipped=[])
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        await reset_test_dir(str(tmp_path))
        assert tool_ctx.timing_log.get_report() == []
