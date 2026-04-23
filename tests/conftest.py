"""Shared test fixtures for autoskillit."""

import os
from pathlib import Path as _Path

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    SubprocessResult,
    TerminationReason,
)
from tests._helpers import _flush_structlog_proxy_caches
from tests.fakes import MockSubprocessRunner

_LAYER_DIRS: frozenset[str] = frozenset(
    {
        "core",
        "config",
        "pipeline",
        "execution",
        "workspace",
        "recipe",
        "migration",
        "franchise",
        "server",
        "cli",
    }
)

_SIZE_DIRS: frozenset[str] = frozenset(
    {
        "cli",
        "config",
        "core",
        "execution",
        "franchise",
        "migration",
        "pipeline",
        "recipe",
        "server",
        "workspace",
    }
)

_scope_key = pytest.StashKey[set[_Path] | None]()
_filter_mode_key = pytest.StashKey[str | None]()
_selected_count_key = pytest.StashKey[int | None]()
_deselected_count_key = pytest.StashKey[int | None]()


class TimeoutTier:
    """Centralized timeout tiers encoding xdist -n 4 budget math.

    CHANNEL_B minimum: 1s preamble + _phase1_timeout (30s) + drain + jitter > 31.5s.
    """

    UNIT = 10  # Pure logic, no I/O
    INTEGRATION = 30  # Filesystem/subprocess, no Channel B
    CHANNEL_B = 60  # Full session_log_dir + Channel B path


@pytest.fixture(autouse=True)
def _structlog_to_null():
    """Prevent structlog from writing to stdout in any test.

    In the default state (before configure_logging() is called), structlog's
    PrintLoggerFactory routes all log output to sys.stdout. Tests that use
    capsys to inspect stdout are silently corrupted when a mock bypass causes
    a real production function to log.

    Two-layer isolation strategy:

    1. Primary: ``structlog.configure(cache_logger_on_first_use=False)`` — the
       official structlog recommendation for test environments. Prevents proxy
       caches from being populated during tests, so ``reset_defaults()`` is
       sufficient after each test without manual cache surgery.

    2. Secondary: ``_flush_structlog_proxy_caches()`` — repairs loggers that
       were cached before this fixture ran (e.g., module-level loggers cached
       at import time before the fixture had a chance to set
       cache_logger_on_first_use=False).

    Then wraps the test in ``capture_logs()`` to drop all log output.

    Note: TestConfigureLogging in test_logging.py has its own class-scoped
    ``_structlog_to_null`` no-op override and ``_reset_structlog`` fixture that
    owns structlog state management for those tests.
    """
    import structlog
    import structlog.testing

    structlog.configure(cache_logger_on_first_use=False)
    _flush_structlog_proxy_caches()
    with structlog.testing.capture_logs():
        yield
    structlog.reset_defaults()


def _make_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
    session_id: str = "",
    channel_b_session_id: str = "",
) -> SubprocessResult:
    """Create a SubprocessResult for mocking run_managed_async."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination_reason,
        pid=12345,
        channel_confirmation=channel_confirmation,
        session_id=session_id,
        channel_b_session_id=channel_b_session_id,
    )


def _make_timeout_result(stdout: str = "", stderr: str = "") -> SubprocessResult:
    """Create a timed-out SubprocessResult."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.TIMED_OUT,
        pid=12345,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )


@pytest.fixture
def parse_stdout_json(capsys):
    """Parse capsys-captured stdout as JSON with diagnostic context on failure.

    Replaces bare ``json.loads(capsys.readouterr().out)`` calls. When parsing
    fails, raises AssertionError showing the full raw stdout and stderr content,
    so the developer immediately sees what was captured rather than getting an
    opaque JSONDecodeError with no context.

    Usage::

        def test_quota_status_outputs_json(self, monkeypatch, parse_stdout_json, tmp_path):
            cli.quota_status()
            data = parse_stdout_json()
            assert "should_sleep" in data
    """
    import json

    def _parse() -> dict:
        captured = capsys.readouterr()
        try:
            return json.loads(captured.out)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"stdout is not valid JSON.\n"
                f"  parse error : {exc}\n"
                f"  stdout      : {captured.out!r}\n"
                f"  stderr      : {captured.err!r}"
            ) from exc

    return _parse


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path_factory):
    """Redirect Path.home() to a per-test temp directory.

    Prevents the developer's real ~/.autoskillit/config.yaml from being
    loaded during tests. Without this, tests that call load_config() without
    mocking Path.home() would fail if the real user config contains
    secrets-only keys (e.g. github.token) that are now rejected by strict
    schema validation.

    Uses tmp_path_factory (not tmp_path) so the isolated home is created
    outside the test's own tmp_path, avoiding pollution in tests that check
    tmp_path is empty or operate on its contents directly.

    Tests that need a specific home structure override this by calling:
        monkeypatch.setattr("pathlib.Path.home", lambda: my_home)
    """
    isolated_home = tmp_path_factory.mktemp("isolated-home")
    monkeypatch.setattr("pathlib.Path.home", lambda: isolated_home)


@pytest.fixture(autouse=True)
def _clear_headless_env(monkeypatch):
    """Ensure AUTOSKILLIT_HEADLESS is unset at the start of every test.

    Tools check this env var to block calls from headless sessions.
    MCP tag resets are handled by tests/server/conftest.py for server tests.
    """
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)


@pytest.fixture(autouse=True)
def _clear_session_type_env(monkeypatch):
    """Prevent SESSION_TYPE leaking between tests."""
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)


@pytest.fixture(autouse=True)
def _clear_skip_stale_check_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)


@pytest.fixture(scope="function")
def anyio_backend():
    """Lock all @pytest.mark.anyio tests to the asyncio backend."""
    return "asyncio"


@pytest.fixture
def minimal_ctx(tmp_path):
    """Lightweight ToolContext using only L0+L1 imports (core, pipeline, config).

    Use for tests that only need gate, audit, token_log, timing_log, or config —
    no server factory, no L2/L3 service wiring. Importing this fixture does NOT
    pull in autoskillit.server, autoskillit.execution, autoskillit.recipe,
    autoskillit.migration, or autoskillit.workspace.

    Tests that need full service wiring (executor, tester, recipes, etc.) should
    use tool_ctx instead.
    """
    from autoskillit.config import AutomationConfig
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.pipeline.timings import DefaultTimingLog
    from autoskillit.pipeline.tokens import DefaultTokenLog

    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=True),
        plugin_dir=None,
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
    )
    return ctx


@pytest.fixture
def tool_ctx(monkeypatch, tmp_path):
    """Provide a fully isolated ToolContext for server integration tests.

    Full-stack fixture: calls make_context() from server/_factory.py, which
    imports ALL production layers (L0–L3). Use minimal_ctx instead when the
    test only needs gate, audit, token_log, timing_log, or config fields.

    Monkeypatches server._ctx so all server tool calls use this context.
    Gate is enabled (open kitchen) by default — tests that need a closed
    gate should do: tool_ctx.gate = DefaultGateState(enabled=False) locally.

    All service fields (executor, tester, db_reader, workspace_mgr, recipes,
    migrations) are wired via make_context() so routing tests work correctly.
    """
    from autoskillit.config import AutomationConfig
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server import _state
    from autoskillit.server._factory import make_context

    mock_runner = MockSubprocessRunner()
    ctx = make_context(AutomationConfig(), runner=mock_runner, plugin_dir=str(tmp_path))
    ctx.gate = DefaultGateState(enabled=True)
    ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")
    ctx.config.linux_tracing.tmpfs_path = str(tmp_path / "shm")
    # Anchor temp_dir to tmp_path so server tools that read from ctx.temp_dir
    # (e.g. _apply_triage_gate's staleness cache) write under the per-test
    # tmp directory rather than the cwd captured at fixture-init time.
    ctx.temp_dir = tmp_path / ".autoskillit" / "temp"
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)
    return ctx


# ---------------------------------------------------------------------------
# Test filter hooks (opt-in via AUTOSKILLIT_TEST_FILTER env var)
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--filter-mode",
        default=None,
        choices=("none", "conservative", "aggressive"),
        help="Test filter mode (overrides AUTOSKILLIT_TEST_FILTER env var).",
    )
    parser.addoption(
        "--filter-base-ref",
        default=None,
        help="Git base ref for changed-file detection (overrides AUTOSKILLIT_TEST_BASE_REF).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Compute test filter scope from env var + git diff + manifest.

    Opt-in via AUTOSKILLIT_TEST_FILTER env var or --filter-mode CLI flag.
    Fail-open: any error sets scope to None (full test run).
    """
    import warnings

    config.stash[_scope_key] = None
    config.stash[_filter_mode_key] = None

    cli_mode = config.getoption("--filter-mode", default=None)
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")

    if not cli_mode and not env_val:
        return
    if not cli_mode and env_val.lower() in ("0", "false", "no"):
        return

    try:
        from tests._test_filter import (
            FilterMode,
            build_test_scope,
            git_changed_files,
            load_manifest,
        )

        if cli_mode:
            mode = FilterMode(cli_mode)
        elif env_val.lower() in ("1", "true", "yes"):
            mode = FilterMode.CONSERVATIVE
        else:
            mode = FilterMode(env_val)

        if mode == FilterMode.NONE:
            return

        cli_base_ref = config.getoption("--filter-base-ref", default=None)
        changed = git_changed_files(config.rootpath, base_ref=cli_base_ref)

        manifest = load_manifest(config.rootpath)
        coverage_map_path = config.rootpath / ".autoskillit" / "test-source-map.json"

        scope = build_test_scope(
            changed_files=changed,
            mode=mode,
            manifest=manifest,
            tests_root=config.rootpath / "tests",
            coverage_map_path=coverage_map_path,
        )
        config.stash[_scope_key] = scope
        config.stash[_filter_mode_key] = mode.value

    except Exception as exc:
        warnings.warn(
            f"Test filter setup failed, running all tests: {exc}",
            stacklevel=1,
        )


def _is_test_feature_enabled(feature_name: str, *, env_val: str | None) -> bool:
    """Return True if feature_name is enabled for this test run.

    Resolution order:
    1. If AUTOSKILLIT_TEST_FEATURES is set (including empty string), parse it
       as a comma-separated list of enabled feature names.  Only listed names
       are enabled; all others are disabled.
    2. If unset, fall back to FEATURE_REGISTRY[name].default_enabled.
       Unknown feature names return True (fail-open: don't skip unrecognised names).

    Args:
        feature_name: The feature name to check.
        env_val: Pre-read value of AUTOSKILLIT_TEST_FEATURES (pass ``None`` when unset).
    """
    if env_val is not None:
        enabled = {f.strip() for f in env_val.split(",") if f.strip()}
        return feature_name in enabled
    from autoskillit.core import FEATURE_REGISTRY

    defn = FEATURE_REGISTRY.get(feature_name)
    if defn is None:
        import warnings

        warnings.warn(
            f"pytest.mark.feature({feature_name!r}) references an unknown feature; "
            "fail-open assumed (test will run). Check for typos in the marker.",
            stacklevel=4,
        )
        return True
    return defn.default_enabled


def pytest_collection_modifyitems(
    items: list[pytest.Item],
    config: pytest.Config,
) -> None:
    """Deselect test items outside the computed filter scope.

    Fail-open: any error leaves all items selected.
    """
    import warnings

    # Layer marker mismatch validation (controller-only under xdist)
    if not hasattr(config, "workerinput"):
        tests_root = config.rootpath / "tests"
        for item in items:
            try:
                rel = item.path.relative_to(tests_root)
            except (ValueError, TypeError):
                continue
            parts = rel.parts
            if not parts or parts[0] not in _LAYER_DIRS:
                continue
            expected_dir = parts[0]

            for mark in item.iter_markers("layer"):
                if mark.args and mark.args[0] != expected_dir:
                    warnings.warn(
                        f"Layer marker mismatch: {item.nodeid} has layer('{mark.args[0]}') "
                        f"but lives in tests/{expected_dir}/",
                        stacklevel=1,
                    )

    # Feature gate pass — orthogonal to layer/size, runs on every worker
    _test_features_env = os.environ.get("AUTOSKILLIT_TEST_FEATURES")
    for item in items:
        marker = item.get_closest_marker("feature")
        if marker and marker.args:
            feature_name = marker.args[0]
            if not isinstance(feature_name, str):
                warnings.warn(
                    f"pytest.mark.feature() received a non-string argument {feature_name!r} "
                    f"on {item.nodeid}; marker will be ignored.",
                    stacklevel=1,
                )
                continue
            if not _is_test_feature_enabled(feature_name, env_val=_test_features_env):
                env_display = _test_features_env or ""
                item.add_marker(
                    pytest.mark.skip(
                        reason=(
                            f"feature '{feature_name}' disabled"
                            f" (AUTOSKILLIT_TEST_FEATURES='{env_display}'"
                            f" does not include '{feature_name}')"
                        )
                    )
                )

    scope: set[_Path] | None = config.stash.get(_scope_key, None)
    if scope is None:
        return

    try:
        root = config.rootpath
        scope_abs: set[_Path] = set()
        for p in scope:
            scope_abs.add(p if p.is_absolute() else root / p)

        selected: list[pytest.Item] = []
        deselected: list[pytest.Item] = []

        for item in items:
            item_path = item.path
            matched = False
            for sp in scope_abs:
                if sp.is_file():
                    if item_path == sp:
                        matched = True
                        break
                else:
                    try:
                        item_path.relative_to(sp)
                        matched = True
                        break
                    except ValueError:
                        continue
            if matched:
                selected.append(item)
            else:
                deselected.append(item)

        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected
            warnings.warn(
                f"Test filter: {len(selected)} selected, {len(deselected)} deselected "
                f"({len(scope)} scope paths)",
                stacklevel=1,
            )

        config.stash[_selected_count_key] = len(items)
        config.stash[_deselected_count_key] = len(deselected)

    except Exception as exc:
        warnings.warn(
            f"Test filter deselection failed, running all tests: {exc}",
            stacklevel=1,
        )

    # --- Size-based deselection (aggressive mode only) ---
    filter_mode = config.stash.get(_filter_mode_key, None)
    if filter_mode == "aggressive":
        _SIZE_MARKERS = {"small", "medium", "large"}
        size_selected: list[pytest.Item] = []
        size_deselected: list[pytest.Item] = []

        for item in items:
            size_marks = [m.name for m in item.iter_markers() if m.name in _SIZE_MARKERS]
            effective_size = size_marks[0] if size_marks else "large"
            if effective_size in ("small", "medium"):
                size_selected.append(item)
            else:
                size_deselected.append(item)

        if size_deselected:
            config.hook.pytest_deselected(items=size_deselected)
            items[:] = size_selected
            warnings.warn(
                f"Size filter (aggressive): {len(size_selected)} selected, "
                f"{len(size_deselected)} large/unannotated deselected",
                stacklevel=1,
            )
            prev_deselected = config.stash.get(_deselected_count_key, None) or 0
            config.stash[_selected_count_key] = len(size_selected)
            config.stash[_deselected_count_key] = prev_deselected + len(size_deselected)


def pytest_sessionfinish(session, exitstatus):
    """Write filter stats sidecar for DefaultTestRunner consumption."""
    if hasattr(session.config, "workerinput"):
        return  # xdist worker — only the controller writes the sidecar
    out_path = os.environ.get("AUTOSKILLIT_FILTER_STATS_FILE")
    if not out_path:
        return
    filter_mode = session.config.stash.get(_filter_mode_key, None)
    selected = session.config.stash.get(_selected_count_key, None)
    deselected = session.config.stash.get(_deselected_count_key, None)
    if filter_mode is None:
        return
    import json

    _Path(out_path).write_text(
        json.dumps(
            {
                "filter_mode": filter_mode,
                "tests_selected": selected,
                "tests_deselected": deselected,
            }
        )
    )
