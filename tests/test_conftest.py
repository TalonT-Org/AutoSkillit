"""Tests for conftest fixture infrastructure: tool_ctx and MockSubprocessRunner."""

from pathlib import Path

from autoskillit.core.types import SubprocessResult, TerminationReason


def test_tool_ctx_provides_isolated_gate(tool_ctx):
    """tool_ctx fixture provides a ToolContext with gate enabled."""
    from autoskillit.pipeline.gate import DefaultGateState

    assert isinstance(tool_ctx.gate, DefaultGateState)
    assert tool_ctx.gate.enabled is True


def test_tool_ctx_provides_isolated_audit(tool_ctx):
    """tool_ctx fixture provides a fresh AuditLog with no records."""
    assert tool_ctx.audit.get_report() == []


def test_tool_ctx_provides_isolated_token_log(tool_ctx):
    """tool_ctx fixture provides a fresh TokenLog with no entries."""
    assert tool_ctx.token_log.get_report() == []


async def test_mock_subprocess_runner_push_and_pop(tmp_path: Path):
    """MockSubprocessRunner.push() queues results, __call__ pops in order."""
    from tests.conftest import MockSubprocessRunner

    runner = MockSubprocessRunner()
    r1 = SubprocessResult(0, "out1", "", TerminationReason.NATURAL_EXIT, 100)
    r2 = SubprocessResult(1, "out2", "err", TerminationReason.NATURAL_EXIT, 101)
    runner.push(r1)
    runner.push(r2)

    got1 = await runner(["cmd"], cwd=tmp_path, timeout=30.0)
    got2 = await runner(["cmd"], cwd=tmp_path, timeout=30.0)
    assert got1 is r1
    assert got2 is r2


async def test_mock_subprocess_runner_default_when_empty(tmp_path: Path):
    """MockSubprocessRunner returns a zero-exit default when queue is empty."""
    from tests.conftest import MockSubprocessRunner

    runner = MockSubprocessRunner()
    result = await runner(["cmd"], cwd=tmp_path, timeout=30.0)
    assert result.returncode == 0


def test_reset_structlog_autouse_removed():
    """_reset_structlog must not exist as a module-level fixture in conftest.

    It was vestigial — TestConfigureLogging in test_logging.py already owns
    its class-scoped structlog reset. Other tests never call configure_logging().
    """
    import tests.conftest as conftest_module

    assert not hasattr(conftest_module, "_reset_structlog"), (
        "_reset_structlog autouse fixture must be removed from conftest.py; "
        "test_logging.py.TestConfigureLogging provides its own class-scoped reset"
    )


def test_reset_audit_log_autouse_removed():
    """_reset_audit_log must not exist as a module-level fixture in conftest.

    The module-level _audit_log singleton is never written to during tests —
    serve() constructs fresh AuditLog() instances, and tool_ctx provides
    per-test isolated instances. The autouse reset was a no-op.
    """
    import tests.conftest as conftest_module

    assert not hasattr(conftest_module, "_reset_audit_log"), (
        "_reset_audit_log autouse fixture must be removed from conftest.py; "
        "test isolation is provided by the tool_ctx fixture via ToolContext DI"
    )


def test_reset_token_log_autouse_removed():
    """_reset_token_log must not exist as a module-level fixture in conftest.

    The module-level _token_log singleton is never written to during tests —
    serve() constructs fresh TokenLog() instances, and tool_ctx provides
    per-test isolated instances. The autouse reset was a no-op.
    """
    import tests.conftest as conftest_module

    assert not hasattr(conftest_module, "_reset_token_log"), (
        "_reset_token_log autouse fixture must be removed from conftest.py; "
        "test isolation is provided by the tool_ctx fixture via ToolContext DI"
    )


def test_flush_logger_proxy_caches_removed_from_conftest():
    """_flush_logger_proxy_caches must not be defined in conftest.

    The conftest.py copy was only used by the removed _reset_structlog fixture.
    test_logging.py maintains its own copy for TestConfigureLogging.
    """
    import tests.conftest as conftest_module

    assert not hasattr(conftest_module, "_flush_logger_proxy_caches"), (
        "_flush_logger_proxy_caches must be removed from conftest.py; "
        "it was only used by the removed _reset_structlog fixture"
    )


def test_structlog_does_not_write_to_stdout_in_tests(capsys):
    """Structlog log calls must never pollute stdout, even in default state.

    Before configure_logging() is called, structlog's default PrintLogger
    writes to sys.stdout. The autouse _structlog_to_null fixture must intercept
    all log output before it reaches stdout.
    """
    from autoskillit.execution.quota import _log as quota_log

    quota_log.warning("test_sentinel_should_not_reach_stdout", probe=True)
    captured = capsys.readouterr()
    assert captured.out == "", f"Structlog wrote to stdout during a test. stdout: {captured.out!r}"


def test_parse_stdout_json_fixture_is_available(parse_stdout_json):
    """The parse_stdout_json fixture must exist and be importable from conftest."""
    assert callable(parse_stdout_json)


def test_pytest_timeout_is_configured(pytestconfig):
    """pytest-timeout must be installed with a finite timeout ceiling."""
    timeout = pytestconfig.getini("timeout")
    assert timeout is not None and int(timeout) > 0, (
        "pytest-timeout is not configured. "
        "Install pytest-timeout and add timeout = 60 to [tool.pytest.ini_options]."
    )
