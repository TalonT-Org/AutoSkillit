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
    from tests.fakes import MockSubprocessRunner

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
    from tests.fakes import MockSubprocessRunner

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
    """parse_stdout_json fixture correctly parses captured stdout JSON."""
    import json

    print(json.dumps({"result": "ok", "count": 3}))
    data = parse_stdout_json()
    assert data == {"result": "ok", "count": 3}


def test_pytest_timeout_is_configured(pytestconfig):
    """pytest-timeout must be installed with a finite timeout ceiling."""
    timeout = pytestconfig.getini("timeout")
    assert timeout is not None and int(timeout) > 0, (
        "pytest-timeout is not configured. "
        "Install pytest-timeout and add timeout = 60 to [tool.pytest.ini_options]."
    )


def test_tool_ctx_log_dir_is_isolated_from_production(tool_ctx):
    """tool_ctx must override log_dir to a tmp path, never the production XDG dir."""
    import os

    log_dir = tool_ctx.config.linux_tracing.log_dir
    assert log_dir != ""
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    production_path = os.path.join(xdg, "autoskillit", "logs")
    assert not os.path.abspath(log_dir).startswith(production_path)


def test_minimal_ctx_imports_only_core_pipeline_and_config():
    """minimal_ctx fixture must only import from autoskillit.core, .pipeline, and .config."""
    import ast
    from pathlib import Path

    conftest_path = Path(__file__).parent / "conftest.py"
    tree = ast.parse(conftest_path.read_text(), filename=str(conftest_path))

    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "minimal_ctx":
            func = node
            break
    assert func is not None, "minimal_ctx fixture not found in conftest.py"

    ALLOWED_PREFIXES = ("autoskillit.core", "autoskillit.pipeline", "autoskillit.config")

    violations = []
    for node in ast.walk(func):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("autoskillit.")
        ):
            if not any(node.module.startswith(p) for p in ALLOWED_PREFIXES):
                violations.append(node.module)

    assert not violations, (
        f"minimal_ctx imports from forbidden modules: {violations}. "
        f"Only autoskillit.core, autoskillit.pipeline, and autoskillit.config are allowed."
    )


def test_minimal_ctx_has_no_server_factory_dependency():
    """minimal_ctx must not import from autoskillit.server or reference make_context."""
    import ast
    from pathlib import Path

    conftest_path = Path(__file__).parent / "conftest.py"
    tree = ast.parse(conftest_path.read_text(), filename=str(conftest_path))

    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "minimal_ctx":
            func = node
            break
    assert func is not None, "minimal_ctx fixture not found in conftest.py"

    for node in ast.walk(func):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("autoskillit.server"), (
                f"minimal_ctx imports from server module: {node.module}"
            )
            names = [alias.name for alias in node.names]
            assert "make_context" not in names, (
                "minimal_ctx imports make_context — use direct ToolContext construction"
            )


def test_clear_headless_env_no_server_import():
    """_clear_headless_env must not import from autoskillit.server."""
    import ast
    from pathlib import Path

    conftest_path = Path(__file__).parent / "conftest.py"
    tree = ast.parse(conftest_path.read_text(), filename=str(conftest_path))

    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_clear_headless_env":
            func = node
            break
    assert func is not None, "_clear_headless_env fixture not found in conftest.py"

    for node in ast.walk(func):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("autoskillit.server"), (
                f"_clear_headless_env imports from server module: {node.module}. "
                f"MCP tag resets belong in tests/server/conftest.py."
            )


def test_minimal_ctx_provides_isolated_gate(minimal_ctx):
    """minimal_ctx fixture provides a ToolContext with gate enabled."""
    from autoskillit.pipeline.gate import DefaultGateState

    assert isinstance(minimal_ctx.gate, DefaultGateState)
    assert minimal_ctx.gate.enabled is True


def test_is_test_feature_enabled_reads_project_config(monkeypatch):
    """When AUTOSKILLIT_TEST_FEATURES is unset, fleet resolves True via experimental_enabled."""
    monkeypatch.delenv("AUTOSKILLIT_TEST_FEATURES", raising=False)
    from tests.conftest import _is_test_feature_enabled, _resolve_test_config

    _resolve_test_config.cache_clear()
    try:
        result = _is_test_feature_enabled("fleet", env_val=None)
        assert result is True
    finally:
        _resolve_test_config.cache_clear()


def test_is_test_feature_enabled_dynaconf_env_overrides(monkeypatch):
    """AUTOSKILLIT_FEATURES__FLEET=false overrides experimental_enabled in test resolution."""
    monkeypatch.delenv("AUTOSKILLIT_TEST_FEATURES", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_FEATURES__FLEET", "false")
    from tests.conftest import _is_test_feature_enabled, _resolve_test_config

    _resolve_test_config.cache_clear()
    try:
        result = _is_test_feature_enabled("fleet", env_val=None)
        assert result is False
    finally:
        _resolve_test_config.cache_clear()


def test_is_test_feature_enabled_respects_experimental_enabled(monkeypatch):
    """EXPERIMENTAL feature resolves True via experimental_enabled=True in config."""
    import autoskillit.core._type_constants as tc
    from autoskillit.core._type_constants import FeatureDef
    from autoskillit.core._type_enums import FeatureLifecycle
    from tests.conftest import _is_test_feature_enabled, _resolve_test_config

    monkeypatch.delenv("AUTOSKILLIT_TEST_FEATURES", raising=False)
    exp_feat = FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="test",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
        default_enabled=False,
    )
    monkeypatch.setitem(tc.FEATURE_REGISTRY, "conftest_test_exp", exp_feat)
    _resolve_test_config.cache_clear()
    try:
        # defaults.yaml has experimental_enabled=true, so EXPERIMENTAL features are enabled
        result = _is_test_feature_enabled("conftest_test_exp", env_val=None)
        assert result is True
    finally:
        _resolve_test_config.cache_clear()


def test_is_test_feature_enabled_disabled_lifecycle_always_false(monkeypatch):
    """_is_test_feature_enabled returns False for DISABLED feature regardless of config."""
    import autoskillit.core._type_constants as tc
    from autoskillit.core._type_constants import FeatureDef
    from autoskillit.core._type_enums import FeatureLifecycle
    from tests.conftest import _is_test_feature_enabled, _resolve_test_config

    monkeypatch.delenv("AUTOSKILLIT_TEST_FEATURES", raising=False)
    disabled_feat = FeatureDef(
        lifecycle=FeatureLifecycle.DISABLED,
        description="disabled test",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
    )
    monkeypatch.setitem(tc.FEATURE_REGISTRY, "conftest_test_disabled", disabled_feat)
    _resolve_test_config.cache_clear()
    try:
        result = _is_test_feature_enabled("conftest_test_disabled", env_val=None)
        assert result is False
    finally:
        _resolve_test_config.cache_clear()
