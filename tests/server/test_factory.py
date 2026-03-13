"""Tests for server/_factory.py make_context() composition root."""

from __future__ import annotations

from autoskillit.config import AutomationConfig
from autoskillit.core.types import SkillResult, SubprocessResult, TerminationReason
from autoskillit.execution.db import DefaultDatabaseReader
from autoskillit.execution.github import DefaultGitHubFetcher
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.testing import DefaultTestRunner
from autoskillit.migration.engine import DefaultMigrationService
from autoskillit.pipeline.context import ToolContext
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.repository import DefaultRecipeRepository
from autoskillit.server._factory import make_context
from autoskillit.workspace.cleanup import DefaultWorkspaceManager
from tests.conftest import MockSubprocessRunner


def _runner() -> MockSubprocessRunner:
    r = MockSubprocessRunner()
    r.set_default(
        SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
    )
    return r


def test_make_context_returns_toolcontext():
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx, ToolContext)


def test_make_context_gate_starts_closed(monkeypatch):
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.gate.enabled is False


def test_make_context_gate_pre_enabled_in_headless_session(monkeypatch):
    """Gate starts enabled when AUTOSKILLIT_HEADLESS=1 (headless worker)."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.gate.enabled is True


def test_make_context_executor_is_default_headless():
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx.executor, DefaultHeadlessExecutor)


def test_make_context_tester_is_default_test_runner():
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx.tester, DefaultTestRunner)


def test_make_context_all_service_fields_populated_includes_github_client():
    """All optional service fields must be populated, including github_client and clone_mgr."""
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.executor is not None
    assert ctx.tester is not None
    assert ctx.recipes is not None
    assert ctx.migrations is not None
    assert ctx.db_reader is not None
    assert ctx.workspace_mgr is not None
    assert ctx.clone_mgr is not None
    assert ctx.github_client is not None
    assert isinstance(ctx.recipes, DefaultRecipeRepository)
    assert isinstance(ctx.migrations, DefaultMigrationService)
    assert isinstance(ctx.db_reader, DefaultDatabaseReader)
    assert isinstance(ctx.workspace_mgr, DefaultWorkspaceManager)


def test_make_context_github_client_is_default_fetcher():
    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".")
    assert isinstance(ctx.github_client, DefaultGitHubFetcher)


def test_make_context_github_client_uses_config_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config = AutomationConfig()
    config.github.token = "config-token"
    ctx = make_context(config, runner=None, plugin_dir=".")
    assert ctx.github_client.has_token is True


def test_make_context_github_client_uses_env_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    config = AutomationConfig()
    ctx = make_context(config, runner=None, plugin_dir=".")
    assert ctx.github_client.has_token is True


def test_make_context_github_client_no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".")
    assert ctx.github_client.has_token is False


def test_make_context_github_client_token_snapshot_is_immutable(monkeypatch):
    """Token is snapshotted at construction. Changing env after does not affect the fetcher."""
    monkeypatch.setenv("GITHUB_TOKEN", "startup-token")
    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".")
    assert ctx.github_client.has_token is True
    monkeypatch.delenv("GITHUB_TOKEN")
    assert ctx.github_client.has_token is True


def test_make_context_tester_none_when_no_runner():
    """When runner=None, DefaultTestRunner cannot be constructed; tester is None."""
    ctx = make_context(AutomationConfig(), runner=None)
    assert ctx.tester is None


def test_make_context_protocol_substitution():
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
            add_dir: str = "",
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

    ctx = make_context(AutomationConfig(), runner=_runner())
    ctx.executor = FakeExecutor()
    assert isinstance(ctx.executor, HeadlessExecutor)


# ---------------------------------------------------------------------------
# Output pattern integration tests
# ---------------------------------------------------------------------------


def test_output_patterns_nonempty_for_open_pr() -> None:
    """open-pr must have non-empty expected_output_patterns in the manifest."""
    name = resolve_skill_name("/autoskillit:open-pr")
    assert name is not None
    contract = get_skill_contract(name, load_bundled_manifest())
    assert contract is not None
    assert contract.expected_output_patterns, (
        "open-pr must have non-empty expected_output_patterns"
    )
    assert any("github" in p.lower() for p in contract.expected_output_patterns)


def test_output_patterns_nonempty_for_investigate() -> None:
    """investigate must have non-empty expected_output_patterns in the manifest."""
    name = resolve_skill_name("/autoskillit:investigate")
    assert name is not None
    contract = get_skill_contract(name, load_bundled_manifest())
    assert contract is not None
    assert contract.expected_output_patterns, (
        "investigate must have non-empty expected_output_patterns"
    )
