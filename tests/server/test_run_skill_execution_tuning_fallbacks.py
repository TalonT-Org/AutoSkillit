"""RecipeStep.model fallback and vacancy-sentinel guards for run_skill (#4402).

Extends the proven #2969/#3377 server-side RecipeStep fallback pattern (see
``tests/server/test_tools_execution_step_resolution.py`` for the
``stale_threshold``/``output_dir`` precedents) to ``model`` — the one
EXECUTION_TUNING param that previously had no fallback and zero runtime
consumer, despite ``RecipeStep.model`` being parsed and lint-validated.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.schema import RecipeStep
from autoskillit.server.tools.tools_execution import run_skill
from tests.fakes import InMemoryHeadlessExecutor

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_empty_caller_model_falls_back_to_recipe_step_model(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """caller model="" + RecipeStep.model set -> the recipe's model reaches the executor."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    step = RecipeStep(name="implement", model="claude-sonnet-5")
    tool_ctx_kitchen_open.active_recipe_steps = {"implement": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement ...", str(tmp_path), step_name="implement")

    assert executor.calls[0].model == "claude-sonnet-5"


@pytest.mark.anyio
async def test_explicit_caller_model_beats_recipe_step_model(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """An explicit non-empty caller model must win — the fallback only fills a vacancy."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    step = RecipeStep(name="implement", model="claude-sonnet-5")
    tool_ctx_kitchen_open.active_recipe_steps = {"implement": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement ...", str(tmp_path), step_name="implement", model="claude-opus-5")

    assert executor.calls[0].model == "claude-opus-5"


@pytest.mark.anyio
async def test_both_model_sources_empty_does_not_crash(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """Neither caller nor recipe step declares model -> "" reaches the executor
    (downstream resolve_model_pin's tier-5 default_model applies from there; this
    only pins that the vacancy is left alone, not what the eventual default is)."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    step = RecipeStep(name="implement")
    tool_ctx_kitchen_open.active_recipe_steps = {"implement": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement ...", str(tmp_path), step_name="implement")

    assert executor.calls[0].model == ""


@pytest.mark.anyio
async def test_config_model_override_beats_recipe_step_fallback(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """config.model.model_override is a pre-executor config tier that already
    outranks the param channel (tools_execution.py's own effective_model
    resolution, ahead of the RecipeStep fallback this plan adds) — the
    RecipeStep.model fallback must not un-do it."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.config.model.model_override = "config-forced-model"
    step = RecipeStep(name="implement", model="claude-sonnet-5")
    tool_ctx_kitchen_open.active_recipe_steps = {"implement": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement ...", str(tmp_path), step_name="implement")

    assert executor.calls[0].model == "config-forced-model"


@pytest.mark.anyio
async def test_explicit_zero_idle_output_timeout_is_not_overwritten(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """idle_output_timeout=0 is documented as "disabled for this step" (handler
    docstring) — the vacancy sentinel for this int param is `is None`, never a
    falsy check, so an explicit 0 must survive untouched even when the recipe
    step declares a truthy fallback value."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    step = RecipeStep(name="implement", idle_output_timeout=900)
    tool_ctx_kitchen_open.active_recipe_steps = {"implement": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement ...", str(tmp_path), step_name="implement", idle_output_timeout=0)

    assert executor.calls[0].idle_output_timeout == 0.0


@pytest.mark.anyio
async def test_unresolved_model_template_is_not_forwarded(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """#4402 remediation — RecipeStep.model is a plain str field, unlike the int
    stale_threshold/idle_output_timeout fields, so it can (and in the real
    bundled remediation.yaml investigate step, does) carry an unresolved
    ``${{ ... }}`` ingredient template: load_recipe() is a thin YAML parse
    with no interpolation. The fallback must skip it — mirroring the
    identical "${{" guard the output_dir fallback already has — rather than
    forward a broken template string as --model. See
    test_attestation_delivery_reachability.py::
    test_attested_run_skill_never_forwards_an_unresolved_model_template for
    the same guard proven against the real recipe step."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    step = RecipeStep(
        name="implement",
        model="${{ 'opus[1m]' if inputs.depth == 'deep' else 'sonnet' }}",
    )
    tool_ctx_kitchen_open.active_recipe_steps = {"implement": step}
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement ...", str(tmp_path), step_name="implement")

    assert "${{" not in executor.calls[0].model
    assert executor.calls[0].model == ""


def test_execution_tuning_step_fields_have_matching_runtime_read_sites() -> None:
    """_EXECUTION_TUNING_STEP_FIELDS documents which RecipeStep fields the
    post-gate fallback reads — it is NOT itself iterated at runtime (each
    field needs a distinct vacancy-sentinel check and writes a distinct
    local variable, which Python cannot dispatch generically by name
    without unsafe locals() mutation). This is the alternative safety net:
    every table entry must have a real ``_recipe_step.<field>`` read site
    inside run_skill(), so a table entry added without a matching if-block
    — the exact silent-no-op drift this table exists to prevent — fails CI
    instead of silently doing nothing at runtime."""
    import ast
    import inspect

    from autoskillit.server.tools import tools_execution

    tree = ast.parse(inspect.getsource(tools_execution.run_skill))
    read_fields = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "_recipe_step"
        and isinstance(node.ctx, ast.Load)
    }
    missing = [
        field_name
        for field_name in tools_execution._EXECUTION_TUNING_STEP_FIELDS.values()
        if field_name not in read_fields
    ]
    assert not missing, (
        f"EXECUTION_TUNING RecipeStep field(s) in _EXECUTION_TUNING_STEP_FIELDS have "
        f"no matching '_recipe_step.<field>' read site inside run_skill(): {missing}. "
        "A table entry with no runtime consumer silently does nothing — add the "
        "matching fallback if-block (see model/stale_threshold/idle_output_timeout "
        "for the pattern)."
    )
