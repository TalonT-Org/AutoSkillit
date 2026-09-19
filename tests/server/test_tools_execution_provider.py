"""Tests for provider_extras/profile_name forwarding through run_skill()."""

from __future__ import annotations

import pytest

import autoskillit.server as server
from autoskillit.server.tools import tools_execution
from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _provider_specific_extras(captured: dict) -> dict[str, str] | None:
    extras = captured.get("provider_extras") or {}
    provider_extras = {
        key: value for key, value in extras.items() if key != "AUTOSKILLIT_SESSION_DEADLINE"
    }
    return provider_extras or None


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_when_feature_disabled(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert _provider_specific_extras(captured) is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_for_anthropic_sentinel(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    from autoskillit.server.lifecycle import _guards

    monkeypatch.setattr(
        _guards,
        "_resolve_provider_profile",
        lambda *a, **kw: ("anthropic", {"SOME_KEY": "val"}),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert _provider_specific_extras(captured) is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_run_skill_provider_extras_forwarded_for_non_anthropic(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        default_provider="bedrock",
        profiles={"bedrock": {"AWS_REGION": "us-east-1"}},
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert _provider_specific_extras(captured) == {"AWS_REGION": "us-east-1"}
    assert captured.get("profile_name") == "bedrock"
    assert captured.get("provider_name") == "bedrock"


def test_execution_candidates_build_fresh_profile_environment(tool_ctx) -> None:
    """A rejected candidate cannot leak its profile variables into the next candidate."""
    from autoskillit.config import ExecutionCandidateSpec
    from autoskillit.config.settings import ProvidersConfig
    from autoskillit.core import BackendAuthority, BackendAuthorityKind, BackendAuthorityTier
    from autoskillit.server.tools.tools_execution._candidate_policy import resolve_candidate_policy

    tool_ctx.config.providers = ProvidersConfig(
        profiles={
            "first": {"base_url": "https://first.example"},
            "second": {"base_url": "https://second.example"},
        }
    )
    authority = BackendAuthority(
        backend="claude-code",
        kind=BackendAuthorityKind.GLOBAL,
        tier=BackendAuthorityTier.GLOBAL,
        key_path="agent_backend.backend",
    )
    _, _, first_environment = resolve_candidate_policy(
        tool_ctx.config,
        authority=authority,
        candidate=ExecutionCandidateSpec(backend="claude-code", profile="first"),
        ordinal=1,
        step_name="",
        recipe_name="",
        step_provider="",
        requested_model="",
        providers_enabled=True,
    )
    first_environment["CANDIDATE_ONLY"] = "leaked"
    _, _, second_environment = resolve_candidate_policy(
        tool_ctx.config,
        authority=authority,
        candidate=ExecutionCandidateSpec(backend="claude-code", profile="second"),
        ordinal=2,
        step_name="",
        recipe_name="",
        step_provider="",
        requested_model="",
        providers_enabled=True,
    )

    assert first_environment["ANTHROPIC_BASE_URL"] == "https://first.example"
    assert second_environment == {"ANTHROPIC_BASE_URL": "https://second.example"}


def test_backend_named_candidate_profile_supplies_its_environment(tool_ctx) -> None:
    from autoskillit.config import ExecutionCandidateSpec
    from autoskillit.config.settings import ProvidersConfig
    from autoskillit.core import BackendAuthority, BackendAuthorityKind, BackendAuthorityTier
    from autoskillit.server.tools.tools_execution._candidate_policy import resolve_candidate_policy

    tool_ctx.config.providers = ProvidersConfig(profiles={"codex": {"CODEX_API_KEY": "key"}})
    authority = BackendAuthority(
        backend="codex",
        kind=BackendAuthorityKind.GLOBAL,
        tier=BackendAuthorityTier.GLOBAL,
        key_path="agent_backend.backend",
    )

    binding, _, environment = resolve_candidate_policy(
        tool_ctx.config,
        authority=authority,
        candidate=ExecutionCandidateSpec(backend="codex", profile="codex"),
        ordinal=1,
        step_name="",
        recipe_name="",
        step_provider="",
        requested_model="",
        providers_enabled=True,
    )

    assert binding.profile == "codex"
    assert environment == {"CODEX_API_KEY": "key"}


@pytest.mark.parametrize(
    ("backend_name", "expected_provider"),
    [("claude-code", "anthropic"), ("codex", "codex")],
)
def test_native_candidate_binding_has_named_provider(
    tool_ctx, backend_name: str, expected_provider: str
) -> None:
    from autoskillit.core import BackendAuthority, BackendAuthorityKind, BackendAuthorityTier
    from autoskillit.server.tools.tools_execution._candidate_policy import resolve_candidate_policy

    authority = BackendAuthority(
        backend=backend_name,
        kind=BackendAuthorityKind.GLOBAL,
        tier=BackendAuthorityTier.GLOBAL,
        key_path="agent_backend.backend",
    )
    binding, _, _ = resolve_candidate_policy(
        tool_ctx.config,
        authority=authority,
        candidate=None,
        ordinal=1,
        step_name="",
        recipe_name="",
        step_provider="",
        requested_model="",
        providers_enabled=False,
    )

    assert binding.provider == expected_provider
    assert binding.required_backend == backend_name


@pytest.mark.anyio
async def test_run_skill_model_as_profile_resolves_provider(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setitem(tool_ctx_kitchen_open.config.features, "providers", True)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        profiles={
            "minimax": {
                "base_url": "https://api.minimax.chat/v1",
                "ANTHROPIC_MODEL": "M2.7",
            }
        }
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), model="minimax")

    assert captured.get("model") == "M2.7"
    assert _provider_specific_extras(captured) == {
        "ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1",
        "ANTHROPIC_MODEL": "M2.7",
    }
    assert captured.get("profile_name") == "minimax"
    assert captured.get("provider_name") == "minimax"


@pytest.mark.anyio
async def test_run_skill_step_overrides_win_over_model_as_profile(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    tool_ctx_kitchen_open.recipe_name = "implementation"
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        profiles={"bedrock": {"AWS_REGION": "us-east-1"}},
        step_overrides={"probe": "bedrock"},
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="probe", model="minimax")

    assert _provider_specific_extras(captured) == {"AWS_REGION": "us-east-1"}
    assert captured.get("profile_name") == "bedrock"
    assert captured.get("model") == "minimax"


@pytest.mark.anyio
async def test_run_skill_model_as_profile_disabled_when_feature_off(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), model="minimax")

    assert captured.get("model") == "minimax"
    assert _provider_specific_extras(captured) is None


@pytest.mark.anyio
async def test_run_skill_model_as_profile_no_anthropic_model_falls_through(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("model") == "sonnet"
    assert _provider_specific_extras(captured) is None


@pytest.mark.anyio
async def test_run_skill_model_overrides_applied(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        model_overrides={"implementation": {"implement": "opus"}}
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="implement")

    assert captured.get("model") == "opus"


@pytest.mark.anyio
async def test_run_skill_model_overrides_wildcard_step(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        model_overrides={"implementation": {"*": "opus"}}
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="plan")

    assert captured.get("model") == "opus"


@pytest.mark.anyio
async def test_run_skill_model_overrides_exact_over_wildcard(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        model_overrides={"implementation": {"implement": "opus", "*": "haiku"}}
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="implement")

    assert captured.get("model") == "opus"


@pytest.mark.anyio
async def test_run_skill_model_overrides_without_providers_feature(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        model_overrides={"implementation": {"implement": "opus"}}
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="implement")

    assert captured.get("model") == "opus"


@pytest.mark.anyio
async def test_run_skill_model_overrides_no_match_passthrough(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        model_overrides={"remediation": {"implement": "opus"}}
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), model="sonnet", step_name="implement")

    assert captured.get("model") == "sonnet"


@pytest.mark.anyio
async def test_run_skill_global_override_beats_model_overrides(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        model_overrides={"implementation": {"implement": "opus"}}
    )
    tool_ctx_kitchen_open.config.model.model_override = "haiku"

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="implement")

    assert captured.get("model") == "haiku"


@pytest.mark.anyio
async def test_run_skill_no_provider_profile_injected_for_default_step(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """run_skill with step_name='plan' and no provider config must NOT
    inject AUTOSKILLIT_PROVIDER_PROFILE into the subprocess."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="plan")

    assert _provider_specific_extras(captured) is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_anthropic_base_url_cannot_override_codex_backend_authority(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.backend = get_backend("codex")
    tool_ctx_kitchen_open.session_skill_manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "ephemeral-sessions",
        persistent_roots={"codex": tmp_path / "persistent-sessions"},
    )
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    from autoskillit.server.lifecycle import _guards

    monkeypatch.setattr(
        _guards,
        "_resolve_provider_profile",
        lambda *a, **kw: (
            "bedrock",
            {
                "ANTHROPIC_BASE_URL": "https://bedrock.us-east-1.amazonaws.com",
                "AWS_REGION": "us-east-1",
            },
        ),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    authority = captured["backend_authority"]
    assert authority.backend == "codex"
    assert authority.kind.value == "global"


@pytest.mark.anyio
async def test_run_skill_backend_override_none_no_anthropic_base_url(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.capabilities.anthropic_provider_capable = True
    tool_ctx_kitchen_open.backend = fake_backend
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    from autoskillit.server.lifecycle import _guards

    monkeypatch.setattr(
        _guards,
        "_resolve_provider_profile",
        lambda *a, **kw: ("minimax", {"BASE_URL": "https://api.minimax.chat/v1"}),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("backend_override") is None


@pytest.mark.anyio
async def test_run_skill_backend_override_none_claude_code_backend(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.capabilities.anthropic_provider_capable = True
    tool_ctx_kitchen_open.backend = fake_backend
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    from autoskillit.server.lifecycle import _guards

    monkeypatch.setattr(
        _guards,
        "_resolve_provider_profile",
        lambda *a, **kw: (
            "bedrock",
            {"ANTHROPIC_BASE_URL": "https://bedrock.us-east-1.amazonaws.com"},
        ),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("backend_override") is None


@pytest.mark.anyio
async def test_run_skill_backend_override_none_providers_disabled(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: False)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("backend_override") is None


@pytest.mark.anyio
async def test_run_skill_forwards_provider_name_matching_profile(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """run_skill must pass provider_name=profile_name_out so telemetry is populated."""
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)
    _feat = tools_execution
    monkeypatch.setattr(_feat, "is_feature_enabled", lambda *a, **kw: True)
    tool_ctx_kitchen_open.config.providers = ProvidersConfig(
        default_provider="minimax",
        profiles={"minimax": {"base_url": "https://api.minimax.chat/v1"}},
    )

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert executor.calls[0].profile_name == "minimax"
    assert executor.calls[0].provider_name == "minimax"
