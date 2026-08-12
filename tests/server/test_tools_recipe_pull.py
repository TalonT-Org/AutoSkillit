"""Immutable recipe generations, finalization, and bounded pull contracts."""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import autoskillit.core.types._type_recipe_delivery as recipe_delivery_types
from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_DELIVERY_SURFACE_REGISTRY,
    RECIPE_FLOW_SCHEMA_VERSION,
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    FinalizedRecipeProjection,
    HostClientAttestation,
    RecipeArtifactGeneration,
    RecipeBindingProjection,
    RecipeDeliveryAttestation,
    RecipeDeliveryEvidenceDef,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    RecipeFlowGeneration,
    load_yaml,
    recipe_delivery_request_digest,
)
from autoskillit.execution import (
    CODEX_RECIPE_DELIVERY_BUDGET,
    ProtectedStoreAuthority,
    RecipeDeliveryReceiptLedger,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.pipeline import (
    InitializingRecipe,
    KitchenEffectPhase,
    KitchenRetryDisposition,
    RecipeInitializationRequirement,
    new_kitchen_open_state,
    start_kitchen_effect,
)
from autoskillit.recipe import load_and_validate
from autoskillit.server import _recipe_delivery as recipe_delivery
from autoskillit.server import _recipe_section_pagination as pagination
from autoskillit.server._recipe_delivery import (
    RECIPE_BODY_END,
    RECIPE_BODY_START,
    RECIPE_COMPLETION_SENTINEL,
    RecipeArtifactError,
    RecipeArtifactSchemaError,
    _canonical_payload,
    _generation_dir,
    _generation_from_payload,
    build_recipe_envelope,
    complete_finalized_recipe_response,
    finalize_recipe_delivery,
    load_recipe_artifact,
    persist_recipe_artifact,
    prepare_recipe_delivery_generation,
    recipe_pull_producers,
    recipe_recreation_producers,
    retire_recipe_artifacts,
)
from autoskillit.server._recipe_section_pagination import (
    PagePlanCache,
    RecipeSectionNonConvergenceError,
    RecipeSectionPaginationError,
    get_or_build_recipe_section_page_plan,
    resolve_recipe_section_bound_bytes,
    select_recipe_section,
)
from autoskillit.server._response_budget import enforce_response_budget
from autoskillit.server.recipe_section import _lifecycle as recipe_section_lifecycle
from autoskillit.server.tools.tools_recipe import get_recipe_section

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_NOW = 1_800_000_000


def _test_flow_generation(payload: dict[str, object]) -> RecipeFlowGeneration:
    existing = payload.get("flow_records")
    records = (
        tuple(record for record in existing if isinstance(record, str))
        if isinstance(existing, list)
        else ()
    )
    if not records:
        names = [
            name for name in payload.get("post_prune_step_names") or [] if isinstance(name, str)
        ]
        name = names[0] if names else "first"
        records = (
            json.dumps(
                {"kind": "entrypoint", "name": name},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            json.dumps(
                {"index": 0, "kind": "step", "name": name},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    return RecipeFlowGeneration(
        schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        records=records,
    )


def _test_projection() -> FinalizedRecipeProjection:
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection({}),
        ordered_step_names=("first",),
        entrypoint="first",
        ordered_flow_edges=(),
    )


def _with_flow(payload: dict[str, object]) -> tuple[dict[str, object], RecipeFlowGeneration]:
    result = dict(payload)
    flow_generation = _test_flow_generation(result)
    result.setdefault("flow_records", list(flow_generation.records))
    result.setdefault("recipe_flow", flow_generation.identity())
    return result, flow_generation


def _payload(
    content: str = "name: remediation\nsteps:\n  first:\n    action: stop\n",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "success": True,
        "valid": True,
        "content": content,
        "content_hash": "sha256:" + ("a" * 64),
        "composite_hash": "sha256:" + ("b" * 64),
        "post_prune_step_names": ["first"],
        "orchestration_rules": "follow the graph",
        "stop_step_semantics": "stop means stop",
        "ingredients_table": "| Ingredient | Required |\n|---|---|\n| task | yes |",
        "errors": [],
        "warnings": [],
    }
    return _with_flow(payload)[0]


def _finalize_recipe_delivery(
    payload: dict[str, object],
    *,
    surface: str,
    recipe_name: str,
    tool_ctx: Any,
    finalized_projection: FinalizedRecipeProjection,
    **kwargs: Any,
):
    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name=recipe_name,
        tool_ctx=tool_ctx,
        finalized_projection=finalized_projection,
    )
    return finalize_recipe_delivery(
        payload,
        surface=surface,
        recipe_name=recipe_name,
        tool_ctx=tool_ctx,
        finalized_projection=finalized_projection,
        flow_generation=prepared.flow_generation,
        canonical_artifact_payload=prepared.canonical_artifact_payload,
        execution_snapshot=prepared.execution_snapshot,
        normalized_compile_key=prepared.normalized_compile_key,
        **kwargs,
    )


def test_prepare_generation_rejects_non_finite_compile_values(tool_ctx) -> None:
    payload = _payload()
    payload["runtime_metadata"] = {"limit": float("nan")}

    with pytest.raises(ValueError, match="non-finite float"):
        prepare_recipe_delivery_generation(
            payload,
            recipe_name="remediation",
            tool_ctx=tool_ctx,
            finalized_projection=_test_projection(),
        )


def test_compile_identity_and_artifact_share_one_source_projection(tool_ctx) -> None:
    tool_ctx.kitchen_id = "compile-identity"
    payload = _payload()
    payload["custom_generation_input"] = {"value": 3}
    payload["initialization_id"] = "caller-owned-stale-id"

    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
    )
    source_payload = prepared.compile_inputs["source_payload"]

    assert isinstance(source_payload, dict)
    assert source_payload["custom_generation_input"] == {"value": 3}
    assert prepared.canonical_artifact_payload["custom_generation_input"] == {"value": 3}
    assert "initialization_id" not in source_payload
    assert "initialization_id" not in prepared.canonical_artifact_payload


def _persist_finalized_generation(
    tool_ctx: Any,
    payload: dict[str, object] | None = None,
):
    source = dict(payload or _payload())
    finalized = _finalize_recipe_delivery(
        source,
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
    )
    assert finalized.artifact_generation is not None
    assert finalized.execution_snapshot is not None
    return finalized.artifact_generation, finalized


def test_finalized_response_rejects_stale_kitchen_transition_owner(tool_ctx) -> None:
    tool_ctx.kitchen_id = "transition-owner"
    tool_ctx.kitchen_open_state = start_kitchen_effect(
        new_kitchen_open_state(
            kitchen_id=tool_ctx.kitchen_id,
            context_id="context-1",
            operation_id="operation-1",
        ),
        "recipe_serving",
    )
    finalized = _finalize_recipe_delivery(
        _payload(),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
    )
    assert finalized.kitchen_transition_token is not None

    tool_ctx.kitchen_open_state = start_kitchen_effect(
        new_kitchen_open_state(
            kitchen_id=tool_ctx.kitchen_id,
            context_id="context-1",
            operation_id="operation-2",
        ),
        "recipe_serving",
    )

    completed = complete_finalized_recipe_response(finalized, finalized.rendered)

    assert json.loads(completed) == {
        "success": False,
        "error": "kitchen_transition_ownership_mismatch",
    }


def test_finalized_response_confirms_owned_kitchen_serving_effect(tool_ctx) -> None:
    tool_ctx.kitchen_id = "transition-owner"
    tool_ctx.kitchen_open_state = start_kitchen_effect(
        new_kitchen_open_state(
            kitchen_id=tool_ctx.kitchen_id,
            context_id="context-1",
            operation_id="operation-1",
        ),
        "recipe_serving",
    )
    finalized = replace(
        _finalize_recipe_delivery(
            _payload(),
            surface="open_kitchen",
            recipe_name="remediation",
            tool_ctx=tool_ctx,
            finalized_projection=_test_projection(),
        ),
        initialization_activating=False,
    )
    assert finalized.kitchen_transition_token is not None

    completed = complete_finalized_recipe_response(finalized, finalized.rendered)

    assert completed == finalized.rendered
    serving = next(
        effect for effect in tool_ctx.kitchen_open_state.effects if effect.name == "recipe_serving"
    )
    assert serving.phase is KitchenEffectPhase.CONFIRMED
    assert serving.receipt == f"response:{serving.effect_id}"


def test_finalized_response_marks_changed_kitchen_serving_effect_ambiguous(
    tool_ctx,
) -> None:
    tool_ctx.kitchen_id = "transition-owner"
    tool_ctx.kitchen_open_state = start_kitchen_effect(
        new_kitchen_open_state(
            kitchen_id=tool_ctx.kitchen_id,
            context_id="context-1",
            operation_id="operation-1",
        ),
        "recipe_serving",
    )
    finalized = replace(
        _finalize_recipe_delivery(
            _payload(),
            surface="open_kitchen",
            recipe_name="remediation",
            tool_ctx=tool_ctx,
            finalized_projection=_test_projection(),
        ),
        initialization_activating=False,
    )

    completed = complete_finalized_recipe_response(
        finalized,
        "bounded replacement",
    )

    assert completed == "bounded replacement"
    serving = next(
        effect for effect in tool_ctx.kitchen_open_state.effects if effect.name == "recipe_serving"
    )
    assert serving.phase is KitchenEffectPhase.AMBIGUOUS
    assert tool_ctx.kitchen_open_state.retry_disposition is (
        KitchenRetryDisposition.RECONCILE_REQUIRED
    )


def _persist(
    tmp_path: Path,
    payload: dict[str, object] | None = None,
    *,
    producer: str = "open_kitchen",
    kitchen_id: str = "kitchen-test",
) -> RecipeArtifactGeneration:
    persisted_payload, flow_generation = _with_flow(dict(payload or _payload()))
    return persist_recipe_artifact(
        tmp_path,
        kitchen_id=kitchen_id,
        producer_tool=producer,
        recipe_name="remediation",
        payload=persisted_payload,
        flow_generation=flow_generation,
    )


def _write_malformed_generation(
    tmp_path: Path,
    payload: dict[str, object],
    *,
    kitchen_id: str = "kitchen-test",
    producer: str = "open_kitchen",
) -> RecipeArtifactGeneration:
    """Write a digest-consistent artifact while bypassing producer validation."""
    blob = _canonical_payload(payload)
    flow_generation = _test_flow_generation(payload)
    generation = _generation_from_payload(
        producer_tool=producer,
        recipe_name="remediation",
        blob=blob,
        payload=payload,
        flow_generation=flow_generation,
    )
    directory = _generation_dir(
        tmp_path,
        kitchen_id=kitchen_id,
        producer_tool=producer,
        recipe_name="remediation",
        descriptor_version=generation.descriptor_version,
        schema_version=generation.schema_version,
        payload_sha256=generation.payload_sha256,
    )
    directory.mkdir(parents=True)
    (directory / "payload.json").write_bytes(blob)
    (directory / "descriptor.json").write_bytes(_canonical_payload(generation.pull_identity()))
    return generation


def test_pull_fixture_matches_load_and_validate_section_shapes(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "remediation.yaml").write_text(
        """\
name: remediation
description: Fixture recipe
summary: Fixture recipe
kitchen_rules:
  - Follow routing rules
ingredients:
  task:
    description: Work to perform
    required: true
steps:
  first:
    action: stop
    message: Done. Emit the L3 result sentinel JSON block now.
""",
        encoding="utf-8",
    )

    producer = dict(load_and_validate("remediation", project_dir=tmp_path))
    if producer.get("warnings") is None:
        producer["warnings"] = []
    fixture = _payload(str(producer["content"]))

    assert producer["valid"] is True
    assert producer["post_prune_step_names"] == fixture["post_prune_step_names"]
    for section in (
        "content",
        "ingredients_table",
        "orchestration_rules",
        "stop_step_semantics",
        "errors",
        "warnings",
    ):
        assert type(fixture[section]) is type(producer[section]), section


@pytest.mark.parametrize(
    ("field", "malformed"),
    [
        ("content", []),
        ("ingredients_table", {"task": {"required": True}}),
        ("orchestration_rules", []),
        ("stop_step_semantics", 1),
        ("errors", ["valid", 1]),
        ("warnings", "warning"),
        ("post_prune_step_names", ["first", 1]),
    ],
)
def test_persistence_rejects_malformed_pullable_sections(
    tmp_path: Path, field: str, malformed: object
) -> None:
    payload = _payload()
    payload[field] = malformed

    with pytest.raises(RecipeArtifactSchemaError):
        _persist(tmp_path, payload)


def test_persistence_rejects_missing_required_content(tmp_path: Path) -> None:
    payload = _payload()
    payload.pop("content")

    with pytest.raises(RecipeArtifactSchemaError, match="missing_required_section@content"):
        _persist(tmp_path, payload)


@pytest.mark.parametrize(
    "field",
    ["content", "orchestration_rules", "stop_step_semantics", "errors", "warnings"],
)
def test_persistence_rejects_null_for_sections_with_invalid_null_behavior(
    tmp_path: Path,
    field: str,
) -> None:
    payload = _payload()
    payload[field] = None

    with pytest.raises(RecipeArtifactSchemaError, match=f"invalid_section_type@{field}"):
        _persist(tmp_path, payload)


def test_schema_mismatch_diagnostic_bounds_reported_findings(tmp_path: Path) -> None:
    payload = _payload()
    payload["warnings"] = list(range(130))

    with pytest.raises(RecipeArtifactSchemaError) as exc_info:
        _persist(tmp_path, payload)

    detail = str(exc_info.value)
    assert detail.count("invalid_section_element_type@warnings.") == 100
    assert detail.endswith("30 additional findings omitted")


def test_load_rejects_digest_consistent_malformed_artifact(tmp_path: Path) -> None:
    payload = _payload()
    payload["errors"] = ["valid", 1]
    generation = _write_malformed_generation(tmp_path, payload)

    with pytest.raises(RecipeArtifactSchemaError):
        load_recipe_artifact(
            tmp_path,
            kitchen_id="kitchen-test",
            identity=generation,
        )


def test_artifact_namespace_encodes_colliding_kitchen_ids_injectively(tmp_path: Path) -> None:
    first = persist_recipe_artifact(
        tmp_path,
        kitchen_id="a/b",
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    second = persist_recipe_artifact(
        tmp_path,
        kitchen_id="a?b",
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )

    assert retire_recipe_artifacts(tmp_path, kitchen_id="a/b") is True
    with pytest.raises(RecipeArtifactError):
        load_recipe_artifact(tmp_path, kitchen_id="a/b", identity=first)
    assert load_recipe_artifact(tmp_path, kitchen_id="a?b", identity=second) == _payload()


def test_artifact_persistence_rejects_blob_above_read_ceiling(tmp_path: Path) -> None:
    oversized = _payload("x" * (RECIPE_ARTIFACT_MAX_BLOB_BYTES + 1))

    with pytest.raises(RecipeArtifactError, match="exceeds persistence limit"):
        _persist(tmp_path, oversized)


def test_shared_producer_surfaces_require_identical_pull_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conflicting = dict(RECIPE_DELIVERY_SURFACE_REGISTRY)
    conflicting["conflicting_open_kitchen"] = conflicting["open_kitchen"]._replace(
        pull_eligible=False
    )
    monkeypatch.setattr(
        "autoskillit.server._recipe_delivery.RECIPE_DELIVERY_SURFACE_REGISTRY",
        conflicting,
    )

    with pytest.raises(RecipeArtifactError, match="must share pull policies"):
        recipe_pull_producers()
    with pytest.raises(RecipeArtifactError, match="must share pull policies"):
        recipe_recreation_producers()


def _remove_persisted_namespace(temp_dir: Path, *, kitchen_id: str) -> None:
    shutil.rmtree(temp_dir / "recipe-delivery" / kitchen_id)


def _build_envelope(
    tmp_path: Path, payload: dict[str, object], *, bound_bytes: int
) -> tuple[dict[str, object], RecipeArtifactGeneration]:
    generation = _persist(tmp_path, payload)
    flow_generation = _test_flow_generation(payload)
    envelope = build_recipe_envelope(
        generation=generation,
        flow_generation=flow_generation,
        bound_bytes=bound_bytes,
    )
    return envelope, generation


def test_recovery_order_is_derived_from_initialization_requirements(
    tmp_path: Path,
) -> None:
    payload = _payload()
    generation = _persist(tmp_path, payload)
    flow_generation = _test_flow_generation(payload)
    requirements = (
        RecipeInitializationRequirement(
            section="first",
            page_plan_sha256="sha256:first-plan",
            total_parts=2,
        ),
        RecipeInitializationRequirement(
            section="flow_records",
            page_plan_sha256="sha256:flow-plan",
            total_parts=1,
        ),
    )

    envelope = build_recipe_envelope(
        generation=generation,
        flow_generation=flow_generation,
        bound_bytes=90_000,
        initialization_requirements=requirements,
    )

    assert [item["section"] for item in envelope["required_sections"]] == [
        "first",
        "flow_records",
    ]
    assert envelope["recovery"]["ordered_sections"] == ["first", "flow_records"]


def test_same_payload_is_idempotent_and_changed_payload_is_immutable(tmp_path: Path) -> None:
    first = _persist(tmp_path)
    same = _persist(tmp_path)
    changed = _persist(tmp_path, _payload("name: remediation\nsteps: {}\n"))

    assert same == first
    assert changed.payload_sha256 != first.payload_sha256
    assert load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=first) == _payload()
    assert load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=changed) == _payload(
        "name: remediation\nsteps: {}\n"
    )


@pytest.mark.parametrize(
    "version_name",
    ["RECIPE_ARTIFACT_DESCRIPTOR_VERSION", "RECIPE_ARTIFACT_SCHEMA_VERSION"],
)
def test_generation_path_includes_version_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version_name: str
) -> None:
    first = _persist(tmp_path)
    current_version = getattr(recipe_delivery, version_name)
    monkeypatch.setattr(
        f"autoskillit.server._recipe_delivery.{version_name}",
        current_version + 1,
    )
    monkeypatch.setattr(recipe_delivery_types, version_name, current_version + 1)

    second = _persist(tmp_path)

    assert second.payload_sha256 == first.payload_sha256
    assert second.pull_identity() != first.pull_identity()
    with pytest.raises(RecipeArtifactError, match="invalid recipe artifact identity bounds"):
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=first)
    assert load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=second) == _payload()


def test_concurrent_writers_publish_one_exact_generation(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        generations = list(pool.map(lambda _: _persist(tmp_path), range(24)))
    assert generations == [generations[0]] * len(generations)
    assert (
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=generations[0])
        == _payload()
    )


@pytest.mark.parametrize(
    ("filename", "error"),
    [
        ("payload.json", "content-addressed payload collision"),
        ("descriptor.json", "content-addressed descriptor collision"),
    ],
)
def test_persistence_collision_checks_use_bounded_descriptor_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    error: str,
) -> None:
    _persist(tmp_path)
    target = next((tmp_path / "recipe-delivery").rglob(filename))
    target.write_bytes(target.read_bytes() + b"x")

    def _unbounded_read_forbidden(*_args, **_kwargs):
        raise AssertionError("unbounded pathlib read used during collision check")

    monkeypatch.setattr(Path, "read_bytes", _unbounded_read_forbidden)
    monkeypatch.setattr(Path, "read_text", _unbounded_read_forbidden)

    with pytest.raises(RecipeArtifactError, match=error):
        _persist(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("producer_tool", "invalid"),
        ("payload_sha256", "sha256:" + ("0" * 64)),
        ("artifact_blob_sha256", "sha256:" + ("1" * 64)),
        ("artifact_blob_size_bytes", 999_999),
        ("body_sha256", "sha256:" + ("2" * 64)),
        ("body_size_bytes", 1),
    ],
)
def test_generation_identity_domains_are_independently_verified(
    tmp_path: Path, field: str, value: str | int
) -> None:
    generation = _persist(tmp_path)
    with pytest.raises(RecipeArtifactError):
        load_recipe_artifact(
            tmp_path,
            kitchen_id="kitchen-test",
            identity=replace(generation, **{field: value}),
        )


def test_generation_descriptor_read_has_server_owned_ceiling(tmp_path: Path) -> None:
    generation = _persist(tmp_path)
    descriptor_path = next((tmp_path / "recipe-delivery").rglob("descriptor.json"))
    descriptor_path.write_bytes(b"x" * 20_000)

    with pytest.raises(RecipeArtifactError, match="descriptor exceeds read limit"):
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=generation)


def test_non_utf8_payload_is_normalized_to_recipe_artifact_error(tmp_path: Path) -> None:
    blob = b"\xff"
    qualified_blob_sha = f"sha256:{hashlib.sha256(blob).hexdigest()}"
    payload_sha = "sha256:" + hashlib.sha256(b"autoskillit.recipe-payload.v1\0" + blob).hexdigest()
    empty_body_sha = f"sha256:{hashlib.sha256(b'').hexdigest()}"
    flow_generation = _test_flow_generation(_payload())
    generation = RecipeArtifactGeneration(
        producer_tool="open_kitchen",
        recipe_name="remediation",
        descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
        schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
        payload_sha256=payload_sha,
        artifact_blob_sha256=qualified_blob_sha,
        artifact_blob_size_bytes=len(blob),
        body_sha256=empty_body_sha,
        body_size_bytes=0,
        flow_schema_version=flow_generation.schema_version,
        flow_sha256=flow_generation.flow_sha256,
        flow_size_bytes=flow_generation.flow_size_bytes,
        flow_record_count=flow_generation.record_count,
    )
    directory = _generation_dir(
        tmp_path,
        kitchen_id="kitchen-test",
        producer_tool=generation.producer_tool,
        recipe_name=generation.recipe_name,
        descriptor_version=generation.descriptor_version,
        schema_version=generation.schema_version,
        payload_sha256=generation.payload_sha256,
    )
    directory.mkdir(parents=True)
    (directory / "payload.json").write_bytes(blob)
    (directory / "descriptor.json").write_text(
        json.dumps(generation.pull_identity(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(RecipeArtifactError, match="not valid JSON"):
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=generation)


def test_generation_descriptor_has_no_caller_selected_path(tmp_path: Path) -> None:
    pull = _persist(tmp_path).pull_identity()
    assert set(pull) == {
        "producer_tool",
        "recipe_name",
        "descriptor_version",
        "schema_version",
        "payload_sha256",
        "artifact_blob_sha256",
        "artifact_blob_size_bytes",
        "body_sha256",
        "body_size_bytes",
        "flow_schema_version",
        "flow_sha256",
        "flow_size_bytes",
        "flow_record_count",
        "pull_tool",
    }
    assert not {"artifact_path", "path", "sha256"} & set(pull)
    assert recipe_pull_producers() == {"open_kitchen", "load_recipe", "get_recipe"}
    assert recipe_recreation_producers() == {"open_kitchen", "get_recipe"}


def test_kitchen_retirement_removes_only_that_namespace(tmp_path: Path) -> None:
    first = _persist(tmp_path)
    second = persist_recipe_artifact(
        tmp_path,
        kitchen_id="other-kitchen",
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    assert retire_recipe_artifacts(tmp_path, kitchen_id="kitchen-test") is True
    with pytest.raises(RecipeArtifactError):
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=first)
    with pytest.raises(RecipeArtifactError, match="namespace is retired"):
        _persist(tmp_path)
    assert (
        load_recipe_artifact(tmp_path, kitchen_id="other-kitchen", identity=second) == _payload()
    )


def test_kitchen_retirement_notifies_callbacks_after_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notified: list[str] = []

    def failing_callback(_kitchen_id: str) -> None:
        raise RuntimeError("cleanup failed")

    def succeeding_callback(kitchen_id: str) -> None:
        notified.append(kitchen_id)

    monkeypatch.setattr(
        recipe_section_lifecycle,
        "_KITCHEN_RETIREMENT_CALLBACKS",
        (failing_callback, succeeding_callback),
    )

    recipe_section_lifecycle.notify_kitchen_retired("kitchen-test")

    assert notified == ["kitchen-test"]


@pytest.mark.parametrize("kwargs", [{"max_entries": -1}, {"max_bytes": -1}])
def test_page_plan_cache_rejects_negative_capacity_limits(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        PagePlanCache(**kwargs)


def test_kitchen_retirement_evicts_only_matching_page_plans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache = PagePlanCache()
    monkeypatch.setattr(pagination, "_PAGE_PLAN_CACHE", cache)
    generation = _persist(tmp_path)
    selected = select_recipe_section(_payload("cached content"), "content")
    common = {
        "generation": generation,
        "selected": selected,
        "recipe_section_bound_bytes": 10_000,
    }
    retired = get_or_build_recipe_section_page_plan(
        kitchen_id="kitchen-test",
        **common,
    )
    retained = get_or_build_recipe_section_page_plan(
        kitchen_id="other-kitchen",
        **common,
    )

    assert retire_recipe_artifacts(tmp_path, kitchen_id="kitchen-test") is True

    assert get_or_build_recipe_section_page_plan(kitchen_id="other-kitchen", **common) is retained
    assert (
        get_or_build_recipe_section_page_plan(kitchen_id="kitchen-test", **common) is not retired
    )


def test_cold_kitchen_retirement_does_not_create_page_plan_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(pagination, "_PAGE_PLAN_CACHE", None)

    assert retire_recipe_artifacts(tmp_path, kitchen_id="cold-kitchen") is True
    assert pagination._PAGE_PLAN_CACHE is None


def test_kitchen_retirement_evicts_after_generation_lock_exits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_active = False
    original_lock = recipe_delivery._generation_lock

    @contextmanager
    def _tracked_lock(temp_dir: Path, *, exclusive: bool):
        nonlocal lock_active
        with original_lock(temp_dir, exclusive=exclusive):
            lock_active = True
            try:
                yield
            finally:
                lock_active = False

    evicted: list[str] = []

    def _evict(kitchen_id: str) -> None:
        assert lock_active is False
        evicted.append(kitchen_id)

    monkeypatch.setattr(recipe_delivery, "_generation_lock", _tracked_lock)
    monkeypatch.setattr(pagination, "evict_kitchen", _evict)

    assert retire_recipe_artifacts(tmp_path, kitchen_id="lock-kitchen") is True
    assert evicted == ["lock-kitchen"]


def test_kitchen_retirement_eviction_is_success_only_and_nonthrowing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evict = MagicMock(side_effect=RuntimeError("cache unavailable"))
    monkeypatch.setattr(pagination, "evict_kitchen", evict)

    assert retire_recipe_artifacts(tmp_path, kitchen_id="successful-kitchen") is True
    evict.assert_called_once_with("successful-kitchen")

    evict.reset_mock()
    monkeypatch.setattr(
        recipe_delivery,
        "atomic_write",
        MagicMock(side_effect=OSError("retirement failed")),
    )
    assert retire_recipe_artifacts(tmp_path, kitchen_id="failed-kitchen") is False
    evict.assert_not_called()


@pytest.mark.parametrize("kitchen_id", [".", ".."])
def test_kitchen_retirement_rejects_dot_path_components(tmp_path: Path, kitchen_id: str) -> None:
    sentinel = tmp_path / "unrelated-temp-data"
    sentinel.write_text("preserve me", encoding="utf-8")

    assert retire_recipe_artifacts(tmp_path, kitchen_id=kitchen_id) is False
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_codex_without_supported_host_evidence_uses_bounded_envelope(tool_ctx) -> None:
    tool_ctx.backend = CodexBackend()
    tool_ctx.kitchen_id = "codex-envelope"
    payload = _payload(
        "name: remediation\nsteps:\n  first:\n    action: stop\n    message: "
        + ("x" * 50_000)
        + "\n"
    )

    finalized = _finalize_recipe_delivery(
        payload,
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    envelope = json.loads(finalized.rendered)
    assert "content" not in envelope
    assert envelope["recipe_pull"]["payload_sha256"].startswith("sha256:")
    assert len(finalized.rendered.encode("utf-8")) <= 40_000


def test_token_dense_payload_does_not_use_four_byte_ordinary_estimate(tool_ctx) -> None:
    tool_ctx.backend = CodexBackend()
    tool_ctx.kitchen_id = "codex-token-dense"

    finalized = _finalize_recipe_delivery(
        _payload("!" * 20_000),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert len(finalized.rendered.encode("utf-8")) <= (
        CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit
    )


def test_initialization_requirements_use_the_pull_response_bound(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_max_bytes = RECIPE_SECTION_RESPONSE_FLOOR_BYTES + 500
    tool_ctx.backend = CodexBackend()
    tool_ctx.kitchen_id = "initialization-page-bound"
    tool_ctx.config.output_budget = OutputBudgetConfig(
        response_max_bytes=response_max_bytes,
        page_max_bytes=None,
    )
    captured_bounds: list[int] = []

    def _capture_requirements(
        **kwargs: Any,
    ) -> tuple[RecipeInitializationRequirement, ...]:
        captured_bounds.append(kwargs["bound_bytes"])
        return ()

    monkeypatch.setattr(
        recipe_delivery,
        "_initialization_requirements",
        _capture_requirements,
    )

    finalized = _finalize_recipe_delivery(
        _payload("!" * 20_000),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert captured_bounds == [
        resolve_recipe_section_bound_bytes(
            response_max_bytes,
            CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit,
            exemption_ceiling_bytes=RESPONSE_BACKSTOP_EXEMPTION_REGISTRY[
                "open_kitchen"
            ].max_utf8_bytes,
        )
    ]


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("stale_initialization_id", "invalid_recipe_initialization_identity"),
        ("altered_page_plan", "invalid_recipe_page_plan_identity"),
        ("wrong_continuation", "invalid_recipe_section_continuation"),
    ],
)
async def test_initialization_pull_rejections_preserve_progress(
    tool_ctx_kitchen_open,
    case: str,
    expected_error: str,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = f"initialization-rejection-{case}"
    tool_ctx_kitchen_open.config.output_budget = OutputBudgetConfig(
        response_max_bytes=8_000,
        page_max_bytes=195_000,
    )
    payload = _payload(
        "name: remediation\nsteps:\n  first:\n    action: stop\n    message: "
        + ("x" * 20_000)
        + "\n"
    )
    finalized = _finalize_recipe_delivery(
        payload,
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx_kitchen_open,
        finalized_projection=_test_projection(),
    )
    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert complete_finalized_recipe_response(finalized, finalized.rendered) == (
        finalized.rendered
    )
    envelope = json.loads(finalized.rendered)
    requirement = envelope["required_sections"][0]
    identity = dict(envelope["recipe_pull"])
    identity.pop("pull_tool")
    initialization_id = envelope["initialization_id"]
    page_plan_sha256 = requirement["page_plan_sha256"]
    part = 0
    continuation = None
    if case == "stale_initialization_id":
        initialization_id = "stale-initialization"
        page_plan_sha256 = None
    elif case == "altered_page_plan":
        page_plan_sha256 = "sha256:" + ("0" * 64)
    elif case == "wrong_continuation":
        continuation = "invalid-continuation"

    before = tool_ctx_kitchen_open.recipe_initialization_state
    assert isinstance(before, InitializingRecipe)
    response = json.loads(
        await get_recipe_section(
            section=requirement["section"],
            part=part,
            initialization_id=initialization_id,
            page_plan_sha256=page_plan_sha256,
            continuation=continuation,
            **identity,
        )
    )

    assert response["error"] == expected_error
    assert tool_ctx_kitchen_open.recipe_initialization_state == before


def test_envelope_manifest_ignores_ambient_multibyte_fields(tmp_path: Path) -> None:
    payload = _payload()
    payload["orchestration_rules"] = "雪" * 1_000
    payload["stop_step_semantics"] = "界" * 1_000
    bound_bytes = 4_000

    envelope, _generation = _build_envelope(tmp_path, payload, bound_bytes=bound_bytes)
    rendered = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))

    assert len(rendered.encode("utf-8")) <= bound_bytes
    assert envelope["success"] is True
    assert envelope["recipe_flow"] == _test_flow_generation(payload).identity()
    assert "orchestration_rules" not in envelope
    assert "stop_step_semantics" not in envelope


def test_envelope_fallbacks_follow_tight_and_extreme_bounds(tmp_path: Path) -> None:
    payload = _payload()
    payload["post_prune_step_names"] = [f"step-{index:03d}" for index in range(100)]
    generation = _persist(tmp_path, payload)
    pull_fallback = {
        "success": False,
        "error": "recipe_envelope_exceeds_delivery_bound",
        "recipe_pull": generation.pull_identity(),
    }
    error_fallback = {
        "success": False,
        "error": "recipe_envelope_exceeds_delivery_bound",
    }

    pull_bound = len(
        json.dumps(pull_fallback, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    error_bound = len(
        json.dumps(error_fallback, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )

    assert _build_envelope(tmp_path, payload, bound_bytes=pull_bound)[0] == pull_fallback
    assert _build_envelope(tmp_path, payload, bound_bytes=error_bound)[0] == error_fallback
    assert _build_envelope(tmp_path, payload, bound_bytes=2)[0] == {}
    with pytest.raises(ValueError, match="too small for a JSON object"):
        _build_envelope(tmp_path, payload, bound_bytes=1)


def test_finalizer_uses_backend_selected_recipe_budget(tool_ctx) -> None:
    selected_budget = CODEX_RECIPE_DELIVERY_BUDGET._replace(contract_digest="sha256:" + ("d" * 64))
    backend = MagicMock()
    backend.capabilities = replace(
        CodexBackend().capabilities,
        recipe_delivery_budget=selected_budget,
    )
    tool_ctx.backend = backend
    tool_ctx.kitchen_id = "selected-budget"

    finalized = _finalize_recipe_delivery(
        _payload(),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
    assert finalized.decision.contract_digest == selected_budget.contract_digest


_ATTESTED_HOST_CLIENT = HostClientAttestation(
    attested_client_gate_tokens=50_000,
    annotation_support=True,
)


def test_annotation_aware_inline_for_exempt_surface_within_ceiling(tool_ctx) -> None:
    """When an attested Claude host client claims annotation support and an exempt
    surface's ordinary-rendered payload exceeds the backend's ordinary token limit
    but fits within the registered exemption ceiling, finalize_recipe_delivery()
    must resolve to ORDINARY_INLINE so the full recipe body survives (superseding
    the removed Issue #4399 ad-hoc override — this is now handled by the
    annotation-aware branch in resolve_recipe_delivery_decision).

    Claude Code backend: recipe_delivery_budget=None → decision resolver is
    eligible for the annotation-aware branch when attestation is present.
    """
    tool_ctx.backend = ClaudeCodeBackend()
    tool_ctx.kitchen_id = "claude-code-exemption"

    # The effective unannotated limit rises to the attested gate × headroom
    # when attestation is valid. The payload must exceed THAT to exercise the
    # annotation-aware branch.
    from autoskillit.core import (
        CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
        CONSERVATIVE_GATE_HEADROOM_DENOMINATOR,
        CONSERVATIVE_GATE_HEADROOM_NUMERATOR,
    )

    effective_unannotated = (
        CLAUDE_INJECTED_CLIENT_RESULT_TOKENS
        * CONSERVATIVE_GATE_HEADROOM_NUMERATOR
        // CONSERVATIVE_GATE_HEADROOM_DENOMINATOR
    )
    ceiling = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"].max_utf8_bytes
    # Payload whose ordinary JSON exceeds the effective attested unannotated
    # limit (46,500) but stays under the char-margin exemption ceiling.
    oversized_content = "x" * min(ceiling * 9 // 10 - 1_000, effective_unannotated + 5_000)
    assert len(oversized_content.encode("utf-8")) > effective_unannotated
    assert len(oversized_content.encode("utf-8")) <= ceiling * 9 // 10

    finalized = _finalize_recipe_delivery(
        _payload(oversized_content),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        host_client_attestation=_ATTESTED_HOST_CLIENT,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
    assert finalized.decision.reason == "annotation_aware_inline"
    # Recipe content must be present in the rendered string (not stripped by ENVELOPE).
    assert oversized_content in finalized.rendered


def test_annotation_aware_inline_falls_through_without_attestation(tool_ctx) -> None:
    """Without a host client attestation, the same payload that would qualify for
    the annotation-aware branch must remain ENVELOPE — never trust an unattested
    per-call claim."""
    tool_ctx.backend = ClaudeCodeBackend()
    tool_ctx.kitchen_id = "claude-code-exemption-unattested"

    ordinary_limit = ClaudeCodeBackend().capabilities.unnegotiated_tool_result_token_limit
    ceiling = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"].max_utf8_bytes
    oversized_content = "x" * min(ceiling * 9 // 10 - 1_000, ordinary_limit + 5_000)

    finalized = _finalize_recipe_delivery(
        _payload(oversized_content),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        host_client_attestation=None,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert finalized.decision.reason != "annotation_aware_inline"


def test_exemption_override_retains_envelope_for_payload_above_ceiling(tool_ctx) -> None:
    """Boundary: payloads exceeding the 195KB exemption ceiling must remain
    ENVELOPE even with attestation present — the annotation-aware branch only
    applies when the ordinary-rendered payload fits within the registered
    exemption.
    """
    tool_ctx.backend = ClaudeCodeBackend()
    tool_ctx.kitchen_id = "claude-code-over-ceiling"

    # Payload whose ordinary JSON exceeds the 195,000-byte exemption ceiling.
    oversized_content = "y" * (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"].max_utf8_bytes + 1_000
    )

    finalized = _finalize_recipe_delivery(
        _payload(oversized_content),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        host_client_attestation=_ATTESTED_HOST_CLIENT,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert finalized.decision.reason != "annotation_aware_inline"


def test_exemption_override_requires_char_ceiling_too(tool_ctx) -> None:
    """Issue #4557 Stage C: the annotation-aware branch must ALSO respect the
    client-measured serialized-char ceiling, not just the byte ceiling. The
    server budgets in compiled UTF-8 bytes, but the client gates on
    JSON-serialized chars — a payload already embedded as an escaped JSON
    string field doubles again in char count (but not in byte count) when the
    client's outer transport re-serializes it. Backslash-dense content can
    therefore stay comfortably under the byte margin while its
    client-serialized char length blows past the char ceiling — the branch
    must not fire for such a payload.
    """
    tool_ctx.backend = ClaudeCodeBackend()
    tool_ctx.kitchen_id = "claude-code-char-ceiling"

    exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"]
    byte_margin = exemption.max_utf8_bytes * 9 // 10
    # n backslashes cost 2n chars once embedded as an ordinary JSON string
    # field (each backslash escapes to `\\`), but 4n chars once the client
    # re-serializes that already-escaped payload as an outer JSON string.
    oversized_content = "\\" * 50_000
    embedded_length = len(json.dumps(oversized_content))
    client_length = len(json.dumps(json.dumps(oversized_content)))
    assert embedded_length < byte_margin
    assert client_length > exemption.max_chars

    finalized = _finalize_recipe_delivery(
        _payload(oversized_content),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        host_client_attestation=_ATTESTED_HOST_CLIENT,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert finalized.decision.reason != "annotation_aware_inline"


def test_exemption_override_does_not_apply_to_non_exempt_surface(tool_ctx) -> None:
    """Boundary: get_recipe has no response_exemption_tool registration, so the
    annotation-aware branch must not apply — ENVELOPE remains the result even
    with attestation present.
    """
    tool_ctx.backend = ClaudeCodeBackend()
    tool_ctx.kitchen_id = "claude-code-get-recipe"

    ordinary_limit = ClaudeCodeBackend().capabilities.unnegotiated_tool_result_token_limit
    oversized_content = "z" * (ordinary_limit * 4 + 5_000)

    finalized = _finalize_recipe_delivery(
        _payload(oversized_content),
        surface="get_recipe",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        host_client_attestation=_ATTESTED_HOST_CLIENT,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert finalized.decision.reason != "annotation_aware_inline"


def test_annotation_aware_inline_not_available_to_protected_backend(tool_ctx) -> None:
    """A backend with its own recipe_delivery_budget (Codex) must never resolve
    via the annotation-aware branch, even when handed a host client attestation
    claiming annotation support — that attestation vector is Claude-only and
    Codex must resolve exclusively through its receipt-based protected
    delivery pipeline.
    """
    tool_ctx.backend = CodexBackend()
    tool_ctx.kitchen_id = "codex-annotation-aware-rejected"

    ordinary_limit = CodexBackend().capabilities.unnegotiated_tool_result_token_limit
    ceiling = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"].max_utf8_bytes
    oversized_content = "x" * min(ceiling * 9 // 10 - 1_000, ordinary_limit + 5_000)

    finalized = _finalize_recipe_delivery(
        _payload(oversized_content),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        host_client_attestation=_ATTESTED_HOST_CLIENT,
    )

    assert finalized.decision.reason != "annotation_aware_inline"
    assert finalized.decision.mode is not RecipeDeliveryMode.ORDINARY_INLINE


def _request() -> RecipeDeliveryRequest:
    budget = CODEX_RECIPE_DELIVERY_BUDGET
    return RecipeDeliveryRequest(
        audience="autoskillit.recipe-delivery",
        delivery_call_id="delivery-finalizer-001",
        contract_version=budget.contract_version,
        contract_digest=budget.contract_digest,
        caller_requested_outer_tokens=(budget.authoritative_attested_recipe_result_token_limit),
        code_digest="sha256:" + ("b" * 64),
    )


def _evidence() -> RecipeDeliveryEvidenceDef:
    budget = CODEX_RECIPE_DELIVERY_BUDGET
    return RecipeDeliveryEvidenceDef(
        identity="protected-finalizer-test-v1",
        host_channel="test-process-isolated-host",
        evidence_schema_version=budget.evidence_version,
        parser_version=budget.parser_version,
        cli_identity="codex-test-cli",
        selected_limit_derivation="protected-resolved-outer-limit",
        selected_result_token_limit=(budget.authoritative_attested_recipe_result_token_limit),
        contract_digest=budget.contract_digest,
    )


def _attestation(thread_id: str = "thread-finalizer") -> RecipeDeliveryAttestation:
    request = _request()
    budget = CODEX_RECIPE_DELIVERY_BUDGET
    return RecipeDeliveryAttestation(
        audience=request.audience,
        thread_id=thread_id,
        turn_id="turn-finalizer-001",
        outer_call_id="outer-finalizer-001",
        code_mode_cell_id="cell-finalizer-001",
        delivery_call_id=request.delivery_call_id,
        host_observed_requested_outer_tokens=request.caller_requested_outer_tokens,
        selected_result_token_limit=(budget.authoritative_attested_recipe_result_token_limit),
        code_digest=request.code_digest,
        request_digest=recipe_delivery_request_digest(request),
        nonce="nonce-finalizer-001",
        expires_at_unix=2_000_000_000,
        contract_version=budget.contract_version,
        contract_digest=budget.contract_digest,
        parser_version=budget.parser_version,
        evidence_version=budget.evidence_version,
        evidence_identity=_evidence().identity,
    )


def _ledger(tmp_path: Path) -> RecipeDeliveryReceiptLedger:
    return RecipeDeliveryReceiptLedger.initialize_protected(
        ProtectedStoreAuthority(
            root=tmp_path / "protected-receipts",
            security_identity="protected-finalizer-test-v1",
            local_filesystem=True,
            caller_writable=False,
            initialized_by_host=True,
        )
    )


def _protected_codex_backend() -> MagicMock:
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = replace(
        CodexBackend().capabilities,
        protected_recipe_delivery_capable=True,
    )
    return backend


def test_attested_finalization_commits_only_after_exact_enforcement(
    tmp_path: Path, tool_ctx
) -> None:
    tool_ctx.backend = _protected_codex_backend()
    tool_ctx.kitchen_id = "codex-attested"
    ledger = _ledger(tmp_path)
    finalized = _finalize_recipe_delivery(
        _payload("x" * 50_000),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        delivery_request=_request(),
        attestation=_attestation(),
        supported_evidence=_evidence(),
        receipt_ledger=ledger,
        now_unix=_NOW,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    assert finalized.receipt_handle is not None
    assert RECIPE_BODY_START in finalized.rendered
    assert RECIPE_BODY_END in finalized.rendered
    assert RECIPE_COMPLETION_SENTINEL in finalized.rendered
    assert ledger.receipt_status("thread-finalizer") == "pending"

    enforced = enforce_response_budget(
        finalized.rendered,
        tool_name="open_kitchen",
        artifact_dir=tmp_path / "responses",
        config=OutputBudgetConfig(),
        selected_result_token_limit=finalized.decision.selected_result_token_limit,
    )
    assert (
        complete_finalized_recipe_response(finalized, enforced, now_unix=_NOW)
        == finalized.rendered
    )
    assert ledger.receipt_status("thread-finalizer") == "committed"


def test_transformed_attested_response_aborts_pending_receipt(tmp_path: Path, tool_ctx) -> None:
    tool_ctx.backend = _protected_codex_backend()
    tool_ctx.kitchen_id = "codex-abort"
    ledger = _ledger(tmp_path)
    finalized = _finalize_recipe_delivery(
        _payload("x" * 50_000),
        surface="load_recipe",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        delivery_request=_request(),
        attestation=_attestation("thread-abort"),
        supported_evidence=_evidence(),
        receipt_ledger=ledger,
        now_unix=_NOW,
    )
    assert finalized.decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    transformed = "bounded replacement"
    assert complete_finalized_recipe_response(finalized, transformed) == transformed
    assert ledger.receipt_status("thread-abort") is None


def test_failed_receipt_abort_is_reported(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_ctx.backend = _protected_codex_backend()
    tool_ctx.kitchen_id = "codex-abort-failure"
    ledger = _ledger(tmp_path)
    finalized = _finalize_recipe_delivery(
        _payload("x" * 50_000),
        surface="load_recipe",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        finalized_projection=_test_projection(),
        delivery_request=_request(),
        attestation=_attestation("thread-abort-failure"),
        supported_evidence=_evidence(),
        receipt_ledger=ledger,
        now_unix=_NOW,
    )
    assert finalized.decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    monkeypatch.setattr(RecipeDeliveryReceiptLedger, "abort", lambda *_args: False)

    completed = complete_finalized_recipe_response(finalized, "bounded replacement")

    assert json.loads(completed) == {
        "success": False,
        "error": "recipe_delivery_receipt_abort_failed",
    }
    assert ledger.receipt_status("thread-abort-failure") == "pending"


async def test_pull_tool_reads_exact_generation_and_reports_byte_offsets(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-kitchen"
    tool_ctx_kitchen_open.config.output_budget = OutputBudgetConfig(page_max_bytes=None)
    expected_content = "héllo\n" * 12_000
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(expected_content),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    chunks: list[str] = []
    expected_byte_start = 0
    part = 0
    page_plan_sha256: str | None = None
    continuation: str | None = None
    while True:
        rendered = await get_recipe_section(
            section="content",
            part=part,
            page_plan_sha256=page_plan_sha256,
            continuation=continuation,
            **kwargs,
        )
        assert len(rendered.encode("utf-8")) <= (
            CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit
        )
        response = json.loads(rendered)
        assert response["success"] is True
        assert response["byte_start"] == expected_byte_start
        assert response["byte_end"] == response["byte_start"] + len(
            response["content"].encode("utf-8")
        )
        assert response["byte_end"] <= response["byte_total"]
        assert response["payload_sha256"] == generation.payload_sha256
        assert response["body_sha256"] == generation.body_sha256
        chunks.append(response["content"])
        expected_byte_start = response["byte_end"]
        if not response["has_more"]:
            assert "next_part" not in response
            break
        assert response["next_part"] == part + 1
        page_plan_sha256 = response["page_plan_sha256"]
        continuation = response["continuation"]
        part = response["next_part"]

    assert part > 0
    assert expected_byte_start == response["byte_total"]
    assert "".join(chunks) == expected_content


@pytest.mark.parametrize("continuation", [None, "wrong-continuation"])
async def test_pull_tool_rejects_missing_or_wrong_continuation(
    tool_ctx_kitchen_open,
    continuation: str | None,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-continuation-rejection"
    tool_ctx_kitchen_open.config.output_budget = OutputBudgetConfig(page_max_bytes=None)
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload("x" * 50_000),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    first = json.loads(await get_recipe_section(section="content", **kwargs))
    assert first["next_part"] == 1

    response = json.loads(
        await get_recipe_section(
            section="content",
            part=1,
            page_plan_sha256=first["page_plan_sha256"],
            continuation=continuation,
            **kwargs,
        )
    )

    assert response["error"] == "invalid_recipe_section_continuation"


def _assert_section_response_bound(rendered: str, tool_ctx) -> None:
    bound = resolve_recipe_section_bound_bytes(
        tool_ctx.config.output_budget.response_max_bytes,
        CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit,
        exemption_ceiling_bytes=RESPONSE_BACKSTOP_EXEMPTION_REGISTRY[
            "get_recipe_section"
        ].max_utf8_bytes,
    )
    assert len(rendered.encode("utf-8")) <= bound


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            RecipeSectionNonConvergenceError("forced nonconvergence"),
            "recipe_section_pagination_nonconvergent",
        ),
        (
            RecipeSectionPaginationError("forced invariant failure"),
            "recipe_section_internal_error",
        ),
        (RuntimeError("forced planner failure"), "recipe_section_internal_error"),
    ],
)
async def test_pull_tool_maps_planner_failures_to_exact_bounded_codes(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx_kitchen_open,
    failure: Exception,
    expected_code: str,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = f"planner-failure-{expected_code}"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    def _fail_plan(**_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_recipe.get_or_build_recipe_section_page_plan",
        _fail_plan,
    )

    rendered = await get_recipe_section(section="content", **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    assert json.loads(rendered) == {"error": expected_code, "success": False}


@pytest.mark.parametrize(
    ("section", "state", "expected_success", "expected_value"),
    [
        ("ingredients_table", "missing", False, None),
        ("ingredients_table", "none", False, None),
        ("ingredients_table", "empty", True, ""),
        ("errors", "missing", True, []),
        ("errors", "empty", True, []),
        ("warnings", "missing", True, []),
        ("warnings", "empty", True, []),
    ],
)
async def test_pull_tool_distinguishes_missing_none_and_present_empty_sections(
    tool_ctx_kitchen_open,
    section: str,
    state: str,
    expected_success: bool,
    expected_value: object,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = f"pull-empty-{section}-{state}"
    payload = _payload()
    if state == "missing":
        payload.pop(section)
    elif state == "none":
        payload[section] = None
    elif section == "ingredients_table":
        payload[section] = ""
    else:
        payload[section] = []
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=payload,
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section=section, **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    response = json.loads(rendered)
    assert response["success"] is expected_success
    if expected_success:
        if section in ("errors", "warnings"):
            # Array sections (json-array-page) arrive pre-parsed as a list.
            assert response["content"] == expected_value
        else:
            # ingredients_table uses json-scalar-page; content is still a string.
            assert json.loads(response["content"]) == expected_value
        assert response["has_more"] is False
        assert "next_part" not in response
    else:
        assert response["error"] == "section_not_found"


async def test_initial_schema_failure_is_bounded_and_never_recreates(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-initial-schema-mismatch"
    payload = _payload()
    payload["warnings"] = ["valid", 1]
    generation = _write_malformed_generation(
        tool_ctx_kitchen_open.temp_dir,
        payload,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
    )
    recreate = MagicMock(side_effect=AssertionError("schema failure must not recreate"))
    warning = MagicMock()
    monkeypatch.setattr(tools_recipe, "serve_recipe", recreate)
    monkeypatch.setattr(tools_recipe.logger, "warning", warning)
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section="warnings", **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    assert json.loads(rendered)["error"] == "recipe_artifact_schema_mismatch"
    recreate.assert_not_called()
    warning.assert_called_once_with(
        "get_recipe_section_schema_mismatch",
        stage="load",
        detail=(
            "recipe artifact section schema mismatch: invalid_section_element_type@warnings.1"
        ),
    )


async def test_recreation_persistence_schema_failure_precedes_artifact_error(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-schema-write"
    generation, _finalized = _persist_finalized_generation(tool_ctx_kitchen_open)
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
    )
    monkeypatch.setattr(
        tools_recipe,
        "persist_recipe_artifact",
        MagicMock(side_effect=RecipeArtifactSchemaError("malformed recreation")),
    )
    warning = MagicMock()
    monkeypatch.setattr(tools_recipe.logger, "warning", warning)
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section="warnings", **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    assert json.loads(rendered)["error"] == "recipe_artifact_schema_mismatch"
    warning.assert_called_once_with(
        "get_recipe_section_schema_mismatch",
        stage="recreate_persist",
        detail="malformed recreation",
    )


async def test_post_recreation_reload_schema_failure_precedes_reload_error(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-schema-reload"
    generation, _finalized = _persist_finalized_generation(tool_ctx_kitchen_open)
    monkeypatch.setattr(
        tools_recipe,
        "persist_recipe_artifact",
        lambda *_args, **_kwargs: generation,
    )
    monkeypatch.setattr(
        tools_recipe,
        "load_recipe_artifact",
        MagicMock(
            side_effect=[
                RecipeArtifactError("artifact missing"),
                RecipeArtifactSchemaError("malformed recreation"),
            ]
        ),
    )
    warning = MagicMock()
    monkeypatch.setattr(tools_recipe.logger, "warning", warning)
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section="warnings", **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    assert json.loads(rendered)["error"] == "recipe_artifact_schema_mismatch"
    warning.assert_called_once_with(
        "get_recipe_section_schema_mismatch",
        stage="reload",
        detail="malformed recreation",
    )


async def test_post_recreation_reload_artifact_failure_logs_exception_context(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-artifact-reload"
    generation, _finalized = _persist_finalized_generation(tool_ctx_kitchen_open)
    monkeypatch.setattr(
        tools_recipe,
        "persist_recipe_artifact",
        lambda *_args, **_kwargs: generation,
    )
    monkeypatch.setattr(
        tools_recipe,
        "load_recipe_artifact",
        MagicMock(
            side_effect=[
                RecipeArtifactError("artifact missing"),
                RecipeArtifactError("checksum mismatch"),
            ]
        ),
    )
    warning = MagicMock()
    monkeypatch.setattr(tools_recipe.logger, "warning", warning)
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section="warnings", **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    assert json.loads(rendered) == {
        "success": False,
        "error": "recipe_artifact_unavailable",
        "detail": "post-recreation reload failed",
    }
    warning.assert_called_once_with(
        "get_recipe_section_artifact_unavailable",
        stage="reload",
        detail="checksum mismatch",
        exc_info=True,
    )


async def test_negative_part_is_rejected_before_artifact_load(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "kitchen-test"
    generation = _persist(tool_ctx_kitchen_open.temp_dir)
    artifact_load = MagicMock(side_effect=AssertionError("negative part reached artifact load"))
    monkeypatch.setattr(tools_recipe, "load_recipe_artifact", artifact_load)
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section="content", part=-1, **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    assert json.loads(rendered)["error"] == "invalid_recipe_section_part"
    artifact_load.assert_not_called()


async def test_oversized_part_is_rejected_after_page_planning(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "kitchen-test"
    generation = _persist(tool_ctx_kitchen_open.temp_dir)
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section="content", part=10_000, **kwargs)

    _assert_section_response_bound(rendered, tool_ctx_kitchen_open)
    assert json.loads(rendered)["error"] == "invalid_recipe_section_part"


async def test_request_specific_floor_returns_exact_bounded_failure(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "kitchen-test"
    monkeypatch.setattr(
        tool_ctx_kitchen_open,
        "config",
        replace(
            tool_ctx_kitchen_open.config,
            output_budget=OutputBudgetConfig(
                response_max_bytes=RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
                page_max_bytes=None,
            ),
        ),
    )
    generation = _persist(tool_ctx_kitchen_open.temp_dir)
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    rendered = await get_recipe_section(section="content", **kwargs)

    assert len(rendered.encode("utf-8")) <= RECIPE_SECTION_RESPONSE_FLOOR_BYTES
    assert json.loads(rendered) == {
        "success": False,
        "error": "recipe_section_bound_too_small",
    }


@pytest.mark.parametrize(
    ("response_max_bytes", "conservative_limit"),
    [(20_000, 10_000), (8_000, 10_000), (10_000, 10_000)],
)
def test_recipe_section_bound_resolver_keeps_conservative_policy(
    response_max_bytes: int,
    conservative_limit: int,
) -> None:
    assert resolve_recipe_section_bound_bytes(
        response_max_bytes,
        conservative_limit,
    ) == min(response_max_bytes, conservative_limit)


async def test_pull_tool_returns_named_step_and_rejects_unknown_section(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-named-step"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    named = json.loads(await get_recipe_section(section="first", **kwargs))
    assert named["success"] is True
    assert named["section"] == "first"
    assert load_yaml(named["content"]) == {"first": {"action": "stop"}}

    unknown = json.loads(await get_recipe_section(section="not_a_real_step", **kwargs))
    assert unknown == {
        "success": False,
        "error": "section_not_found",
        "section": "not_a_real_step",
    }


async def test_pull_tool_reports_malformed_named_step_yaml(tool_ctx_kitchen_open) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-malformed-step"
    malformed = _payload("steps: [")
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=malformed,
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    response = json.loads(await get_recipe_section(section="first", **kwargs))

    assert response["success"] is False
    assert response["error"] == "recipe_artifact_parse_failed"


async def test_oversized_named_step_round_trips_through_continuation(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-oversized-step"
    tool_ctx_kitchen_open.config.output_budget = OutputBudgetConfig(page_max_bytes=None)
    content = "steps:\n  giant_step:\n    note: " + ("X" * 80_000) + "\n"
    payload = _payload(content)
    payload["post_prune_step_names"] = ["giant_step"]
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=payload,
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    chunks: list[str] = []
    part = 0
    page_plan_sha256: str | None = None
    continuation: str | None = None

    while True:
        rendered = await get_recipe_section(
            section="giant_step",
            part=part,
            page_plan_sha256=page_plan_sha256,
            continuation=continuation,
            **kwargs,
        )
        assert len(rendered.encode("utf-8")) <= (
            CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit
        )
        response = json.loads(rendered)
        assert response["success"] is True
        chunks.append(response["content"])
        if response["has_more"] is False:
            break
        page_plan_sha256 = response["page_plan_sha256"]
        continuation = response["continuation"]
        part = response["next_part"]

    assert part > 0
    reconstructed = load_yaml("".join(chunks))
    parsed = load_yaml(content)
    assert reconstructed == {"giant_step": parsed["steps"]["giant_step"]}


async def test_pull_tool_rejects_wrong_generation_identity(tool_ctx_kitchen_open) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-wrong"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    kwargs["artifact_blob_sha256"] = "sha256:" + ("0" * 64)
    response = json.loads(await get_recipe_section(section="content", **kwargs))
    assert response == {"success": False, "error": "invalid_recipe_artifact_identity"}


@pytest.mark.parametrize("field", ["artifact_blob_size_bytes", "body_size_bytes"])
async def test_pull_tool_rejects_forged_unbounded_identity_sizes(
    tool_ctx_kitchen_open, field: str
) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-unbounded-identity"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    kwargs[field] = 1_000_000_000

    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response == {"success": False, "error": "invalid_recipe_artifact_identity"}


async def test_pull_tool_recreates_missing_exact_generation(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.kitchen_id = "pull-recreate"
    generation, finalized = _persist_finalized_generation(tool_ctx_kitchen_open)
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir, kitchen_id=tool_ctx_kitchen_open.kitchen_id
    )
    recompile = MagicMock(side_effect=AssertionError("recreation must not recompile"))
    monkeypatch.setattr(
        tools_recipe,
        "serve_recipe",
        recompile,
    )

    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response["success"] is True
    assert response["content"] == _payload()["content"]
    recreated = load_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        identity=generation,
    )
    assert recreated["content"] == _payload()["content"]
    assert recreated["recipe_execution"]["execution_id"] == (
        finalized.execution_snapshot.execution_id
    )
    recompile.assert_not_called()


async def test_recreation_reuses_original_snapshot_without_snapshot_factory(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server._recipe_execution as recipe_execution

    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-stale-snapshot"
    payload = _payload()
    payload["content_hash"] = "sha256:" + ("a" * 64)
    payload["composite_hash"] = "sha256:" + ("b" * 64)
    generation, finalized = _persist_finalized_generation(
        tool_ctx_kitchen_open,
        payload,
    )
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir, kitchen_id=tool_ctx_kitchen_open.kitchen_id
    )
    snapshot_factory = MagicMock(
        side_effect=AssertionError("recreation must reuse the original snapshot")
    )
    monkeypatch.setattr(
        recipe_execution,
        "build_recipe_execution_snapshot",
        snapshot_factory,
    )

    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response["success"] is True
    recreated = load_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        identity=generation,
    )
    assert recreated["recipe_execution"]["execution_id"] == (
        finalized.execution_snapshot.execution_id
    )
    snapshot_factory.assert_not_called()


async def test_pull_tool_reports_invalid_missing_generation_recreation(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-invalid"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir, kitchen_id=tool_ctx_kitchen_open.kitchen_id
    )

    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response == {"success": False, "error": "invalid_recipe_artifact_identity"}


async def test_pull_tool_rejects_changed_recreated_generation(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-changed"
    generation, _finalized = _persist_finalized_generation(tool_ctx_kitchen_open)
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir, kitchen_id=tool_ctx_kitchen_open.kitchen_id
    )

    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    kwargs["flow_sha256"] = "sha256:" + ("0" * 64)
    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response == {"success": False, "error": "invalid_recipe_artifact_identity"}
