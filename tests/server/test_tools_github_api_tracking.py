import pytest
from unittest.mock import patch, MagicMock
from autoskillit.core._type_subprocess import TerminationReason, SubprocessResult
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio]


async def test_gh_cli_call_is_recorded(build_ctx):
    from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog
    log = DefaultGitHubApiLog()
    ctx = build_ctx(github_api_log=log)
    runner = MockSubprocessRunner()
    runner.set_default(SubprocessResult(
        returncode=0, stdout="[]", stderr="",
        termination=TerminationReason.NATURAL_EXIT, pid=1,
    ))
    ctx.runner = runner

    from autoskillit.server.helpers import _run_subprocess
    with patch("autoskillit.server.helpers._get_ctx", return_value=ctx):
        await _run_subprocess(["gh", "pr", "list", "--json", "number"], cwd="/tmp", timeout=30)

    usage = log.to_usage("sess-1")
    assert usage is not None
    assert usage["total_requests"] == 1
    assert usage["by_source"]["gh_cli"] == 1


async def test_non_gh_command_is_not_recorded(build_ctx):
    from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog
    log = DefaultGitHubApiLog()
    ctx = build_ctx(github_api_log=log)
    runner = MockSubprocessRunner()
    runner.set_default(SubprocessResult(
        returncode=0, stdout="ok", stderr="",
        termination=TerminationReason.NATURAL_EXIT, pid=1,
    ))
    ctx.runner = runner

    from autoskillit.server.helpers import _run_subprocess
    with patch("autoskillit.server.helpers._get_ctx", return_value=ctx):
        await _run_subprocess(["git", "status"], cwd="/tmp", timeout=30)

    assert log.to_usage("sess-1") is None


async def test_gh_cli_records_exit_code_and_latency(build_ctx):
    from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog
    log = DefaultGitHubApiLog()
    ctx = build_ctx(github_api_log=log)
    runner = MockSubprocessRunner()
    runner.set_default(SubprocessResult(
        returncode=1, stdout="", stderr="error",
        termination=TerminationReason.NATURAL_EXIT, pid=1,
    ))
    ctx.runner = runner

    from autoskillit.server.helpers import _run_subprocess
    with patch("autoskillit.server.helpers._get_ctx", return_value=ctx):
        await _run_subprocess(["gh", "api", "repos/o/r/issues"], cwd="/tmp", timeout=30)

    usage = log.to_usage("sess-1")
    assert usage["total_requests"] == 1
    assert usage["total_latency_ms"] >= 0


async def test_flush_session_log_writes_github_api_usage(tmp_path):
    from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog
    from autoskillit.execution.session_log import flush_session_log
    import json

    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET", path="/repos/o/r/issues/1", status_code=200,
        latency_ms=100.0, rate_limit_remaining=4900, rate_limit_used=100,
        rate_limit_reset=0, timestamp="2026-04-27T10:00:00Z",
    )

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="test-session",
        pid=1234,
        skill_command="test",
        success=True,
        subtype="headless",
        exit_code=0,
        start_ts="2026-04-27T10:00:00Z",
        proc_snapshots=None,
        github_api_log=log,
    )

    usage_file = tmp_path / "sessions" / "test-session" / "github_api_usage.json"
    assert usage_file.exists()
    data = json.loads(usage_file.read_text())
    assert data["session_id"] == "test-session"
    assert data["total_requests"] == 1

    summary_file = tmp_path / "sessions" / "test-session" / "summary.json"
    summary = json.loads(summary_file.read_text())
    assert summary["github_api_requests"] == 1

    index_file = tmp_path / "sessions.jsonl"
    lines = [json.loads(l) for l in index_file.read_text().splitlines()]
    assert lines[0]["github_api_requests"] == 1
