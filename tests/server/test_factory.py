"""Tests for server/_factory.py make_context() composition root."""

from __future__ import annotations

from pathlib import Path

import pytest

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
from autoskillit.server._factory import _gh_cli_token, _LazyTokenFactory, make_context
from autoskillit.workspace import DefaultCloneManager, SkillResolver
from autoskillit.workspace.cleanup import DefaultWorkspaceManager
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
    assert ctx.gate is not None
    assert ctx.runner is not None


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


def test_make_context_service_fields_are_typed_instances():
    """Core service fields are typed instances (skill_resolver, clone_mgr, repositories)."""
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx.skill_resolver, SkillResolver)
    assert isinstance(ctx.clone_mgr, DefaultCloneManager)
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
    # After lazy resolution via has_token, verify the resolved value
    assert ctx.github_client._resolve_token() == "config-token"


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


@pytest.mark.parametrize(
    "invocation,expected_mode,required_tokens",
    [
        (
            "/autoskillit:resolve-failures /tmp/wt .autoskillit/temp/plan.md main",
            "conditional",
            ["verdict"],
        ),
        (
            "/autoskillit:retry-worktree .autoskillit/temp/plan.md /tmp/wt",
            "conditional",
            ["phases_implemented"],
        ),
        (
            "/autoskillit:resolve-review feature-branch main",
            "conditional",
            ["verdict"],
        ),
        (
            "/autoskillit:audit-claims /tmp/wt main https://github.com/o/r/pull/1",
            None,
            [],
        ),
        (
            "/autoskillit:review-research-pr /tmp/wt main https://github.com/o/r/pull/1",
            None,
            [],
        ),
        (
            "/autoskillit:resolve-claims-review /tmp/wt main",
            "conditional",
            ["verdict"],
        ),
        (
            "/autoskillit:resolve-research-review /tmp/wt main",
            "conditional",
            ["verdict"],
        ),
        ("/autoskillit:make-plan some task", "always", []),
        ("/autoskillit:nonexistent-skill foo", None, []),
        ("/autoskillit:resolve-merge-conflicts", "conditional", ["conflict_report_path"]),
    ],
)
def test_write_expected_resolver_mode(
    invocation: str, expected_mode: str | None, required_tokens: list[str]
) -> None:
    """write_expected_resolver returns the correct mode and token patterns per skill."""
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.write_expected_resolver is not None
    spec = ctx.write_expected_resolver(invocation)
    assert spec.mode == expected_mode
    if expected_mode is None:
        assert spec.expected_when == ()
    else:
        for token in required_tokens:
            assert any(token in p for p in spec.expected_when)


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
    cook_path = root / "cli" / "session" / "_cook.py"
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


# ---------------------------------------------------------------------------
# TokenFactory unit tests
# ---------------------------------------------------------------------------


def test_token_factory_resolves_lazily():
    """TokenFactory must not resolve until first call, then cache."""
    call_count = 0

    def _resolver():
        nonlocal call_count
        call_count += 1
        return "ghp_test_token"

    factory = _LazyTokenFactory(_resolver)
    assert call_count == 0, "TokenFactory resolved eagerly at construction"
    assert not factory.is_resolved

    token = factory()
    assert token == "ghp_test_token"
    assert call_count == 1
    assert factory.is_resolved

    # Second call uses cache
    token2 = factory()
    assert token2 == "ghp_test_token"
    assert call_count == 1, "TokenFactory resolved twice instead of caching"


def test_token_factory_caches_none():
    """TokenFactory caches None results (gh CLI not available)."""
    call_count = 0

    def _resolver():
        nonlocal call_count
        call_count += 1
        return None

    factory = _LazyTokenFactory(_resolver)
    assert factory() is None
    assert call_count == 1
    assert factory() is None
    assert call_count == 1, "TokenFactory resolved twice for None result"


def test_gh_cli_token_not_called_during_make_context(monkeypatch):
    """make_context() must not call _gh_cli_token() — token resolves lazily."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    calls: list[object] = []
    original_run = __import__("subprocess").run

    def tracking_run(*args, **kwargs):
        calls.append(args)
        return original_run(*args, **kwargs)

    monkeypatch.setattr("autoskillit.server._factory.subprocess.run", tracking_run)

    config = AutomationConfig()
    make_context(config, runner=None, plugin_dir=".")

    gh_calls = [c for c in calls if "gh" in str(c)]
    assert gh_calls == [], f"_gh_cli_token() called during make_context: {gh_calls}"


def test_make_context_marketplace_install_yields_marketplace_plugin_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """make_context() with a marketplace-detected install produces MarketplaceInstall."""
    from autoskillit.core.types._type_plugin_source import MarketplaceInstall

    fake_cache = tmp_path / "cache" / "autoskillit-local" / "autoskillit" / "1.0.0"
    fake_cache.mkdir(parents=True)

    monkeypatch.setattr("autoskillit.server._factory._check_plugin_installed", lambda: True)
    monkeypatch.setattr(
        "autoskillit.server._factory._resolve_marketplace_cache_path",
        lambda: fake_cache,
    )

    ctx = make_context(AutomationConfig(), runner=None)
    assert isinstance(ctx.plugin_source, MarketplaceInstall)
    assert ctx.plugin_source.cache_path == fake_cache


def test_make_context_direct_install_yields_direct_plugin_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """make_context() with a direct install produces DirectInstall with pkg_root."""
    from autoskillit.core.types._type_plugin_source import DirectInstall

    monkeypatch.setattr("autoskillit.server._factory._check_plugin_installed", lambda: False)

    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=str(tmp_path))
    assert isinstance(ctx.plugin_source, DirectInstall)
    assert ctx.plugin_source.plugin_dir == tmp_path


def test_make_context_sets_token_factory(tmp_path):
    """make_context() sets token_factory on the returned ToolContext."""
    cfg = AutomationConfig()
    ctx = make_context(cfg, runner=None, plugin_dir=str(tmp_path))
    assert callable(ctx.token_factory)


# --- Group P-2: project_dir env inheritance ---


def test_make_context_reads_project_dir_env(tmp_path, monkeypatch):
    """make_context reads AUTOSKILLIT_PROJECT_DIR and stores it on ctx.project_dir."""
    monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", str(tmp_path))
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.project_dir == tmp_path


def test_make_context_project_dir_git_root_fallback(monkeypatch):
    """make_context resolves project_dir via git toplevel when env var is not set."""
    monkeypatch.delenv("AUTOSKILLIT_PROJECT_DIR", raising=False)
    ctx = make_context(AutomationConfig(), runner=_runner())
    import subprocess as _sp

    expected = Path(
        _sp.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    assert ctx.project_dir == expected


# --- _resolve_project_dir unit tests ---


def test_resolve_project_dir_env_var(tmp_path, monkeypatch):
    from autoskillit.server._factory import _resolve_project_dir

    monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", str(tmp_path))
    assert _resolve_project_dir() == tmp_path


def test_resolve_project_dir_git_root(monkeypatch):
    import subprocess as _subprocess

    from autoskillit.server._factory import _resolve_project_dir

    monkeypatch.delenv("AUTOSKILLIT_PROJECT_DIR", raising=False)

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 0, stdout="/fake/git/root\n", stderr="")

    monkeypatch.setattr("autoskillit.server._factory.subprocess.run", fake_run)
    assert _resolve_project_dir() == Path("/fake/git/root")


def test_resolve_project_dir_cwd_fallback(monkeypatch):
    import subprocess as _subprocess

    from autoskillit.server._factory import _resolve_project_dir

    monkeypatch.delenv("AUTOSKILLIT_PROJECT_DIR", raising=False)

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not a git repo")

    monkeypatch.setattr("autoskillit.server._factory.subprocess.run", fake_run)
    assert _resolve_project_dir() == Path.cwd()


# --- AUTOSKILLIT_PROVIDER_PROFILE env override tests ---


def test_make_context_env_profile_overrides_default_provider(monkeypatch):
    """AUTOSKILLIT_PROVIDER_PROFILE in env must set config.providers.default_provider."""
    monkeypatch.setenv("AUTOSKILLIT_PROVIDER_PROFILE", "minimax")
    config = AutomationConfig()
    config.providers.default_provider = None
    ctx = make_context(config, runner=_runner())
    assert ctx.config.providers.default_provider == "minimax"


def test_make_context_env_profile_overrides_existing_default(monkeypatch):
    """AUTOSKILLIT_PROVIDER_PROFILE overrides even a config-set default_provider."""
    monkeypatch.setenv("AUTOSKILLIT_PROVIDER_PROFILE", "minimax")
    config = AutomationConfig()
    config.providers.default_provider = "openai"
    ctx = make_context(config, runner=_runner())
    assert ctx.config.providers.default_provider == "minimax"


def test_make_context_no_env_profile_preserves_config_default(monkeypatch):
    """Without AUTOSKILLIT_PROVIDER_PROFILE in env, default_provider is unchanged."""
    monkeypatch.delenv("AUTOSKILLIT_PROVIDER_PROFILE", raising=False)
    config = AutomationConfig()
    config.providers.default_provider = "openai"
    ctx = make_context(config, runner=_runner())
    assert ctx.config.providers.default_provider == "openai"
