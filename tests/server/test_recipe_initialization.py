"""Server-owned recipe initialization completion transactions."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import Mock

import pytest

import autoskillit.server._recipe_initialization as recipe_initialization
import autoskillit.server._state as server_state
from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    FinalizedRecipeProjection,
    FinalizedRecipeSegment,
    RecipeArtifactGeneration,
    RecipeBindingProjection,
    RecipeExecutionCredential,
    RecipeFlowGeneration,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    ReadyRecipe,
    RecipeInitializationRequirement,
    record_initialization_page,
)
from autoskillit.server._factory import make_recipe_execution
from autoskillit.server._recipe_execution import (
    RecipeExecutionAdmissionError,
    build_recipe_execution_snapshot,
    install_recipe_execution,
    prepare_recipe_execution,
)
from autoskillit.server._recipe_initialization import (
    FinalizedRecipeInitializationResponse,
    FinalizedRecipeSectionResponse,
    admit_registered_tool_during_initialization,
    build_completion_response,
    complete_initialization_response,
    complete_section_response,
    recipe_initialization_receipt,
    replay_terminal_section_response,
    stage_recipe_initialization,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _hash(seed: str) -> str:
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


def _stage(tool_ctx, tmp_path) -> InitializingRecipe:
    tool_ctx.kitchen_id = "kitchen"
    snapshot = build_recipe_execution_snapshot(
        recipe_name="recipe",
        content_hash=_hash("content"),
        composite_hash=_hash("composite"),
        projection=RecipeBindingProjection({}),
        execution_id="execution",
    )
    flow = RecipeFlowGeneration(
        schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        records=(
            json.dumps(
                {"kind": "entrypoint", "name": "step"},
                separators=(",", ":"),
                sort_keys=True,
            ),
        ),
    )
    artifact = RecipeArtifactGeneration(
        producer_tool="open_kitchen",
        recipe_name="recipe",
        descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
        schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
        payload_sha256=_hash("payload"),
        artifact_blob_sha256=_hash("blob"),
        artifact_blob_size_bytes=10,
        body_sha256=_hash("body"),
        body_size_bytes=5,
        flow_schema_version=flow.schema_version,
        flow_sha256=flow.flow_sha256,
        flow_size_bytes=flow.flow_size_bytes,
        flow_record_count=flow.record_count,
    )
    tool_ctx.project_dir = tmp_path
    tool_ctx.recipe_execution_factory = make_recipe_execution
    return stage_recipe_initialization(
        tool_ctx,
        recipe_name="recipe",
        artifact_generation=artifact,
        flow_generation=flow,
        initialization_id="initialization",
        staged_snapshot=snapshot,
        requirements=(
            RecipeInitializationRequirement(
                section="flow_records",
                page_plan_sha256=_hash("flow-plan"),
                total_parts=1,
            ),
        ),
        generation_store_key="compile-key",
        finalized_projection=FinalizedRecipeProjection(
            binding_projection=RecipeBindingProjection({}),
            ordered_step_names=("step",),
            entrypoint="step",
            ordered_flow_edges=(),
        ),
    )


def test_completion_is_server_owned_and_commits_ready_only_after_enforcement(
    minimal_ctx,
    tmp_path,
) -> None:
    state = _stage(minimal_ctx, tmp_path)

    incomplete = json.loads(build_completion_response(minimal_ctx, "initialization"))
    assert incomplete == {
        "success": False,
        "error": "recipe_initialization_incomplete",
    }

    with minimal_ctx.recipe_execution_lock:
        minimal_ctx.recipe_initialization_state = record_initialization_page(
            state,
            initialization_id="initialization",
            section="flow_records",
            page_plan_sha256=_hash("flow-plan"),
            part=0,
        )

    finalized = build_completion_response(minimal_ctx, "initialization")
    assert isinstance(finalized, FinalizedRecipeInitializationResponse)
    assert isinstance(minimal_ctx.recipe_initialization_state, InitializingRecipe)

    altered = complete_initialization_response(finalized, "substituted")
    altered_parsed = json.loads(altered)
    assert altered_parsed["success"] is False
    assert altered_parsed["error"] == "recipe_initialization_receipt_altered"
    assert altered_parsed["initialization_id"] == finalized.initialization_id
    assert "user_visible_message" in altered_parsed
    assert altered_parsed["response_budget_error"] == ""
    assert isinstance(minimal_ctx.recipe_initialization_state, InitializingRecipe)

    budget_failure = json.dumps({"success": False, "error": "response_budget_exceeded"})
    budget_parsed = json.loads(complete_initialization_response(finalized, budget_failure))
    assert budget_parsed["error"] == "recipe_initialization_receipt_altered"
    assert budget_parsed["response_budget_error"] == "response_budget_exceeded"
    assert isinstance(minimal_ctx.recipe_initialization_state, InitializingRecipe)

    assert complete_initialization_response(finalized, finalized.rendered) == finalized.rendered
    assert isinstance(minimal_ctx.recipe_initialization_state, ReadyRecipe)

    replay = build_completion_response(minimal_ctx, "initialization")
    assert replay == finalized.rendered

    parsed_initial = json.loads(finalized.rendered)
    parsed_replay = json.loads(replay)
    assert set(parsed_initial["recipe_execution"].keys()) == {
        "execution_id",
        "invocation_template_digests",
        "skill_input_shapes",
        "snapshot_digest",
    }
    assert parsed_initial["recipe_execution"] == parsed_replay["recipe_execution"]


def test_segmented_completion_credential_is_scoped_to_initial_bodies() -> None:
    projection = FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection({}),
        ordered_step_names=("initial", "future"),
        entrypoint="initial",
        ordered_flow_edges=(),
        delivery_segments=(
            FinalizedRecipeSegment(name="S0", ordered_step_names=("initial",)),
            FinalizedRecipeSegment(name="S1", ordered_step_names=("future",)),
        ),
    )
    credential = RecipeExecutionCredential(
        execution_id="execution",
        snapshot_digest=_hash("snapshot"),
        invocation_template_digests={"initial": _hash("initial"), "future": _hash("future")},
        skill_input_shapes={
            "initial": {"keys": ["task"], "unresolved_defaults": {}},
            "future": {"keys": ["review_path"], "unresolved_defaults": {"review_path": ""}},
        },
    )

    public_credential = recipe_initialization._public_completion_credential(credential, projection)

    assert public_credential.execution_id == credential.execution_id
    assert public_credential.snapshot_digest == credential.snapshot_digest
    assert public_credential.invocation_template_digests == {"initial": _hash("initial")}
    assert public_credential.skill_input_shapes == {
        "initial": {"keys": ["task"], "unresolved_defaults": {}}
    }


def test_install_rejects_initializing_recipe_without_completion_receipt(
    minimal_ctx,
    tmp_path,
) -> None:
    state = _stage(minimal_ctx, tmp_path)
    prepared = prepare_recipe_execution(minimal_ctx, snapshot=state.staged_snapshot)

    with pytest.raises(RecipeExecutionAdmissionError, match="without a completion receipt"):
        install_recipe_execution(minimal_ctx, prepared_execution=prepared)


def test_terminal_page_credit_atomically_installs_ready_recipe(minimal_ctx, tmp_path) -> None:
    state = _stage(minimal_ctx, tmp_path)
    content_sha256 = _hash("terminal-content")
    receipt = recipe_initialization_receipt(
        "initialization",
        state.artifact_generation,
        content_sha256=content_sha256,
    )
    rendered = json.dumps({"success": True, "completion_receipt": receipt})
    finalized = FinalizedRecipeSectionResponse(
        rendered=rendered,
        tool_ctx=minimal_ctx,
        initialization_id="initialization",
        artifact_generation=state.artifact_generation,
        section="flow_records",
        page_plan_sha256=_hash("flow-plan"),
        part=0,
        content_sha256=content_sha256,
        completion_receipt=receipt,
    )

    assert complete_section_response(finalized, rendered) == rendered
    ready = minimal_ctx.recipe_initialization_state
    assert isinstance(ready, ReadyRecipe)
    assert ready.completion_receipt == receipt
    assert (
        replay_terminal_section_response(
            minimal_ctx,
            initialization_id="initialization",
            section="flow_records",
            part=0,
            content_sha256=content_sha256,
        )
        == rendered
    )


def test_section_rejection_preserves_diagnostic_context(
    minimal_ctx,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _stage(minimal_ctx, tmp_path)
    rendered = json.dumps({"success": True})
    finalized = FinalizedRecipeSectionResponse(
        rendered=rendered,
        tool_ctx=minimal_ctx,
        initialization_id=state.initialization_id,
        artifact_generation=state.artifact_generation,
        section="flow_records",
        page_plan_sha256=_hash("flow-plan"),
        part=1,
        content_sha256=_hash("content"),
        completion_receipt=None,
    )
    logger = Mock()
    monkeypatch.setattr(recipe_initialization, "logger", logger)

    response = complete_section_response(finalized, rendered)

    assert json.loads(response) == {
        "success": False,
        "error": "recipe_initialization_page_rejected",
    }
    logger.error.assert_called_once_with(
        "recipe initialization page rejected",
        initialization_id=state.initialization_id,
        section="flow_records",
        part=1,
        exc_info=True,
    )


def test_receipt_content_mismatch_preserves_diagnostic_context(
    minimal_ctx,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _stage(minimal_ctx, tmp_path)
    rendered = json.dumps({"success": True})
    finalized = FinalizedRecipeSectionResponse(
        rendered=rendered,
        tool_ctx=minimal_ctx,
        initialization_id=state.initialization_id,
        artifact_generation=state.artifact_generation,
        section="flow_records",
        page_plan_sha256=_hash("flow-plan"),
        part=0,
        content_sha256=_hash("content"),
        completion_receipt="wrong-receipt",
    )
    expected_receipt = recipe_initialization_receipt(
        state.initialization_id,
        state.artifact_generation,
        content_sha256=finalized.content_sha256,
    )
    logger = Mock()
    monkeypatch.setattr(recipe_initialization, "logger", logger)

    response = complete_section_response(finalized, rendered)

    assert json.loads(response)["error"] == "recipe_initialization_receipt_content_mismatch"
    logger.error.assert_called_once_with(
        "recipe initialization receipt content mismatch",
        initialization_id=state.initialization_id,
        section="flow_records",
        part=0,
        expected_receipt=expected_receipt,
        observed_receipt="wrong-receipt",
    )


def test_terminal_preparation_failure_preserves_diagnostic_context(
    minimal_ctx,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _stage(minimal_ctx, tmp_path)
    content_sha256 = _hash("content")
    receipt = recipe_initialization_receipt(
        state.initialization_id,
        state.artifact_generation,
        content_sha256=content_sha256,
    )
    rendered = json.dumps({"success": True, "completion_receipt": receipt})
    finalized = FinalizedRecipeSectionResponse(
        rendered=rendered,
        tool_ctx=minimal_ctx,
        initialization_id=state.initialization_id,
        artifact_generation=state.artifact_generation,
        section="flow_records",
        page_plan_sha256=_hash("flow-plan"),
        part=0,
        content_sha256=content_sha256,
        completion_receipt=receipt,
    )
    logger = Mock()
    monkeypatch.setattr(recipe_initialization, "logger", logger)
    monkeypatch.setattr(
        recipe_initialization,
        "prepare_recipe_execution",
        Mock(side_effect=ValueError("prepare failed")),
    )

    response = complete_section_response(finalized, rendered)

    assert json.loads(response)["error"] == "recipe_execution_install_failed"
    logger.error.assert_called_once_with(
        "terminal recipe execution preparation failed",
        initialization_id=state.initialization_id,
        section="flow_records",
        part=0,
        exc_info=True,
    )


def test_terminal_page_replay_rejects_changed_content_digest(minimal_ctx, tmp_path) -> None:
    state = _stage(minimal_ctx, tmp_path)
    content_sha256 = _hash("terminal-content")
    receipt = recipe_initialization_receipt(
        "initialization",
        state.artifact_generation,
        content_sha256=content_sha256,
    )
    rendered = json.dumps({"success": True, "completion_receipt": receipt})
    finalized = FinalizedRecipeSectionResponse(
        rendered=rendered,
        tool_ctx=minimal_ctx,
        initialization_id="initialization",
        artifact_generation=state.artifact_generation,
        section="flow_records",
        page_plan_sha256=_hash("flow-plan"),
        part=0,
        content_sha256=content_sha256,
        completion_receipt=receipt,
    )
    assert complete_section_response(finalized, rendered) == rendered

    replay = replay_terminal_section_response(
        minimal_ctx,
        initialization_id="initialization",
        section="flow_records",
        part=0,
        content_sha256=_hash("tampered"),
    )
    assert replay is not None
    assert json.loads(replay)["error"] == "recipe_initialization_receipt_content_mismatch"


def test_terminal_page_replay_is_scoped_to_section(minimal_ctx, tmp_path) -> None:
    state = _stage(minimal_ctx, tmp_path)
    content_sha256 = _hash("terminal-content")
    receipt = recipe_initialization_receipt(
        state.initialization_id,
        state.artifact_generation,
        content_sha256=content_sha256,
    )
    rendered = json.dumps({"success": True, "completion_receipt": receipt})
    finalized = FinalizedRecipeSectionResponse(
        rendered=rendered,
        tool_ctx=minimal_ctx,
        initialization_id=state.initialization_id,
        artifact_generation=state.artifact_generation,
        section="flow_records",
        page_plan_sha256=_hash("flow-plan"),
        part=0,
        content_sha256=content_sha256,
        completion_receipt=receipt,
    )
    assert complete_section_response(finalized, rendered) == rendered

    assert (
        replay_terminal_section_response(
            minimal_ctx,
            initialization_id=state.initialization_id,
            section="step",
            part=0,
            content_sha256=content_sha256,
        )
        is None
    )


def test_completion_rejects_stale_or_altered_initialization_id(
    minimal_ctx,
    tmp_path,
) -> None:
    state = _stage(minimal_ctx, tmp_path)
    with minimal_ctx.recipe_execution_lock:
        minimal_ctx.recipe_initialization_state = record_initialization_page(
            state,
            initialization_id="initialization",
            section="flow_records",
            page_plan_sha256=_hash("flow-plan"),
            part=0,
        )

    assert json.loads(build_completion_response(minimal_ctx, "altered")) == {
        "success": False,
        "error": "recipe_initialization_incomplete",
    }


def test_completion_normalizes_unexpected_execution_preparation_failures(
    minimal_ctx,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _stage(minimal_ctx, tmp_path)
    with minimal_ctx.recipe_execution_lock:
        minimal_ctx.recipe_initialization_state = record_initialization_page(
            state,
            initialization_id="initialization",
            section="flow_records",
            page_plan_sha256=_hash("flow-plan"),
            part=0,
        )
    finalized = build_completion_response(minimal_ctx, "initialization")
    assert isinstance(finalized, FinalizedRecipeInitializationResponse)

    def _raise_prepare(*_args, **_kwargs):
        raise RuntimeError("resolver unavailable")

    monkeypatch.setattr(
        "autoskillit.server._recipe_initialization.prepare_recipe_execution",
        _raise_prepare,
    )

    response = complete_initialization_response(finalized, finalized.rendered)

    assert json.loads(response) == {
        "success": False,
        "error": "recipe_execution_install_failed",
    }
    assert isinstance(minimal_ctx.recipe_initialization_state, InitializingRecipe)


def test_common_admission_allows_only_recovery_inspection_and_lifecycle_control(
    minimal_ctx,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stage(minimal_ctx, tmp_path)
    monkeypatch.setattr(server_state, "_ctx", minimal_ctx)

    for tool_name in (
        "complete_recipe_initialization",
        "get_recipe_section",
        "kitchen_status",
        "open_kitchen",
        "close_kitchen",
    ):
        assert admit_registered_tool_during_initialization(tool_name) is None

    for tool_name in (
        "run_skill",
        "run_cmd",
        "test_check",
        "merge_worktree",
        "clone_repo",
        "push_to_remote",
        "reset_workspace",
    ):
        denial = json.loads(admit_registered_tool_during_initialization(tool_name) or "{}")
        assert denial["error"] == "recipe_initialization_incomplete"
        assert denial["initialization_id"] == "initialization"
