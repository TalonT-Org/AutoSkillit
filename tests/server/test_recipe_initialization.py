"""Server-owned recipe initialization completion transactions."""

from __future__ import annotations

import hashlib
import json

import pytest

import autoskillit.server._state as server_state
from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    InstalledRecipeExecution,
    RecipeArtifactGeneration,
    RecipeBindingProjection,
    RecipeFlowGeneration,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    ReadyRecipe,
    RecipeInitializationRequirement,
    record_initialization_page,
)
from autoskillit.server._recipe_execution import (
    DefaultAuditCycleHeadStore,
    DefaultInputPreflightResolver,
    build_recipe_execution_snapshot,
)
from autoskillit.server._recipe_initialization import (
    FinalizedRecipeInitializationResponse,
    admit_registered_tool_during_initialization,
    build_completion_response,
    complete_initialization_response,
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
    store = DefaultAuditCycleHeadStore()

    def factory(*, snapshot, allowed_root):
        return InstalledRecipeExecution(
            snapshot=snapshot,
            runtime_binding_digests={},
            audit_cycle_heads=store,
            input_preflight_resolver=DefaultInputPreflightResolver(
                allowed_root=allowed_root,
                head_store=store,
            ),
        )

    tool_ctx.project_dir = tmp_path
    tool_ctx.recipe_execution_factory = factory
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

    assert complete_initialization_response(finalized, "substituted") == "substituted"
    assert isinstance(minimal_ctx.recipe_initialization_state, InitializingRecipe)

    assert complete_initialization_response(finalized, finalized.rendered) == finalized.rendered
    assert isinstance(minimal_ctx.recipe_initialization_state, ReadyRecipe)

    replay = build_completion_response(minimal_ctx, "initialization")
    assert replay == finalized.rendered


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
