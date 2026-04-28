"""Tests for autoskillit server status tools: quota events, telemetry writing, and DB access."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.config import AutomationConfig, ReadDbConfig
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools_status import (
    get_quota_events,
    read_db,
    write_telemetry_files,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestGetQuotaEvents:
    @pytest.mark.anyio
    async def test_returns_events_from_jsonl(self, tool_ctx, tmp_path, monkeypatch):
        events = [
            {
                "ts": "2026-03-10T10:00:00+00:00",
                "event": "approved",
                "threshold": 85.0,
                "utilization": 50.0,
            },
            {
                "ts": "2026-03-10T11:00:00+00:00",
                "event": "blocked",
                "threshold": 85.0,
                "utilization": 92.5,
            },
        ]
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "quota_events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n"
        )
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = json.loads(await get_quota_events())
        assert result["total_count"] == 2
        assert result["events"][0]["event"] == "blocked"  # most recent first

    @pytest.mark.anyio
    async def test_limits_to_n_events(self, tool_ctx, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        lines = [
            json.dumps({"ts": f"2026-03-10T{h:02d}:00:00+00:00", "event": "approved"})
            for h in range(10)
        ]
        (log_dir / "quota_events.jsonl").write_text("\n".join(lines) + "\n")
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = json.loads(await get_quota_events(n=3))
        assert len(result["events"]) == 3
        assert result["total_count"] == 10
        # most-recent-first: hours 09, 08, 07
        assert result["events"][0]["ts"] == "2026-03-10T09:00:00+00:00"
        assert result["events"][1]["ts"] == "2026-03-10T08:00:00+00:00"
        assert result["events"][2]["ts"] == "2026-03-10T07:00:00+00:00"

    @pytest.mark.anyio
    async def test_returns_empty_when_file_missing(self, tool_ctx, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = json.loads(await get_quota_events())
        assert result["events"] == []
        assert result["total_count"] == 0


class TestWriteTelemetryFiles:
    @pytest.mark.anyio
    async def test_writes_token_summary_markdown(self, tool_ctx, tmp_path):
        tool_ctx.token_log.record(
            "step1",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        path = Path(result["token_summary_path"])
        assert path.exists()
        content = path.read_text()
        assert "step1" in content
        # Format-structural assertions (table, not bullet list)
        assert "| Step |" in content
        assert "|---" in content
        assert "- input_tokens:" not in content
        assert "# Token Summary" not in content

    @pytest.mark.anyio
    async def test_writes_timing_summary_markdown(self, tool_ctx, tmp_path):
        tool_ctx.timing_log.record("step1", 12.5)
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        path = Path(result["timing_summary_path"])
        assert path.exists()
        content = path.read_text()
        assert "step1" in content
        # Format-structural assertions (table, not bullet list)
        assert "| Step |" in content
        assert "|---" in content
        assert "- total_seconds:" not in content
        assert "# Timing Summary" not in content

    @pytest.mark.anyio
    async def test_token_file_uses_wall_clock_seconds(self, tool_ctx, tmp_path):
        """write_telemetry_files merges wall_clock_seconds from timing log."""
        tool_ctx.token_log.record(
            "deploy",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            elapsed_seconds=5.0,
        )
        tool_ctx.timing_log.record("deploy", 120.0)
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        content = Path(result["token_summary_path"]).read_text()
        # Should show 2m 0s (wall_clock=120), not 5s (elapsed)
        assert "2m 0s" in content

    @pytest.mark.anyio
    async def test_creates_output_dir_if_missing(self, tool_ctx, tmp_path):
        out = str(tmp_path / "nested" / "telemetry")
        result = json.loads(await write_telemetry_files(out))
        assert Path(result["token_summary_path"]).exists()
        assert Path(result["timing_summary_path"]).exists()

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx, tmp_path):
        tool_ctx.gate.disable()
        result = json.loads(await write_telemetry_files(str(tmp_path)))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"


# ---------------------------------------------------------------------------
# TestReadDb — moved from test_tools_workspace.py (tools_status owns read_db)
# ---------------------------------------------------------------------------


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

    from tests.fakes import InMemoryDatabaseReader

    reader = InMemoryDatabaseReader(query_result={"rows": [], "count": 0})
    tool_ctx.db_reader = reader

    db_path = str(tmp_path / "test.db")
    # Create an empty sqlite db so path-exists check passes
    _sqlite3.connect(db_path).close()
    await read_db(db_path, "SELECT 1")
    assert len(reader.calls) == 1
    assert "SELECT 1" in reader.calls[0]["sql"]


# T3 — read_db error paths include success: False
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_db_all_error_paths_include_success_false(tool_ctx, monkeypatch):
    """Every read_db error path must return success=False."""
    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", tool_ctx)

    # Nonexistent db path — exercises the "does not exist" error branch
    result = json.loads(await read_db(db_path="/nonexistent/path/db.sqlite", query="SELECT 1"))
    assert result.get("success") is False
    assert "error" in result
