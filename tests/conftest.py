"""Shared test fixtures for autoskillit."""

import functools
import os
from pathlib import Path as _Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from autoskillit.config.settings import AutomationConfig

from autoskillit.core import (
    SKILL_PROJECTION_VERSION,
    InspectorCallback,
    InspectorEvidence,
    InspectorVerdict,
)
from autoskillit.core.types import (
    ChannelConfirmation,
    SubprocessResult,
    TerminationReason,
)
from tests._helpers import _collect_structlog_proxies, _flush_structlog_proxy_caches

_LAYER_DIRS: frozenset[str] = frozenset(
    {
        "core",
        "config",
        "pipeline",
        "execution",
        "exploration",
        "workspace",
        "recipe",
        "migration",
        "fleet",
        "planner",
        "server",
        "cli",
    }
)

_SIZE_DIRS: frozenset[str] = frozenset(
    {
        "arch",
        "assets",
        "backend",
        "cli",
        "config",
        "contracts",
        "core",
        "docs",
        "execution",
        "exploration",
        "fleet",
        "hooks",
        "infra",
        "integration",
        "migration",
        "pipeline",
        "planner",
        "recipe",
        "report",
        "server",
        "skills",
        "skills_extended",
        "workspace",
    }
)

_scope_key = pytest.StashKey[set[_Path] | None]()
_filter_mode_key = pytest.StashKey[str | None]()
_selected_count_key = pytest.StashKey[int | None]()
_deselected_count_key = pytest.StashKey[int | None]()
_full_run_reason_key = pytest.StashKey[str | None]()
_feature_scope_key = pytest.StashKey[dict[str, bool] | None]()

# Module-level accumulator for xdist worker-to-controller IPC.
# Populated by pytest_testnodedown (controller); cleared by pytest_configure
# at session start so in-process pytester reruns don't leak stale data.
_worker_filter_counts: dict[str, int | None] = {}
_worker_feature_scope: dict[str, bool] = {}


class TimeoutTier:
    """Centralized timeout tiers encoding xdist -n 4 budget math.

    CHANNEL_B minimum: 1s preamble + _phase1_timeout (30s) + drain + jitter > 31.5s.
    """

    UNIT = 10  # Pure logic, no I/O
    INTEGRATION = 30  # Filesystem/subprocess, no Channel B
    CHANNEL_B = 60  # Full session_log_dir + Channel B path


_structlog_proxies: list[object] = []


@pytest.fixture(autouse=True, scope="session")
def _detect_tmp_git_contamination():
    """Fail fast if /tmp/.git is a structurally valid git repo.

    An empty /tmp/.git directory is harmless — _find_git_ancestor() walks past
    it (no HEAD file).  Only a valid git repo at /tmp contaminates tests that
    use cwd='/tmp'.
    """
    tmp_git = _Path("/tmp/.git")
    if tmp_git.is_dir() and (tmp_git / "HEAD").is_file():
        pytest.fail(
            "/tmp/.git exists with a valid HEAD — this contaminates "
            "is_git_main_checkout() for tests using cwd='/tmp'. "
            "Remove it before running the test suite."
        )
    if tmp_git.is_file():
        pytest.fail(
            "/tmp/.git is a worktree pointer file — this contaminates "
            "is_git_worktree() for tests using cwd='/tmp'. "
            "Remove it before running the test suite."
        )


@pytest.fixture(scope="session", autouse=True)
def _structlog_session_init():
    """One-time structlog proxy cache flush and proxy inventory per worker session.

    Repairs module-level loggers cached at import time (before any fixture ran)
    and collects all BoundLoggerLazyProxy instances for cheap per-test clearing.
    """
    import structlog

    structlog.configure(cache_logger_on_first_use=False)
    _flush_structlog_proxy_caches()
    _structlog_proxies.clear()
    _structlog_proxies.extend(_collect_structlog_proxies())


@pytest.fixture(autouse=True)
def _structlog_to_null():
    """Suppress structlog output in every test.

    Resets wrapper_class to BoundLogger (allowing all log levels through to
    LogCapture — core/logging.py sets BoundLoggerFilteringAtInfo which silently
    drops DEBUG events before processors see them). Clears cached ``bind``
    methods on known proxies so tests that call configure_logging() don't
    leak cached production loggers into subsequent tests.
    """
    import logging as _logging

    import structlog
    import structlog.testing

    structlog.configure(
        cache_logger_on_first_use=False,
        wrapper_class=structlog.make_filtering_bound_logger(_logging.DEBUG),
    )
    for proxy in _structlog_proxies:
        proxy.__dict__.pop("bind", None)
    structlog.contextvars.clear_contextvars()
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


def make_stub_inspector(verdict: str = "SPARE") -> InspectorCallback:
    """Return an InspectorCallback that always emits the given verdict action.

    Used by tests that need to inject a deterministic inspector callback without
    wiring up a real LLM. Default action is "SPARE" (no kill); pass "KILL" to
    exercise the kill path.
    """

    async def _stub(evidence: InspectorEvidence) -> InspectorVerdict:
        return InspectorVerdict(
            action=verdict,
            reasoning="stub",
            confidence="high",
            elapsed_seconds=0.0,
        )

    return _stub


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
def _clear_private_env(monkeypatch) -> None:
    """Clear ALL autoskillit-private env vars before every test.

    Iterates AUTOSKILLIT_PRIVATE_ENV_VARS ∪ _HEADLESS_EXCLUSIVE_VARS
    programmatically, so new vars are automatically covered without
    manual fixture additions.
    """
    from autoskillit.core.types._type_constants_env import AUTOSKILLIT_PRIVATE_ENV_VARS
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    for var in AUTOSKILLIT_PRIVATE_ENV_VARS | _HEADLESS_EXCLUSIVE_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clear_features_env(monkeypatch):
    """Clear ALL AUTOSKILLIT_FEATURES__* env vars before every test.

    Dynaconf reads env vars with prefix AUTOSKILLIT_ and injects them
    into the config dict at the highest priority layer. Feature-related
    env vars (AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED, FLEET, etc.)
    override the is_dev_install() auto-detection and config file values,
    breaking test isolation when CI sets these vars.

    Uses prefix-based scanning so new feature env vars are automatically
    covered without needing individual fixture additions.
    """
    for key in list(os.environ):
        if key.startswith("AUTOSKILLIT_FEATURES__"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_run_skill_env(monkeypatch):
    """Clear ALL AUTOSKILLIT_RUN_SKILL__* env vars before every test.

    Dynaconf reads env vars with prefix AUTOSKILLIT_ and injects them into the
    config dict at the highest priority layer. RunSkillConfig env vars
    (AUTOSKILLIT_RUN_SKILL__MAX_SUPPRESSION_SECONDS, etc.) override YAML/dataclass
    defaults when set by the pipeline harness, breaking test isolation.

    Uses prefix-based scanning so new run_skill env vars are automatically covered
    without needing individual fixture additions.
    """
    for key in list(os.environ):
        if key.startswith("AUTOSKILLIT_RUN_SKILL__"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_test_filter_env(monkeypatch):
    """Clear AUTOSKILLIT_TEST_FILTER and related env vars before every test.

    These env vars control test filtering behavior in CI and must not
    leak between tests or affect tests that don't explicitly need them.
    """
    monkeypatch.delenv("AUTOSKILLIT_TEST_FILTER", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_TEST_BASE_REF", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_FILTER_STATS_FILE", raising=False)


@pytest.fixture(autouse=True)
def _reset_mcp_visibility():
    import sys

    def _clear_transforms():
        from autoskillit.core import ALL_VISIBILITY_TAGS
        from autoskillit.server import mcp

        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})

    if "autoskillit.server" in sys.modules:
        _clear_transforms()
    yield
    if "autoskillit.server" in sys.modules:
        _clear_transforms()


@pytest.fixture(scope="function")
def anyio_backend():
    """Lock all @pytest.mark.anyio tests to the asyncio backend."""
    return "asyncio"


@pytest.fixture
def minimal_ctx(tmp_path):
    """Lightweight ToolContext using only L0+L1 imports (core, pipeline, config).

    Use for tests that only need gate, audit, token_log, timing_log, or config —
    no server factory or L2/L3 service wiring. The execution-layer launch resolver
    is included so tests that promote this context into a physical runner still
    cross the production authority boundary.

    Tests that need full service wiring (executor, tester, recipes, etc.) should
    use tool_ctx instead.
    """
    from autoskillit.config import AutomationConfig
    from autoskillit.core import (
        AuditAdmissionStoreAuthority,
        AuditAuthorityMaterializer,
        CommittedDispositionResolver,
        ContextAdmissionStoreAuthority,
    )
    from autoskillit.execution.launch_resolution import DefaultLaunchResolver
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.pipeline.context_admission_ledger import (
        DefaultContextAdmissionLedger,
    )
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.pipeline.timings import DefaultTimingLog
    from autoskillit.pipeline.tokens import DefaultTokenLog
    from tests.fakes import (
        FakeManagedHeadlessSessionLineageStore,
        FakePluginArtifactAuthority,
        FakeSkillSessionContractStore,
    )

    plugin_authority = FakePluginArtifactAuthority(tmp_path)
    audit_admission_ledger = DefaultAuditAdmissionLedger(
        AuditAdmissionStoreAuthority(
            database_path=(
                tmp_path / ".autoskillit" / "temp" / "audit-admission" / "ledger.sqlite3"
            ).resolve(),
            expected_owner_id=os.getuid(),
        )
    )
    audit_admission_ledger.recover_all()
    ctx = ToolContext(
        config=AutomationConfig(features={"fleet": True}),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=False),
        plugin_authority=plugin_authority,
        runner=None,
        launch_resolver=DefaultLaunchResolver(),
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=(FakeManagedHeadlessSessionLineageStore()),
        context_admission_ledger=DefaultContextAdmissionLedger(
            ContextAdmissionStoreAuthority(
                database_path=(
                    tmp_path / ".autoskillit" / "temp" / "context-admission" / "ledger.sqlite3"
                ).resolve(),
                expected_owner_id=os.getuid(),
            )
        ),
        audit_admission_ledger=audit_admission_ledger,
        audit_authority_materializer=cast(AuditAuthorityMaterializer, object()),
        committed_disposition_resolver=cast(CommittedDispositionResolver, object()),
    )
    try:
        yield ctx
    finally:
        plugin_authority.close()


@pytest.fixture
def make_tool_ctx(monkeypatch, tmp_path):
    """Factory fixture building a fully isolated ToolContext for server tests.

    Full-stack factory: each call runs make_context() from server/_factory.py,
    which imports ALL production layers (L0–L3). Use minimal_ctx instead when
    the test only needs gate, audit, token_log, timing_log, or config fields.

    Monkeypatches server._ctx so all server tool calls use the most recently
    built context. Gate starts closed (matching production) — use
    tool_ctx_kitchen_open when a test needs the gate open.

    All service fields (executor, tester, db_reader, workspace_mgr, recipes,
    migrations) are wired via make_context() so routing tests work correctly.

    Pass config to build a context from a specific AutomationConfig (e.g. to
    pin agent_backend.recipe_overrides before context creation); omit it for
    the default AutomationConfig(features={"fleet": True}).
    """
    from autoskillit.config import AutomationConfig
    from autoskillit.server import _state
    from autoskillit.server._factory import make_context
    from tests.fakes import FakePluginArtifactAuthority, MockSubprocessRunner

    created_authorities: list[FakePluginArtifactAuthority] = []

    def _factory(config: AutomationConfig | None = None):
        mock_runner = MockSubprocessRunner()
        plugin_authority = FakePluginArtifactAuthority(tmp_path)
        created_authorities.append(plugin_authority)
        ctx = make_context(
            config if config is not None else AutomationConfig(features={"fleet": True}),
            runner=mock_runner,
            plugin_authority=plugin_authority,
            project_dir=tmp_path,
        )
        ctx.audit_admission_ledger.recover_all()
        ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")
        ctx.config.linux_tracing.tmpfs_path = str(tmp_path / "shm")
        # Independent of the basis already baked into ctx.session_skill_manager
        # at make_context() time — tests asserting manager roots must derive
        # expectations from resolve_temp_dir(project_dir, config.workspace.temp_dir),
        # not ctx.temp_dir.
        ctx.temp_dir = tmp_path / ".autoskillit" / "temp"
        test_skills_root = tmp_path / ".claude" / "skills"
        for skill_name in (
            "do-a",
            "do-b",
            "eval-agent",
            "idle-scope",
            "implement",
            "probe",
            "some-skill",
            "target-skill",
            "test",
            "test-skill",
        ):
            skill_dir = test_skills_root / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: Test fixture skill\n---\n"
                "# Test fixture skill\n"
            )
        monkeypatch.setattr(_state, "_ctx", ctx)
        monkeypatch.setattr(_state, "_startup_ready", None)
        return ctx

    try:
        yield _factory
    finally:
        for plugin_authority in created_authorities:
            plugin_authority.close()


@pytest.fixture
def tool_ctx(make_tool_ctx):
    """Provide a fully isolated ToolContext for server integration tests.

    Thin wrapper around make_tool_ctx() using the default AutomationConfig.
    See make_tool_ctx for the full contract.
    """
    return make_tool_ctx()


@pytest.fixture
def tool_ctx_kitchen_open(tool_ctx):
    """tool_ctx variant with gate explicitly opened.

    Use when the test requires a tool that calls _require_enabled() and
    the test is not testing gate-boot behavior itself. This fixture
    mirrors the post-lifespan-boot state for interactive sessions.

    The production resolver remains installed so every run_skill call exercises
    the same fail-closed effective-contract path as a real session.  The parent
    fixture provides small project-local skills for generic routing tests.
    """
    from autoskillit.pipeline.gate import DefaultGateState

    tool_ctx.gate = DefaultGateState(enabled=True)
    tool_ctx.kitchen_id = "test-kitchen"
    return tool_ctx


def bind_test_skill_resume_contract(
    tool_ctx,
    *,
    session_id: str,
    cwd,
    skill_name: str = "implement",
    resolved_command: str | None = None,
    read_only: bool = False,
) -> None:
    """Bind a minimal valid projected contract for resume-path tests."""
    import hashlib
    from pathlib import Path

    from autoskillit.core import (
        BackendAuthority,
        BackendAuthorityKind,
        BackendAuthorityTier,
        CmdSpec,
        LaunchResolutionRequest,
        LaunchSurface,
        LaunchValueSource,
        LaunchValueSourceKind,
        SemanticLaunchPlan,
        SkillExecutionRole,
        SkillSource,
        SkillSourceRef,
    )
    from autoskillit.execution import (
        DefaultSkillSessionContractStore,
        SkillSessionContract,
    )
    from autoskillit.execution.headless._headless_launch import _HeadlessLaunchAdapter

    text = f"---\nname: {skill_name}\n---\n# Test resume snapshot\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    resolved_cwd = str(Path(cwd).resolve())
    launch_source = LaunchValueSource(
        LaunchValueSourceKind.DEFAULT,
        "tests.resume.default",
    )
    launch_preparation = tool_ctx.launch_resolver.prepare(
        LaunchResolutionRequest(
            surface=LaunchSurface.HEADLESS_SKILL,
            authority_candidates=(
                BackendAuthority(
                    backend=tool_ctx.backend.name,
                    kind=BackendAuthorityKind.GLOBAL,
                    tier=BackendAuthorityTier.GLOBAL,
                    key_path="agent_backend.backend",
                ),
            ),
            semantic_plan=SemanticLaunchPlan(
                surface=LaunchSurface.HEADLESS_SKILL,
                semantic_digest="test-resume-semantic",
                projection_digest="test-resume-projection",
            ),
            command=resolved_command or f"/{skill_name}",
            arguments=(),
            cwd=resolved_cwd,
            requested_model=None,
            requested_model_source=launch_source,
            configured_model=None,
            configured_model_source=launch_source,
            effort=None,
            effort_source=launch_source,
            sandbox_mode="test",
            network_access=False,
            pty_required=False,
            inherited_fd_policy="test",
            branch_identity={},
            worktree_identity={"cwd": resolved_cwd},
            executable_identity={"backend": tool_ctx.backend.name},
            plugin_identity={},
            projection_identity={"digest": "test-resume-projection"},
            artifact_paths=(),
            quota_identity={},
            non_authority_metadata={"fixture": True},
        )
    )
    launch_contract = tool_ctx.launch_resolver.finalize(
        launch_preparation,
        _HeadlessLaunchAdapter(
            build_spec=lambda _binding, _extras: CmdSpec(
                cmd=(tool_ctx.backend.name, "--test-resume"),
                cwd=resolved_cwd,
                env={},
            ),
            binding=None,
            provider_extras=None,
            observer=None,
            managed_attempt_id=None,
        ),
    )
    relative_path = (
        Path(tool_ctx.backend.conventions.skills_subdir) / skill_name / "SKILL.md"
    ).as_posix()
    contract = SkillSessionContract(
        root_name=skill_name,
        execution_role=SkillExecutionRole.SESSION,
        source_refs={
            skill_name: SkillSourceRef(
                origin=SkillSource.PROJECT_LOCAL,
                logical_name=skill_name,
                skill_path=Path(tool_ctx.project_dir) / relative_path,
                search_dir=".claude/skills",
                precedence=0,
            )
        },
        closure=(skill_name,),
        capability_union=frozenset(),
        canonical_digests={skill_name: digest},
        projected_digests={skill_name: digest},
        projection_version=SKILL_PROJECTION_VERSION,
        project_root=str(Path(tool_ctx.project_dir).resolve()),
        cwd=resolved_cwd,
        backend=tool_ctx.backend.name,
        resolved_command=resolved_command or f"/{skill_name}",
        member_roles={skill_name: SkillExecutionRole.SESSION},
        member_capabilities={skill_name: frozenset()},
        member_activate_deps={skill_name: ()},
        canonical_contents={skill_name: text},
        launch_contract=launch_contract,
        read_only=read_only,
        parent_sandbox_mode="read-only" if read_only else "workspace-write",
    )
    store = DefaultSkillSessionContractStore(root=Path(tool_ctx.temp_dir) / "test-skill-contracts")
    correlation_key = store.create_provisional(
        contract=contract,
        snapshot={relative_path: text},
    )
    store.finalize(correlation_key, session_id)
    tool_ctx.skill_session_contract_store = store


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
    parser.addoption(
        "--update-fixtures",
        action="store_true",
        default=False,
        help="Regenerate deterministic conformance fixtures in-place instead of asserting.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Compute test filter scope from env var + git diff + manifest.

    Opt-in via AUTOSKILLIT_TEST_FILTER env var or --filter-mode CLI flag.
    Fail-open: any error sets scope to None (full test run).
    """
    import warnings

    # Reset xdist IPC accumulator so in-process pytester reruns don't leak counts.
    _worker_filter_counts.clear()
    _worker_feature_scope.clear()

    config.stash[_scope_key] = None
    config.stash[_filter_mode_key] = None
    config.stash[_full_run_reason_key] = None
    config.stash[_feature_scope_key] = None

    cli_mode = config.getoption("--filter-mode", default=None)
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")

    if not cli_mode and not env_val:
        return
    if not cli_mode and env_val.lower() in ("0", "false", "no"):
        return

    try:
        from tests._test_filter import (
            FilterMode,
            FullRunReason,
            build_test_scope,
            git_changed_files,
            git_changed_files_local,
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
        if mode == FilterMode.AGGRESSIVE:
            changed = git_changed_files_local(config.rootpath)
        else:
            changed = git_changed_files(config.rootpath, base_ref=cli_base_ref)

        # Resolve the actual base_ref used (env fallback mirrors git_changed_files logic)
        resolved_base_ref = cli_base_ref or os.environ.get(
            "AUTOSKILLIT_TEST_BASE_REF",
            os.environ.get("GITHUB_BASE_REF"),
        )

        manifest = load_manifest(config.rootpath)
        coverage_map_path = config.rootpath / ".autoskillit" / "test-source-map.json"

        scope = build_test_scope(
            changed_files=changed,
            mode=mode,
            manifest=manifest,
            tests_root=config.rootpath / "tests",
            coverage_map_path=coverage_map_path,
            cwd=config.rootpath,
            base_ref=resolved_base_ref,
        )

        if isinstance(scope, FullRunReason):
            config.stash[_scope_key] = None
            config.stash[_full_run_reason_key] = scope.value
        else:
            config.stash[_scope_key] = scope
        config.stash[_filter_mode_key] = mode.value

    except Exception as exc:
        warnings.warn(
            f"Test filter setup failed, running all tests: {exc}",
            stacklevel=1,
        )


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _resolve_test_config() -> "AutomationConfig":
    """Resolve full config for test collection via full config resolution.

    Uses the same dynaconf chain as production: defaults.yaml → project config → env vars.

    Fail-closed: any exception raised by ``load_config`` (or a type mismatch in
    the returned value) propagates to the caller. Since this function runs at
    pytest collection time inside ``pytest_collection_modifyitems``, an unhandled
    exception aborts collection with a clear error rather than silently
    downgrading the test scope to per-feature ``default_enabled`` (which is
    ``False`` for every currently-registered feature — see
    ``FEATURE_REGISTRY`` in
    ``src/autoskillit/core/types/_type_constants_features.py:42-100``).

    ``lru_cache`` only caches successful returns; a transient failure will
    retry on the next call.
    """
    from pathlib import Path

    from autoskillit.config import load_config
    from autoskillit.config.settings import AutomationConfig as _AutomationConfig

    # Anchor to repo root via this file's known location (tests/conftest.py)
    # rather than Path.cwd(), which varies across IDE runners and monkeypatch.chdir.
    repo_root = Path(__file__).resolve().parent.parent
    cfg = load_config(repo_root)
    if not isinstance(cfg, _AutomationConfig):
        raise TypeError(f"load_config returned {type(cfg)!r}, expected AutomationConfig")
    return cfg


def _is_test_feature_enabled(feature_name: str, *, env_val: str | None) -> bool:
    """Return True if feature_name is enabled for this test run.

    Resolution order:
    1. If AUTOSKILLIT_TEST_FEATURES is set (including empty string), parse it
       as a comma-separated whitelist.  Only listed names are enabled.
    2. If unset, resolve via full config chain (defaults.yaml → project config
       → env vars) using load_config().  This respects experimental_enabled and
       per-feature config overrides.
       Unknown feature names return True (fail-open).

    Args:
        feature_name: The feature name to check.
        env_val: Pre-read value of AUTOSKILLIT_TEST_FEATURES (pass ``None`` when unset).
    """
    if env_val is not None:
        enabled = {f.strip() for f in env_val.split(",") if f.strip()}
        return feature_name in enabled

    from autoskillit.core import FEATURE_REGISTRY
    from autoskillit.core.feature_flags import is_feature_enabled

    defn = FEATURE_REGISTRY.get(feature_name)
    if defn is None:
        import warnings

        warnings.warn(
            f"pytest.mark.feature({feature_name!r}) references an unknown feature; "
            "fail-open assumed (test will run). Check for typos in the marker.",
            stacklevel=4,
        )
        return True

    cfg = _resolve_test_config()
    return is_feature_enabled(
        feature_name, cfg.features, experimental_enabled=cfg.experimental_enabled
    )


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
    # Stash the full feature scope for all registered features (not just those
    # encountered on items) so pytest_terminal_summary can emit a single line
    # describing the test run's effective feature scope. Under xdist
    # (-n 4 --dist worksteal) this is populated on each worker, then
    # transferred to the controller via workeroutput in pytest_sessionfinish.
    from autoskillit.core import FEATURE_REGISTRY

    _feature_scope: dict[str, bool] = {
        name: _is_test_feature_enabled(name, env_val=_test_features_env)
        for name in FEATURE_REGISTRY
    }
    config.stash[_feature_scope_key] = _feature_scope
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
                # Step 2E: split skip reason by gating mechanism. Whitelist
                # mode (AUTOSKILLIT_TEST_FEATURES set) names the env var;
                # config-resolution mode uses a dedicated message so users
                # can tell at a glance which path actually disabled the test.
                if _test_features_env is not None:
                    env_display = _test_features_env or ""
                    reason = (
                        f"feature '{feature_name}' disabled"
                        f" (AUTOSKILLIT_TEST_FEATURES='{env_display}'"
                        f" does not include '{feature_name}')"
                    )
                else:
                    reason = f"feature '{feature_name}' disabled via config resolution"
                item.add_marker(pytest.mark.skip(reason=reason))

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
        from tests._test_filter import ALWAYS_RUN_AGGRESSIVE

        tests_root = config.rootpath / "tests"
        _SIZE_MARKERS = {"small", "medium", "large"}
        size_selected: list[pytest.Item] = []
        size_deselected: list[pytest.Item] = []

        for item in items:
            try:
                first_part = item.path.relative_to(tests_root).parts[0]
            except (ValueError, IndexError):
                first_part = ""
            if first_part in ALWAYS_RUN_AGGRESSIVE:
                size_selected.append(item)
                continue
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
        # xdist worker: propagate counts to controller via workeroutput IPC channel.
        # config.stash is process-local; the controller never sees stash writes from
        # workers, so we must transfer the counts explicitly here.
        session.config.workeroutput["filter_selected"] = session.config.stash.get(
            _selected_count_key, None
        )
        session.config.workeroutput["filter_deselected"] = session.config.stash.get(
            _deselected_count_key, None
        )
        # Step 2D: feature-scope propagation. Must happen BEFORE the early
        # return — the controller only receives data via workeroutput, and
        # any write after `return` is dead code on workers.
        session.config.workeroutput["feature_scope"] = session.config.stash.get(
            _feature_scope_key, None
        )
        return
    out_path = os.environ.get("AUTOSKILLIT_FILTER_STATS_FILE")
    if not out_path:
        return
    filter_mode = session.config.stash.get(_filter_mode_key, None)
    selected = session.config.stash.get(_selected_count_key, None)
    deselected = session.config.stash.get(_deselected_count_key, None)
    # Under xdist the controller never runs pytest_collection_modifyitems, so the
    # stash keys are None there. Fall back to counts aggregated by pytest_testnodedown.
    if selected is None and _worker_filter_counts:
        selected = _worker_filter_counts.get("selected")
    if deselected is None and _worker_filter_counts:
        deselected = _worker_filter_counts.get("deselected")
    if filter_mode is None:
        return
    import json

    full_run_reason = session.config.stash.get(_full_run_reason_key, None)
    _Path(out_path).write_text(
        json.dumps(
            {
                "filter_mode": filter_mode,
                "tests_selected": selected,
                "tests_deselected": deselected,
                "full_run_reason": full_run_reason,
            }
        )
    )


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """Aggregate filter counts from the first xdist worker that reports.

    Called on the controller process by xdist after each worker finishes.
    We capture the first worker that reports both counts as non-None; all workers
    see the same test set under ``--dist load`` (collection and filtering happen
    per-worker before distribution), so any single worker's counts are
    representative of the full session.  Note: this assumption only holds under
    ``--dist load``; under ``--dist loadscope`` or ``--dist loadfile`` different
    workers process different subsets and counts may diverge.
    """
    if _worker_filter_counts:
        return  # already captured from the first reporting worker
    wo = getattr(node, "workeroutput", {})
    selected = wo.get("filter_selected")
    deselected = wo.get("filter_deselected")
    if selected is not None and deselected is not None:
        _worker_filter_counts["selected"] = selected
        _worker_filter_counts["deselected"] = deselected
    # Step 2D: feature-scope aggregation from the first reporting worker.
    # All workers run pytest_collection_modifyitems over the full pre-distribution
    # item list (identical resolution across workers under --dist worksteal), so
    # any single worker's scope is representative. "First worker wins" avoids
    # pathological cases where a worker's scope is unexpectedly empty.
    if not _worker_feature_scope:
        scope = wo.get("feature_scope")
        if scope:
            _worker_feature_scope.update(scope)


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:
    """Emit a single ``Feature scope:`` line so the test run's effective feature
    gate state is visible regardless of ``--disable-warnings``.

    Fallback order: stash first (in-process / non-xdist runs where
    ``pytest_collection_modifyitems`` populated it), then the
    ``_worker_feature_scope`` accumulator (xdist runs where data was
    transferred via ``workeroutput`` -> ``pytest_testnodedown``). If both are
    empty, no summary is emitted.
    """
    scope = config.stash.get(_feature_scope_key, None)
    if scope is None:
        scope = _worker_feature_scope or None
    if not scope:
        return
    parts = " ".join(
        f"{name}=enabled" if enabled else f"{name}=disabled"
        for name, enabled in sorted(scope.items())
    )
    terminalreporter.write_line(f"Feature scope: {parts}")
