"""Tests for server-side recipe step parameter resolution in run_skill."""

from __future__ import annotations

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_skill_resolves_output_dir_from_recipe_step(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When output_dir is omitted but step_name maps to a recipe step with
    output_dir in with_args, the server resolves it automatically."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(
        name="verify",
        with_args={"output_dir": str(tmp_path / ".autoskillit" / "temp")},
    )
    tool_ctx_kitchen_open.active_recipe_steps = {"verify": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    plan = tmp_path / "plan.md"
    plan.write_text("content")
    await run_skill(f"/dry-walkthrough {plan}", str(tmp_path), step_name="verify")

    assert len(executor.calls) == 1
    expected_prefix = str(tmp_path / ".autoskillit" / "temp") + "/"
    assert executor.calls[0].allowed_write_prefix == expected_prefix
    assert executor.calls[0].allowed_write_prefixes == (expected_prefix,)


@pytest.mark.anyio
async def test_run_skill_resolves_stale_threshold_from_recipe_step(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When stale_threshold is None but step_name maps to a recipe step with
    stale_threshold set, the server uses the recipe value."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(name="implement", stale_threshold=2400)
    tool_ctx_kitchen_open.active_recipe_steps = {"implement": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement ...", str(tmp_path), step_name="implement")

    assert executor.calls[0].stale_threshold == 2400.0


@pytest.mark.anyio
async def test_run_skill_resolves_idle_output_timeout_from_recipe_step(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When idle_output_timeout is None but step_name maps to a recipe step
    with idle_output_timeout=0, the server uses the recipe value (disabled)."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(name="idle-scope", idle_output_timeout=0)
    tool_ctx_kitchen_open.active_recipe_steps = {"idle-scope": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/idle-scope ...", str(tmp_path), step_name="idle-scope")

    assert executor.calls[0].idle_output_timeout == 0.0


@pytest.mark.anyio
async def test_run_skill_llm_provided_params_override_recipe_step(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """Explicit caller-provided values must override recipe step defaults."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(
        name="verify",
        with_args={"output_dir": str(tmp_path / "recipe-default")},
        stale_threshold=2400,
    )
    tool_ctx_kitchen_open.active_recipe_steps = {"verify": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    plan = tmp_path / "plan.md"
    plan.write_text("content")
    override_dir = str(tmp_path / "explicit-override")
    await run_skill(
        f"/dry-walkthrough {plan}",
        str(tmp_path),
        step_name="verify",
        output_dir=override_dir,
        stale_threshold=3600,
    )

    assert executor.calls[0].allowed_write_prefix == override_dir + "/"
    assert executor.calls[0].allowed_write_prefixes == (override_dir + "/",)
    assert executor.calls[0].stale_threshold == 3600.0


@pytest.mark.anyio
async def test_run_skill_logs_warning_when_output_dir_resolved_from_recipe(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """Server-side resolution should log a warning so LLM forwarding gaps are visible.

    Uses structlog.testing.capture_logs() because the conftest's autouse
    _structlog_to_null fixture intercepts all structlog output, making capsys
    and caplog unable to capture it.
    """
    import structlog.testing

    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(
        name="verify",
        with_args={"output_dir": str(tmp_path / ".autoskillit" / "temp")},
    )
    tool_ctx_kitchen_open.active_recipe_steps = {"verify": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    plan = tmp_path / "plan.md"
    plan.write_text("content")
    with structlog.testing.capture_logs() as cap:
        await run_skill(f"/dry-walkthrough {plan}", str(tmp_path), step_name="verify")

    assert any(entry.get("event") == "output_dir_resolved_from_recipe" for entry in cap)


@pytest.mark.anyio
async def test_run_skill_skips_auto_fill_when_output_dir_has_template(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When recipe step output_dir has ${{ }}, auto-fill MUST skip AND fallback
    to resolve_skill_temp_dir — producing the correct skill-scoped prefix."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    plan = tmp_path / "plan.md"
    plan.write_text("content")
    tool_ctx_kitchen_open.active_recipe_steps = {
        "verify": RecipeStep(
            name="verify",
            with_args={
                "output_dir": "${{ context.work_dir }}/.autoskillit/temp",
                "skill_command": f"/autoskillit:dry-walkthrough {plan}",
            },
        )
    }
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill(
        skill_command=f"/autoskillit:dry-walkthrough {plan}",
        cwd=str(tmp_path),
        step_name="verify",
    )
    expected = str(tmp_path / ".autoskillit" / "temp" / "dry-walkthrough") + "/"
    assert executor.calls[0].allowed_write_prefix == expected
    assert executor.calls[0].allowed_write_prefixes == (expected,)


@pytest.mark.anyio
async def test_run_skill_auto_fills_relative_output_dir_from_recipe(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When recipe step output_dir is a server-resolvable relative path,
    auto-fill MUST resolve it against cwd and produce the correct prefix."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    plan = tmp_path / "plan.md"
    plan.write_text("content")
    tool_ctx_kitchen_open.active_recipe_steps = {
        "verify": RecipeStep(
            name="verify",
            with_args={"output_dir": ".autoskillit/temp"},
        )
    }
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill(
        skill_command=f"/autoskillit:dry-walkthrough {plan}",
        cwd=str(tmp_path),
        step_name="verify",
    )
    expected = str(tmp_path / ".autoskillit" / "temp") + "/"
    assert executor.calls[0].allowed_write_prefix == expected
    assert executor.calls[0].allowed_write_prefixes == (expected,)


@pytest.mark.anyio
async def test_run_skill_resolves_step_provider_from_recipe_step(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """step_provider auto-filled from RecipeStep.provider when caller omits it."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(name="run_canaries", provider="minimax")
    tool_ctx_kitchen_open.active_recipe_steps = {"run_canaries": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)

    captured_kwargs: dict = {}

    def spy(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"})

    monkeypatch.setattr("autoskillit.server._guards._resolve_provider_profile", spy)

    await run_skill(
        "/eval-agent --agent-name test",
        str(tmp_path),
        step_name="run_canaries",
        step_provider="",
    )

    assert captured_kwargs["step_provider"] == "minimax"


@pytest.mark.anyio
async def test_run_skill_llm_step_provider_overrides_recipe_step(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """Explicit caller step_provider must not be overridden by recipe step."""
    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(name="run_canaries", provider="minimax")
    tool_ctx_kitchen_open.active_recipe_steps = {"run_canaries": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)

    captured_kwargs: dict = {}

    def spy(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return ("bedrock", {"AWS_REGION": "us-east-1"})

    monkeypatch.setattr("autoskillit.server._guards._resolve_provider_profile", spy)

    await run_skill(
        "/eval-agent --agent-name test",
        str(tmp_path),
        step_name="run_canaries",
        step_provider="bedrock",
    )

    assert captured_kwargs["step_provider"] == "bedrock"


@pytest.mark.anyio
async def test_run_skill_logs_warning_when_step_provider_resolved_from_recipe(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """Server-side step_provider resolution must log for observability."""
    import structlog.testing

    from autoskillit.recipe.schema import RecipeStep
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    step = RecipeStep(name="run_canaries", provider="minimax")
    tool_ctx_kitchen_open.active_recipe_steps = {"run_canaries": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("minimax", {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}),
    )

    with structlog.testing.capture_logs() as cap:
        await run_skill(
            "/eval-agent --agent-name test",
            str(tmp_path),
            step_name="run_canaries",
            step_provider="",
        )

    resolved_events = [e for e in cap if e.get("event") == "step_provider_resolved_from_recipe"]
    assert len(resolved_events) == 1
    assert resolved_events[0]["step"] == "run_canaries"
    assert resolved_events[0]["provider"] == "minimax"
