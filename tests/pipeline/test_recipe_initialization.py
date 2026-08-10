"""Focused contracts for the named-recipe initialization lifecycle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, cast

import pytest

from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    InstallationVersion,
    InstalledRecipeExecution,
    RecipeArtifactGeneration,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
    compute_recipe_execution_snapshot_digest,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    ReadyRecipe,
    RecipeInitializationRequirement,
    initialization_is_complete,
    record_initialization_page,
    replace_ready_execution,
    start_recipe_initialization,
    transition_recipe_ready,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _hash(seed: str) -> str:
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


def _snapshot(execution_id: str = "execution-a") -> RecipeExecutionSnapshot:
    recipe_name = "recipe"
    content_hash = _hash(f"{execution_id}:content")
    composite_hash = _hash(f"{execution_id}:composite")
    snapshot_digest = compute_recipe_execution_snapshot_digest(
        execution_id=execution_id,
        recipe_name=recipe_name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates={},
        dynamic_skill_step_names=frozenset(),
    )
    return RecipeExecutionSnapshot(
        execution_id=execution_id,
        recipe_name=recipe_name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates={},
        snapshot_digest=snapshot_digest,
        dynamic_skill_step_names=frozenset(),
    )


def _flow() -> RecipeFlowGeneration:
    return RecipeFlowGeneration(
        schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        records=(
            json.dumps(
                {"kind": "entrypoint", "name": "step"},
                separators=(",", ":"),
                sort_keys=True,
            ),
        ),
    )


def _artifact(flow: RecipeFlowGeneration) -> RecipeArtifactGeneration:
    return RecipeArtifactGeneration(
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


def _initializing() -> InitializingRecipe:
    flow = _flow()
    return start_recipe_initialization(
        kitchen_id="kitchen",
        recipe_name="recipe",
        artifact_generation=_artifact(flow),
        flow_generation=flow,
        initialization_id="initialization",
        staged_snapshot=_snapshot(),
        installation_version=InstallationVersion("installation-a"),
        requirements=(
            RecipeInitializationRequirement(
                section="flow_records",
                page_plan_sha256=_hash("flow-plan"),
                total_parts=2,
            ),
            RecipeInitializationRequirement(
                section="step",
                page_plan_sha256=_hash("step-plan"),
                total_parts=1,
            ),
        ),
        generation_store_key="compile-key",
    )


def test_start_initialization_creates_zero_progress_for_each_requirement() -> None:
    state = _initializing()

    assert tuple(item.next_part for item in state.progress) == (0, 0)
    assert initialization_is_complete(state) is False


def test_page_progress_is_in_order_and_exact_replay_is_idempotent() -> None:
    state = _initializing()
    first = record_initialization_page(
        state,
        initialization_id="initialization",
        section="flow_records",
        page_plan_sha256=_hash("flow-plan"),
        part=0,
    )

    assert first != state
    assert (
        record_initialization_page(
            first,
            initialization_id="initialization",
            section="flow_records",
            page_plan_sha256=_hash("flow-plan"),
            part=0,
        )
        is first
    )


def test_page_progress_rejects_a_later_section_before_prior_completion() -> None:
    with pytest.raises(ValueError, match="sections are out of order"):
        record_initialization_page(
            _initializing(),
            initialization_id="initialization",
            section="step",
            page_plan_sha256=_hash("step-plan"),
            part=0,
        )


@pytest.mark.parametrize(
    ("initialization_id", "section", "page_plan_sha256", "part"),
    [
        ("stale", "flow_records", _hash("flow-plan"), 0),
        ("initialization", "other", _hash("flow-plan"), 0),
        ("initialization", "flow_records", _hash("other-plan"), 0),
        ("initialization", "flow_records", _hash("flow-plan"), 1),
    ],
)
def test_page_progress_rejects_stale_changed_or_skipped_requests(
    initialization_id: str,
    section: str,
    page_plan_sha256: str,
    part: int,
) -> None:
    with pytest.raises(ValueError):
        record_initialization_page(
            _initializing(),
            initialization_id=initialization_id,
            section=section,
            page_plan_sha256=page_plan_sha256,
            part=part,
        )


def test_ready_requires_complete_coverage_and_the_staged_snapshot() -> None:
    state = _initializing()
    installed = InstalledRecipeExecution(
        snapshot=state.staged_snapshot,
        installation_version=state.installation_version,
        runtime_binding_digests={},
        audit_admission_ledger=cast(Any, object()),
        input_preflight_resolver=cast(Any, object()),
    )

    with pytest.raises(ValueError, match="incomplete"):
        transition_recipe_ready(
            state,
            installed_execution=installed,
            completion_receipt=_hash("receipt"),
        )

    for section, page_plan_sha256, parts in (
        ("flow_records", _hash("flow-plan"), range(2)),
        ("step", _hash("step-plan"), range(1)),
    ):
        for part in parts:
            state = cast(
                InitializingRecipe,
                record_initialization_page(
                    state,
                    initialization_id="initialization",
                    section=section,
                    page_plan_sha256=page_plan_sha256,
                    part=part,
                ),
            )

    mismatched = replace(installed, snapshot=_snapshot("execution-b"))
    with pytest.raises(ValueError, match="differs"):
        transition_recipe_ready(
            state,
            installed_execution=mismatched,
            completion_receipt=_hash("receipt"),
        )

    ready = transition_recipe_ready(
        state,
        installed_execution=installed,
        completion_receipt=_hash("receipt"),
    )
    assert ready.initialization_id == state.initialization_id
    assert ready.artifact_generation == state.artifact_generation
    assert ready.flow_generation == state.flow_generation


def test_ready_recipe_rejects_direct_construction_without_transition_authority() -> None:
    state = _initializing()
    installed = InstalledRecipeExecution(
        snapshot=state.staged_snapshot,
        installation_version=state.installation_version,
        runtime_binding_digests={},
        audit_admission_ledger=cast(Any, object()),
        input_preflight_resolver=cast(Any, object()),
    )

    with pytest.raises(ValueError, match="completed initialization transition"):
        ReadyRecipe(
            kitchen_id=state.kitchen_id,
            recipe_name=state.recipe_name,
            artifact_generation=state.artifact_generation,
            flow_generation=state.flow_generation,
            initialization_id=state.initialization_id,
            installed_execution=installed,
            generation_store_key=state.generation_store_key,
            completion_receipt=_hash("receipt"),
            _transition_token=object(),
        )


def test_ready_execution_replacement_rejects_cross_generation_without_mutation() -> None:
    state = _initializing()
    for requirement in state.requirements:
        for part in range(requirement.total_parts):
            state = cast(
                InitializingRecipe,
                record_initialization_page(
                    state,
                    initialization_id=state.initialization_id,
                    section=requirement.section,
                    page_plan_sha256=requirement.page_plan_sha256,
                    part=part,
                ),
            )
    installed = InstalledRecipeExecution(
        snapshot=state.staged_snapshot,
        installation_version=state.installation_version,
        runtime_binding_digests={},
        audit_admission_ledger=cast(Any, object()),
        input_preflight_resolver=cast(Any, object()),
    )
    ready = transition_recipe_ready(
        state,
        installed_execution=installed,
        completion_receipt=_hash("receipt"),
    )
    original = ready
    replacement = replace(installed, snapshot=_snapshot("execution-b"))

    with pytest.raises(ValueError, match="crosses generations"):
        replace_ready_execution(ready, replacement)

    assert ready == original
    assert ready.installed_execution is installed
