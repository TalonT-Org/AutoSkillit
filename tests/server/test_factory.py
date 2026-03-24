"""Tests for server/_factory.py make_context() composition root."""

from __future__ import annotations

from pathlib import Path

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
from autoskillit.server._factory import _gh_cli_token, make_context
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


def test_make_context_gate_stays_closed_in_headless_session(monkeypatch):
    """Gate is NOT pre-enabled when AUTOSKILLIT_HEADLESS=1.
    Tag-based visibility (mcp.enable({'headless'})) handles tool reveal."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.gate.enabled is False


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
    assert ctx.skill_resolver is not None
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
    monkeypatch.setattr("autoskillit.server._factory._gh_cli_token", lambda: None)
    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".")
    assert ctx.github_client.has_token is False


def test_make_context_github_client_uses_gh_cli_fallback(monkeypatch):
    """When no config token or GITHUB_TOKEN env var, fall back to gh auth token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("autoskillit.server._factory._gh_cli_token", lambda: "gh-cli-token")
    config = AutomationConfig()
    ctx = make_context(config, runner=None, plugin_dir=".")
    assert ctx.github_client.has_token is True


def test_make_context_github_client_config_token_takes_priority_over_gh_cli(monkeypatch):
    """Config token takes priority over gh CLI token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("autoskillit.server._factory._gh_cli_token", lambda: "gh-cli-token")
    config = AutomationConfig()
    config.github.token = "config-token"
    ctx = make_context(config, runner=None, plugin_dir=".")
    assert ctx.github_client.has_token is True
    assert ctx.github_client._token == "config-token"


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


# ---------------------------------------------------------------------------
# Write-expected resolver integration tests
# ---------------------------------------------------------------------------


def test_write_expected_resolver_wired_on_context() -> None:
    """make_context() must wire a write_expected_resolver onto ToolContext."""
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.write_expected_resolver is not None
    spec = ctx.write_expected_resolver("/autoskillit:make-plan some task")
    assert spec.mode == "always"


def test_write_expected_resolver_unknown_skill() -> None:
    """Unknown skills produce a WriteBehaviorSpec with mode=None."""
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.write_expected_resolver is not None
    spec = ctx.write_expected_resolver("/autoskillit:nonexistent-skill foo")
    assert spec.mode is None


def test_write_expected_resolver_conditional_skill() -> None:
    """resolve-merge-conflicts produces mode='conditional' with patterns."""
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.write_expected_resolver is not None
    spec = ctx.write_expected_resolver("/autoskillit:resolve-merge-conflicts")
    assert spec.mode == "conditional"
    assert len(spec.expected_when) > 0


def test_cook_and_factory_session_skill_manager_ctor_args_in_sync() -> None:
    """Sync test: _cook.py and _factory.py must call DefaultSessionSkillManager
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
    cook_path = root / "cli" / "_cook.py"
    factory_path = root / "server" / "_factory.py"

    cook_count = _count_ctor_positional_args(cook_path)
    factory_count = _count_ctor_positional_args(factory_path)

    assert cook_count != -1, "No DefaultSessionSkillManager call found in _cook.py"
    assert factory_count != -1, "No DefaultSessionSkillManager call found in _factory.py"
    assert cook_count == factory_count, (
        f"DefaultSessionSkillManager constructor arg count mismatch:\n"
        f"  _cook.py:    {cook_count} positional arg(s)\n"
        f"  _factory.py: {factory_count} positional arg(s)\n"
        "Align both call sites or update this test if the API intentionally diverged."
    )


# ---------------------------------------------------------------------------
# _gh_cli_token unit tests
# ---------------------------------------------------------------------------


def test_gh_cli_token_returns_token_on_success(monkeypatch):
    """_gh_cli_token returns stdout when gh auth token succeeds."""
    import subprocess as _subprocess

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 0, stdout="gho_abc123\n", stderr="")

    monkeypatch.setattr("autoskillit.server._factory.subprocess.run", fake_run)
    assert _gh_cli_token() == "gho_abc123"


def test_gh_cli_token_returns_none_on_failure(monkeypatch):
    """_gh_cli_token returns None when gh auth token fails."""
    import subprocess as _subprocess

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")

    monkeypatch.setattr("autoskillit.server._factory.subprocess.run", fake_run)
    assert _gh_cli_token() is None


def test_gh_cli_token_returns_none_when_gh_not_installed(monkeypatch):
    """_gh_cli_token returns None when gh is not on PATH."""

    def fake_run(cmd, *, capture_output, text, timeout):
        raise FileNotFoundError("gh")

    monkeypatch.setattr("autoskillit.server._factory.subprocess.run", fake_run)
    assert _gh_cli_token() is None
