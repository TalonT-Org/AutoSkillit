"""Tests for provider_extras/profile_name forwarding through run_skill()."""

from __future__ import annotations

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_when_feature_disabled(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("provider_extras") is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_for_anthropic_sentinel(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("anthropic", {"SOME_KEY": "val"}),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("provider_extras") is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_run_skill_provider_extras_forwarded_for_non_anthropic(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("bedrock", {"AWS_REGION": "us-east-1"}),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("provider_extras") == {"AWS_REGION": "us-east-1"}
    assert captured.get("profile_name") == "bedrock"
    assert captured.get("provider_name") == "bedrock"


@pytest.mark.anyio
async def test_run_skill_model_as_profile_resolves_provider(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("anthropic", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_model_as_profile",
        lambda *a: ("M2.7", "minimax", {"BASE_URL": "https://api.minimax.chat/v1"}),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("model") == "M2.7"
    assert captured.get("provider_extras") == {"BASE_URL": "https://api.minimax.chat/v1"}
    assert captured.get("profile_name") == "minimax"
    assert captured.get("provider_name") == "minimax"


@pytest.mark.anyio
async def test_run_skill_step_overrides_win_over_model_as_profile(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("bedrock", {"AWS_REGION": "us-east-1"}),
    )
    map_called = []
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_model_as_profile",
        lambda *a: map_called.append(True) or ("", "", None),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), model="minimax")

    assert captured.get("provider_extras") == {"AWS_REGION": "us-east-1"}
    assert captured.get("profile_name") == "bedrock"
    assert not map_called


@pytest.mark.anyio
async def test_run_skill_model_as_profile_disabled_when_feature_off(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), model="minimax")

    assert captured.get("model") == "minimax"
    assert captured.get("provider_extras") is None


@pytest.mark.anyio
async def test_run_skill_model_as_profile_no_anthropic_model_falls_through(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("anthropic", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_model_as_profile",
        lambda *a: ("", "", None),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("model") == ""
    assert captured.get("provider_extras") is None


@pytest.mark.anyio
async def test_run_skill_model_overrides_applied(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from autoskillit.config.settings import ProvidersConfig
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.recipe_name = "implementation"
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)
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
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)
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
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)
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
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)
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
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)
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
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)
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
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path), step_name="plan")

    assert captured.get("provider_extras") is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_run_skill_backend_override_codex_with_anthropic_base_url(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    type(fake_backend).name = property(lambda self: "codex")
    tool_ctx_kitchen_open.backend = fake_backend
    # Skip Codex session init: copies ~/.codex/config.toml which is absent in CI
    tool_ctx_kitchen_open.session_skill_manager = None
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
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

    assert captured.get("backend_override") == "claude-code"


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
    type(fake_backend).name = property(lambda self: "codex")
    tool_ctx_kitchen_open.backend = fake_backend
    tool_ctx_kitchen_open.session_skill_manager = None
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
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
    type(fake_backend).name = property(lambda self: "claude-code")
    tool_ctx_kitchen_open.backend = fake_backend
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
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
    type(fake_backend).name = property(lambda self: "codex")
    tool_ctx_kitchen_open.backend = fake_backend
    tool_ctx_kitchen_open.session_skill_manager = None
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)

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
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("minimax", {"BASE_URL": "https://api.minimax.chat/v1"}),
    )

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert executor.calls[0].profile_name == "minimax"
    assert executor.calls[0].provider_name == "minimax"
