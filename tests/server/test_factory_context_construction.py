"""make_context() composition root — context construction and service defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import AuditAdmissionStoreAuthority
from autoskillit.core.types import SkillResult
from autoskillit.execution.db import DefaultDatabaseReader
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.session import DefaultManagedHeadlessSessionLineageStore
from autoskillit.execution.testing import DefaultTestRunner
from autoskillit.migration.service import DefaultMigrationService
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from autoskillit.recipe.repository import DefaultRecipeRepository
from autoskillit.server._factory import make_context
from autoskillit.workspace import DefaultCloneManager, SkillResolver
from autoskillit.workspace.cleanup import DefaultWorkspaceManager
from tests.server._factory_test_helpers import (
    _install_shared_explorer_authority,
    _runner,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_make_context_survives_stale_precontract_shadowing_skill(tmp_path):
    """T6: make_context() (the real factory composition path, no resolver
    mocking) does not crash when a project-local skill shadows a bundled
    tier skill with a stale, pre-contract-era copy — it falls through and
    logs the exclusion instead of raising (#4470)."""
    import structlog.testing

    stale_dir = tmp_path / ".claude" / "skills" / "audit-bugs"
    stale_dir.mkdir(parents=True)
    stale_dir_path = stale_dir / "SKILL.md"
    stale_dir_path.write_text(
        "---\n"
        "name: audit-bugs\n"
        "description: Stale pre-contract-era copy.\n"
        "---\n"
        "# audit-bugs\n\n"
        'LOG_DIR="$HOME/.claude/projects/${PWD//\\//-}"\n',
        encoding="utf-8",
    )

    with structlog.testing.capture_logs() as logs:
        ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)

    assert isinstance(ctx, ToolContext)
    assert any(entry.get("event") == "skill_catalog_exclusions" for entry in logs)


def test_factory_bootstraps_exploration_store_from_verified_launch_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root, execution_cwd, _authority_path = _install_shared_explorer_authority(
        monkeypatch, tmp_path
    )
    monkeypatch.chdir(execution_cwd)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_dir=".",
        project_dir=execution_cwd,
    )

    assert ctx.project_dir == execution_cwd
    assert ctx.exploration_context_store is not None
    assert ctx.exploration_context_store.trusted_root == repository_root


@pytest.mark.parametrize("invalid_authority", ("partial", "tampered", "wrong-cwd"))
def test_factory_does_not_redirect_for_invalid_launch_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_authority: str,
) -> None:
    repository_root, execution_cwd, authority_path = _install_shared_explorer_authority(
        monkeypatch, tmp_path
    )
    if invalid_authority == "partial":
        monkeypatch.delenv("AUTOSKILLIT_EXPLORATION_CAPABILITY")
    elif invalid_authority == "tampered":
        payload = json.loads(authority_path.read_text(encoding="utf-8"))
        payload["principal"]["repository_root"] = str(tmp_path / "unsigned-redirect")
        authority_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        wrong_cwd = tmp_path / "wrong-cwd"
        wrong_cwd.mkdir()
        execution_cwd = wrong_cwd.resolve()
    monkeypatch.chdir(execution_cwd)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_dir=".",
        project_dir=execution_cwd,
    )

    assert ctx.exploration_context_store is not None
    assert ctx.exploration_context_store.trusted_root == execution_cwd
    assert ctx.exploration_context_store.trusted_root != repository_root


def test_factory_normal_session_keeps_explicit_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for key in (
        "AUTOSKILLIT_EXPLORATION_CAPABILITY",
        "AUTOSKILLIT_EXPLORATION_ROLE",
        "AUTOSKILLIT_EXPLORATION_SESSION_ID",
        "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
    ):
        monkeypatch.delenv(key, raising=False)

    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".", project_dir=tmp_path)

    assert ctx.exploration_context_store is not None
    assert ctx.exploration_context_store.trusted_root == tmp_path.resolve()


def test_make_context_returns_toolcontext(tmp_path):
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert isinstance(ctx, ToolContext)
    assert ctx.gate is not None
    assert ctx.runner is not None
    assert isinstance(ctx.context_admission_ledger, DefaultContextAdmissionLedger)
    assert isinstance(
        ctx.managed_headless_session_lineage_store,
        DefaultManagedHeadlessSessionLineageStore,
    )
    assert (
        ctx.context_admission_ledger.database_path
        == (tmp_path / ".autoskillit" / "temp" / "context-admission" / "ledger.sqlite3").resolve()
    )
    assert not ctx.context_admission_ledger.database_path.exists()
    assert (
        ctx.audit_admission_ledger.store_authority.database_path
        == (tmp_path / ".autoskillit" / "temp" / "audit-admission" / "ledger.sqlite3").resolve()
    )


def test_make_context_uses_explicit_parent_audit_admission_authority(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    parent_authority = AuditAdmissionStoreAuthority(
        database_path=(tmp_path / "parent" / "audit-admission.sqlite3").resolve(),
        expected_owner_id=os.getuid(),
    )

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_dir=".",
        project_dir=clone,
        audit_admission_store_authority=parent_authority,
    )

    assert ctx.audit_admission_ledger.store_authority is parent_authority


def test_make_context_default_audit_authority_is_clone_local(tmp_path: Path) -> None:
    clone_a = tmp_path / "clone-a"
    clone_b = tmp_path / "clone-b"
    clone_a.mkdir()
    clone_b.mkdir()

    ctx_a = make_context(AutomationConfig(), runner=None, plugin_dir=".", project_dir=clone_a)
    ctx_b = make_context(AutomationConfig(), runner=None, plugin_dir=".", project_dir=clone_b)

    authority_a = ctx_a.audit_admission_ledger.store_authority
    authority_b = ctx_b.audit_admission_ledger.store_authority
    assert authority_a.database_path.is_relative_to(clone_a.resolve())
    assert authority_b.database_path.is_relative_to(clone_b.resolve())
    assert authority_a.authority_id != authority_b.authority_id


def test_make_context_gate_starts_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert ctx.gate.enabled is False


def test_make_context_gate_stays_closed_in_headless_session(monkeypatch, tmp_path):
    """Gate is NOT pre-enabled when AUTOSKILLIT_HEADLESS=1.
    Tag-based visibility (mcp.enable({'headless'})) handles tool reveal."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert ctx.gate.enabled is False


def test_make_context_executor_is_default_headless(tmp_path):
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert isinstance(ctx.executor, DefaultHeadlessExecutor)


def test_make_context_tester_is_default_test_runner(tmp_path):
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert isinstance(ctx.tester, DefaultTestRunner)


def test_make_context_service_fields_are_typed_instances(tmp_path):
    """Core service fields are typed instances (skill_resolver, clone_mgr, repositories)."""
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert isinstance(ctx.skill_resolver, SkillResolver)
    assert isinstance(ctx.clone_mgr, DefaultCloneManager)
    assert isinstance(ctx.recipes, DefaultRecipeRepository)
    assert isinstance(ctx.migrations, DefaultMigrationService)
    assert isinstance(ctx.db_reader, DefaultDatabaseReader)
    assert isinstance(ctx.workspace_mgr, DefaultWorkspaceManager)
    assert isinstance(ctx.context_admission_ledger, DefaultContextAdmissionLedger)
    assert ctx.github_review_poster is not None
    assert type(ctx.github_review_poster).__name__ == "DefaultGitHubReviewPoster"


def test_make_context_creates_isolated_context_admission_ledgers(tmp_path) -> None:
    first = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    second = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)

    assert first.context_admission_ledger is not second.context_admission_ledger
    assert (
        first.context_admission_ledger.database_path
        == second.context_admission_ledger.database_path
    )


def test_make_context_tester_none_when_no_runner(tmp_path):
    """When runner=None, DefaultTestRunner cannot be constructed; tester is None."""
    ctx = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)
    assert ctx.tester is None


def test_make_context_protocol_substitution(tmp_path):
    """Any object satisfying HeadlessExecutor protocol can replace ctx.executor."""
    from autoskillit.core.types import HeadlessExecutor

    class FakeExecutor:
        async def run(
            self,
            skill_command: str,
            cwd: str,
            *,
            model: str = "",
            step_name: str = "",
            add_dirs=(),
        ) -> SkillResult:
            return SkillResult(
                success=True,
                result="",
                session_id="",
                subtype="",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason="none",
                stderr="",
                token_usage=None,
            )

        async def dispatch_food_truck(
            self,
            orchestrator_prompt: str,
            cwd: str,
            *,
            completion_marker: str = "",
            model: str = "",
            step_name: str = "",
            on_spawn=None,
            **kwargs,
        ) -> SkillResult:
            return SkillResult(
                success=True,
                result="",
                session_id="",
                subtype="",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason="none",
                stderr="",
                token_usage=None,
            )

    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    ctx.executor = FakeExecutor()
    assert isinstance(ctx.executor, HeadlessExecutor)


def test_cook_and_factory_session_skill_manager_ctor_args_in_sync() -> None:
    """Sync test: _session_cook.py and _factory.py must call DefaultSessionSkillManager
    with the same number of positional arguments.

    Both are separate entry points (REQ-TIER-011) and must not be merged, but they
    must stay structurally aligned. This AST-based test catches constructor drift
    without requiring the paths to be unified.
    """
    import ast

    from autoskillit.core import pkg_root

    def _count_ctor_positional_args(src_path: Path) -> int:
        """Return the positional arg count of the first DefaultSessionSkillManager(...) call."""
        tree = ast.parse(src_path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "DefaultSessionSkillManager"
            ):
                return len(node.args)
        return -1

    root = pkg_root()
    cook_path = root / "cli" / "session" / "_session_cook.py"
    factory_path = root / "server" / "_factory.py"

    cook_count = _count_ctor_positional_args(cook_path)
    factory_count = _count_ctor_positional_args(factory_path)

    assert cook_count != -1, "No DefaultSessionSkillManager call found in _session_cook.py"
    assert factory_count != -1, "No DefaultSessionSkillManager call found in _factory.py"
    assert cook_count == factory_count, (
        f"DefaultSessionSkillManager constructor arg count mismatch:\n"
        f"  _session_cook.py:    {cook_count} positional arg(s)\n"
        f"  _factory.py: {factory_count} positional arg(s)\n"
        "Align both call sites or update this test if the API intentionally diverged."
    )


def test_make_context_plugin_authority_derives_from_pkg_root_not_the_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default authority lazily projects the running package.

    Even with installed_plugins.json naming a plugin cache directory, no part of
    resolution reads it: a registry-named path can be stale, relocated, or
    already garbage-collected. The projection must derive from pkg_root() and
    must never be the canonical root itself.
    """
    from autoskillit.core import PluginArtifactAuthority, PluginLoadMode, pkg_root
    from autoskillit.execution.backends.claude import ClaudeCodeBackend

    fake_cache = tmp_path / "cache" / "autoskillit-local" / "autoskillit" / "1.0.0"
    fake_cache.mkdir(parents=True)

    ctx = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)
    assert isinstance(ctx.plugin_authority, PluginArtifactAuthority)
    with ctx.plugin_authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as binding:
        assert binding.plugin_dir is not None
        assert binding.plugin_dir != fake_cache
        assert binding.plugin_dir != pkg_root()
        assert (binding.plugin_dir / "skills").is_dir()


def test_make_context_direct_install_yields_lazy_sanitized_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Direct installs are projected only when a launch binding is acquired."""
    from autoskillit.core import PluginLoadMode
    from autoskillit.execution.backends.claude import ClaudeCodeBackend

    ctx = make_context(
        AutomationConfig(), runner=None, plugin_dir=str(tmp_path), project_dir=tmp_path
    )
    with ctx.plugin_authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as binding:
        assert binding.plugin_dir is not None
        assert binding.plugin_dir != tmp_path
        assert (binding.plugin_dir / "skills").is_dir()


def test_make_context_sets_token_factory(tmp_path):
    """make_context() sets token_factory on the returned ToolContext."""
    cfg = AutomationConfig()
    ctx = make_context(cfg, runner=None, plugin_dir=str(tmp_path), project_dir=tmp_path)
    assert callable(ctx.token_factory)


def test_make_context_ignores_ambient_provider_profile(monkeypatch, tmp_path):
    """Ambient AUTOSKILLIT_PROVIDER_PROFILE must not mutate default_provider."""
    monkeypatch.setenv("AUTOSKILLIT_PROVIDER_PROFILE", "minimax")
    config = AutomationConfig()
    config.providers.profiles = {"minimax": {}}
    config.providers.default_provider = None
    ctx = make_context(config, runner=_runner(), project_dir=tmp_path)
    assert ctx.config.providers.default_provider is None


def test_make_context_no_env_profile_preserves_config_default(monkeypatch, tmp_path):
    """Without AUTOSKILLIT_PROVIDER_PROFILE in env, default_provider is unchanged."""
    monkeypatch.delenv("AUTOSKILLIT_PROVIDER_PROFILE", raising=False)
    config = AutomationConfig()
    config.providers.default_provider = "openai"
    config.providers.profiles = {}
    ctx = make_context(config, runner=_runner(), project_dir=tmp_path)
    assert ctx.config.providers.default_provider == "openai"
