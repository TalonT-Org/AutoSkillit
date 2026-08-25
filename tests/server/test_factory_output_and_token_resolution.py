"""make_context() output-pattern and token resolution behavior."""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.execution.github import DefaultGitHubFetcher
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.server._factory import _gh_cli_token, _LazyTokenFactory, make_context
from tests.server._factory_test_helpers import _runner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_make_context_github_client_is_default_fetcher(tmp_path):
    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".", project_dir=tmp_path)
    assert isinstance(ctx.github_client, DefaultGitHubFetcher)


def test_make_context_github_client_uses_config_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config = AutomationConfig()
    config.github.token = "config-token"
    ctx = make_context(config, runner=None, plugin_dir=".", project_dir=tmp_path)
    assert ctx.github_client.has_token is True


def test_make_context_github_client_uses_env_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    config = AutomationConfig()
    ctx = make_context(config, runner=None, plugin_dir=".", project_dir=tmp_path)
    assert ctx.github_client.has_token is True


def test_make_context_github_client_no_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("autoskillit.server._factory._gh_cli_token", lambda: None)
    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".", project_dir=tmp_path)
    assert ctx.github_client.has_token is False


def test_make_context_github_client_uses_gh_cli_fallback(monkeypatch, tmp_path):
    """When no config token or GITHUB_TOKEN env var, fall back to gh auth token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("autoskillit.server._factory._gh_cli_token", lambda: "gh-cli-token")
    config = AutomationConfig()
    ctx = make_context(config, runner=None, plugin_dir=".", project_dir=tmp_path)
    assert ctx.github_client.has_token is True


def test_make_context_github_client_config_token_takes_priority_over_gh_cli(monkeypatch, tmp_path):
    """Config token takes priority over gh CLI token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("autoskillit.server._factory._gh_cli_token", lambda: "gh-cli-token")
    config = AutomationConfig()
    config.github.token = "config-token"
    ctx = make_context(config, runner=None, plugin_dir=".", project_dir=tmp_path)
    assert ctx.github_client.has_token is True
    # After lazy resolution via has_token, verify the resolved value
    assert ctx.github_client._resolve_token() == "config-token"


def test_make_context_github_client_token_snapshot_is_immutable(monkeypatch, tmp_path):
    """Token is snapshotted at construction. Changing env after does not affect the fetcher."""
    monkeypatch.setenv("GITHUB_TOKEN", "startup-token")
    ctx = make_context(AutomationConfig(), runner=None, plugin_dir=".", project_dir=tmp_path)
    assert ctx.github_client.has_token is True
    monkeypatch.delenv("GITHUB_TOKEN")
    assert ctx.github_client.has_token is True


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
    tmp_path, invocation: str, expected_mode: str | None, required_tokens: list[str]
) -> None:
    """write_expected_resolver returns the correct mode and token patterns per skill."""
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert ctx.write_expected_resolver is not None
    spec = ctx.write_expected_resolver(invocation)
    assert spec.mode == expected_mode
    if expected_mode is None:
        assert spec.expected_when == ()
    else:
        for token in required_tokens:
            assert any(token in p for p in spec.expected_when)


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


def test_gh_cli_token_not_called_during_make_context(monkeypatch, tmp_path):
    """make_context() must not call _gh_cli_token() — token resolves lazily."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    calls: list[object] = []
    original_run = __import__("subprocess").run

    def tracking_run(*args, **kwargs):
        calls.append(args)
        return original_run(*args, **kwargs)

    monkeypatch.setattr("autoskillit.server._factory.subprocess.run", tracking_run)

    config = AutomationConfig()
    make_context(config, runner=None, plugin_dir=".", project_dir=tmp_path)

    gh_calls = [c for c in calls if "gh" in str(c)]
    assert gh_calls == [], f"_gh_cli_token() called during make_context: {gh_calls}"
