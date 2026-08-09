"""Unified recipe finalization and immutable pull-generation storage."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from autoskillit._recipe_delivery_framing import (
    RECIPE_BODY_END,
    RECIPE_BODY_START,
    RECIPE_COMPLETION_SENTINEL,
)
from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    CLAUDE_CODE_CAPABILITIES,
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RECIPE_ARTIFACT_MAX_DESCRIPTOR_BYTES,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_DELIVERY_SURFACE_REGISTRY,
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    RECIPE_FLOW_SCHEMA_VERSION,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    BackendCapabilities,
    BoundedDeliveryRoundTripBudgetExceededError,
    FinalizedRecipeProjection,
    RecipeArtifactGeneration,
    RecipeDeliveryAttestation,
    RecipeDeliveryDecision,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    RecipeExecutionId,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
    atomic_write,
    build_recipe_execution_credential,
    fast_dumps,
    get_logger,
    load_yaml,
    resolve_general_output_token_limit,
    resolve_recipe_delivery_decision,
    resolve_recipe_envelope_byte_limit,
    validate_recipe_artifact_sections,
)
from autoskillit.execution import (
    RecipeDeliveryReceiptLedger,
    RecipeReceiptHandle,
    codex_recipe_delivery_calling_contract,
)
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RECIPE_SERVING as _RECIPE_SERVING,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    KitchenEffectPhase,
    KitchenTransitionToken,
    ReadyRecipe,
    RecipeInitializationRequirement,
    confirm_kitchen_effect,
    mark_kitchen_effect_ambiguous,
)
from autoskillit.server._recipe_execution import install_recipe_execution, prepare_recipe_execution
from autoskillit.server._recipe_generation import (
    RecipeGenerationError,
    RecipeGenerationRecord,
    generation_json_primitive,
    get_recipe_generation_store,
)
from autoskillit.server._recipe_initialization import (
    build_recipe_envelope,
    stage_recipe_initialization,
)
from autoskillit.server._recipe_section_pagination import (
    get_or_build_recipe_section_page_plan,
    resolve_recipe_section_bound_bytes,
    select_recipe_section,
)
from autoskillit.server._response_budget import enforce_response_budget
from autoskillit.server.recipe_section._lifecycle import notify_kitchen_retired

if TYPE_CHECKING:
    from autoskillit.core import (
        RecipeDeliveryBudgetDef,
        RecipeDeliveryEvidenceDef,
    )
    from autoskillit.pipeline import ToolContext


def document_recipe_delivery_contract(function: Any) -> Any:
    """Append the generated Codex contract before FastMCP reads a tool docstring."""
    description = function.__doc__ or ""
    function.__doc__ = f"{description.rstrip()}\n\n{codex_recipe_delivery_calling_contract()}\n"
    return function


class RecipeArtifactError(RuntimeError):
    """A requested immutable recipe generation is absent or corrupt."""


class RecipeArtifactSchemaError(RecipeArtifactError):
    """A recipe artifact violates the static pullable-section schema."""


_MAX_BOUNDED_RECIPE_CALLS = 4
_MAX_PAGES_PER_INITIALIZATION_SECTION = 1


@dataclass(frozen=True, slots=True)
class FinalizedRecipeResponse:
    """Internal carrier consumed before FastMCP result conversion."""

    rendered: str
    decision: RecipeDeliveryDecision
    receipt_handle: RecipeReceiptHandle | None = None
    receipt_ledger: RecipeDeliveryReceiptLedger | None = None
    artifact_generation: RecipeArtifactGeneration | None = None
    finalized_projection: FinalizedRecipeProjection | None = None
    flow_generation: RecipeFlowGeneration | None = None
    execution_snapshot: RecipeExecutionSnapshot | None = None
    normalized_compile_key: str | None = None
    tool_ctx: ToolContext | None = None
    recipe_name: str | None = None
    initialization_activating: bool = False
    initialization_id: str | None = None
    initialization_requirements: tuple[RecipeInitializationRequirement, ...] = ()
    kitchen_transition_token: KitchenTransitionToken | None = None


@dataclass(frozen=True, slots=True)
class PreparedRecipeGeneration:
    """Canonical compile outputs shared by every delivery surface."""

    finalized_projection: FinalizedRecipeProjection
    flow_generation: RecipeFlowGeneration
    canonical_artifact_payload: dict[str, Any]
    execution_snapshot: RecipeExecutionSnapshot
    normalized_compile_key: str
    compile_inputs: dict[str, Any]


_RECIPE_GENERATION_SOURCE_EXCLUDED_FIELDS = frozenset(
    {
        "_finalized_projection",
        "delivery_bound_spill",
        "hook_warning",
        "initialization_id",
        "kitchen",
        "recipe_pull",
        "recovery",
        "required_sections",
        "success",
        "version",
        "warnings",
    }
)


def _qualified_sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _domain_sha256(domain: str, data: bytes) -> str:
    return _qualified_sha256(domain.encode("ascii") + b"\0" + data)


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_flow_record(record: dict[str, Any]) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _finalized_projection_payload(
    projection: FinalizedRecipeProjection,
) -> dict[str, Any]:
    primitive = generation_json_primitive(projection)
    if not isinstance(primitive, dict):
        raise TypeError("finalized recipe projection did not serialize to a mapping")
    return primitive


def _recipe_generation_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Project caller payload fields shared by compile identity and persistence."""
    return {
        key: value
        for key, value in payload.items()
        if key not in _RECIPE_GENERATION_SOURCE_EXCLUDED_FIELDS
    }


def build_recipe_flow_generation(
    projection: FinalizedRecipeProjection,
) -> RecipeFlowGeneration:
    """Build the immutable record stream from one finalized recipe projection."""
    records = [
        _canonical_flow_record(
            {
                "kind": "entrypoint",
                "name": projection.entrypoint,
            }
        )
    ]
    records.extend(
        _canonical_flow_record(
            {
                "index": index,
                "kind": "step",
                "name": name,
            }
        )
        for index, name in enumerate(projection.ordered_step_names)
    )
    records.extend(
        _canonical_flow_record(
            {
                "condition": edge.condition,
                "edge_type": edge.edge_type,
                "index": index,
                "kind": "edge",
                "result_field": edge.result_field,
                "source": edge.source,
                "target": edge.target,
            }
        )
        for index, edge in enumerate(projection.ordered_flow_edges)
    )
    return RecipeFlowGeneration(
        schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        records=tuple(records),
    )


def build_canonical_recipe_artifact_payload(
    payload: dict[str, Any],
    *,
    finalized_projection: FinalizedRecipeProjection,
    flow_generation: RecipeFlowGeneration,
    execution_snapshot: RecipeExecutionSnapshot,
) -> dict[str, Any]:
    """Return the primitive, generation-bound canonical artifact payload."""
    candidate_payload = _recipe_generation_source_payload(payload)
    candidate_payload["finalized_recipe_projection"] = _finalized_projection_payload(
        finalized_projection
    )
    candidate_payload["flow_records"] = list(flow_generation.records)
    candidate_payload["recipe_flow"] = flow_generation.identity()
    candidate_payload[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY] = build_recipe_execution_credential(
        execution_snapshot
    ).as_wire_block()
    return candidate_payload


def prepare_recipe_delivery_generation(
    payload: dict[str, Any],
    *,
    recipe_name: str,
    tool_ctx: ToolContext,
    finalized_projection: FinalizedRecipeProjection,
) -> PreparedRecipeGeneration:
    """Build or reuse one server-owned canonical compile generation."""
    from autoskillit.server._recipe_execution import (  # circular-break
        build_recipe_execution_snapshot,
    )

    flow_generation = build_recipe_flow_generation(finalized_projection)
    source_payload = _recipe_generation_source_payload(payload)
    compile_inputs = {
        "recipe_name": recipe_name,
        "content_hash": source_payload.get("content_hash"),
        "composite_hash": source_payload.get("composite_hash"),
        "source_payload": generation_json_primitive(source_payload),
        "finalized_projection": _finalized_projection_payload(finalized_projection),
        "flow_generation": flow_generation.identity(),
    }
    normalized_compile_key = _domain_sha256(
        "autoskillit.recipe-compile-generation.v1",
        _canonical_payload(compile_inputs),
    )
    store = get_recipe_generation_store()
    existing = store.lookup_compile(tool_ctx.kitchen_id, normalized_compile_key)
    if existing is not None:
        if (
            existing.recipe_name != recipe_name
            or existing.finalized_projection != finalized_projection
            or existing.flow_generation != flow_generation
        ):
            raise RecipeGenerationError(
                "normalized compile generation resolved to different canonical outputs"
            )
        artifact_payload = generation_json_primitive(existing.artifact_payload)
        compile_inputs_copy = generation_json_primitive(existing.compile_inputs)
        if not isinstance(artifact_payload, dict) or not isinstance(compile_inputs_copy, dict):
            raise RecipeGenerationError("stored recipe generation did not reconstruct to mappings")
        expected_payload = build_canonical_recipe_artifact_payload(
            payload,
            finalized_projection=finalized_projection,
            flow_generation=flow_generation,
            execution_snapshot=existing.execution_snapshot,
        )
        if artifact_payload != expected_payload:
            raise RecipeGenerationError(
                "normalized compile replay changed the canonical artifact payload"
            )
        return PreparedRecipeGeneration(
            finalized_projection=existing.finalized_projection,
            flow_generation=existing.flow_generation,
            canonical_artifact_payload=artifact_payload,
            execution_snapshot=existing.execution_snapshot,
            normalized_compile_key=existing.normalized_compile_key,
            compile_inputs=compile_inputs_copy,
        )

    try:
        execution_snapshot = build_recipe_execution_snapshot(
            recipe_name=recipe_name,
            content_hash=str(source_payload.get("content_hash", "")),
            composite_hash=str(source_payload.get("composite_hash", "")),
            projection=finalized_projection.binding_projection,
        )
    except (TypeError, ValueError) as exc:
        get_logger(__name__).warning(
            "recipe_execution_compilation_failed",
            recipe_name=recipe_name,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise RecipeGenerationError("recipe execution snapshot compilation failed") from exc
    artifact_payload = build_canonical_recipe_artifact_payload(
        payload,
        finalized_projection=finalized_projection,
        flow_generation=flow_generation,
        execution_snapshot=execution_snapshot,
    )
    admitted = store.put(
        RecipeGenerationRecord(
            kitchen_id=tool_ctx.kitchen_id,
            normalized_compile_key=normalized_compile_key,
            recipe_name=recipe_name,
            finalized_projection=finalized_projection,
            flow_generation=flow_generation,
            artifact_payload=artifact_payload,
            execution_snapshot=execution_snapshot,
            execution_id=execution_snapshot.execution_id,
            compile_inputs=compile_inputs,
        )
    )
    admitted_payload = generation_json_primitive(admitted.artifact_payload)
    admitted_inputs = generation_json_primitive(admitted.compile_inputs)
    if not isinstance(admitted_payload, dict) or not isinstance(admitted_inputs, dict):
        raise RecipeGenerationError("admitted recipe generation is not reconstructable")
    return PreparedRecipeGeneration(
        finalized_projection=admitted.finalized_projection,
        flow_generation=admitted.flow_generation,
        canonical_artifact_payload=admitted_payload,
        execution_snapshot=admitted.execution_snapshot,
        normalized_compile_key=admitted.normalized_compile_key,
        compile_inputs=admitted_inputs,
    )


def _flow_generation_from_payload(payload: dict[str, Any]) -> RecipeFlowGeneration:
    records = payload.get("flow_records")
    identity = payload.get("recipe_flow")
    if (
        not isinstance(records, list)
        or not records
        or any(not isinstance(record, str) for record in records)
        or not isinstance(identity, dict)
    ):
        raise RecipeArtifactSchemaError("recipe flow generation is unavailable")
    try:
        return RecipeFlowGeneration(
            schema_version=int(identity["flow_schema_version"]),
            records=tuple(records),
            flow_sha256=str(identity["flow_sha256"]),
            flow_size_bytes=int(identity["flow_size_bytes"]),
            record_count=int(identity["flow_record_count"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RecipeArtifactSchemaError("recipe flow generation is invalid") from exc


def _validate_recipe_artifact_schema(payload: dict[str, Any]) -> None:
    findings = validate_recipe_artifact_sections(payload)
    if findings:
        summary = ",".join(finding.diagnostic() for finding in findings)
        raise RecipeArtifactSchemaError(f"recipe artifact section schema mismatch: {summary}")


def _read_bounded_bytes(path: Path, *, max_bytes: int, error: str) -> bytes:
    """Read through one descriptor with an explicit allocation ceiling."""
    try:
        with path.open("rb") as source:
            data = source.read(max_bytes + 1)
    except OSError as exc:
        raise RecipeArtifactError(error) from exc
    if len(data) > max_bytes:
        raise RecipeArtifactError(error)
    return data


def _safe_component(value: str) -> str:
    if not value:
        return "~"
    if value in {".", ".."}:
        raise RecipeArtifactError("unsafe recipe artifact path component")
    encoded: list[str] = []
    for byte in value.encode("utf-8"):
        character = chr(byte)
        if character.isascii() and (character.isalnum() or character in "._-"):
            encoded.append(character)
        else:
            encoded.append(f"~{byte:02x}")
    return "".join(encoded)


def _artifact_root(temp_dir: Path) -> Path:
    if not isinstance(temp_dir, Path):
        raise RecipeArtifactError("recipe artifact temp directory is unavailable")
    return temp_dir / "recipe-delivery"


def _generation_dir(
    temp_dir: Path,
    *,
    kitchen_id: str,
    producer_tool: str,
    recipe_name: str,
    descriptor_version: int,
    schema_version: int,
    payload_sha256: str,
) -> Path:
    return (
        _artifact_root(temp_dir)
        / _safe_component(kitchen_id)
        / _safe_component(producer_tool)
        / _safe_component(recipe_name)
        / f"descriptor-{descriptor_version}-schema-{schema_version}"
        / _safe_component(payload_sha256)
    )


def _retired_namespace_marker(temp_dir: Path, *, kitchen_id: str) -> Path:
    return _artifact_root(temp_dir) / ".retired" / f"{_safe_component(kitchen_id)}.retired"


@contextmanager
def _generation_lock(temp_dir: Path, *, exclusive: bool) -> Iterator[None]:
    root = _artifact_root(temp_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".generation.lock").open("a+b") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _generation_from_payload(
    *,
    producer_tool: str,
    recipe_name: str,
    blob: bytes,
    payload: dict[str, Any],
    flow_generation: RecipeFlowGeneration,
    descriptor_version: int | None = None,
    schema_version: int | None = None,
) -> RecipeArtifactGeneration:
    body = payload.get("content")
    body_bytes = body.encode("utf-8") if isinstance(body, str) else b""
    return RecipeArtifactGeneration(
        producer_tool=producer_tool,
        recipe_name=recipe_name,
        descriptor_version=(
            RECIPE_ARTIFACT_DESCRIPTOR_VERSION
            if descriptor_version is None
            else descriptor_version
        ),
        schema_version=(
            RECIPE_ARTIFACT_SCHEMA_VERSION if schema_version is None else schema_version
        ),
        payload_sha256=_domain_sha256("autoskillit.recipe-payload.v1", blob),
        artifact_blob_sha256=_qualified_sha256(blob),
        artifact_blob_size_bytes=len(blob),
        body_sha256=_qualified_sha256(body_bytes),
        body_size_bytes=len(body_bytes),
        flow_schema_version=flow_generation.schema_version,
        flow_sha256=flow_generation.flow_sha256,
        flow_size_bytes=flow_generation.flow_size_bytes,
        flow_record_count=flow_generation.record_count,
    )


def persist_recipe_artifact(
    temp_dir: Path,
    *,
    kitchen_id: str,
    producer_tool: str,
    recipe_name: str,
    payload: dict[str, Any],
    flow_generation: RecipeFlowGeneration | None = None,
) -> RecipeArtifactGeneration:
    """Publish an immutable canonical payload and its generation descriptor."""
    _validate_recipe_artifact_schema(payload)
    canonical_flow_generation = _flow_generation_from_payload(payload)
    if flow_generation is not None and canonical_flow_generation != flow_generation:
        raise RecipeArtifactSchemaError("recipe flow generation does not match payload")
    flow_generation = canonical_flow_generation
    blob = _canonical_payload(payload)
    if len(blob) > RECIPE_ARTIFACT_MAX_BLOB_BYTES:
        raise RecipeArtifactError("recipe artifact blob exceeds persistence limit")
    generation = _generation_from_payload(
        producer_tool=producer_tool,
        recipe_name=recipe_name,
        blob=blob,
        payload=payload,
        flow_generation=flow_generation,
    )
    directory = _generation_dir(
        temp_dir,
        kitchen_id=kitchen_id,
        producer_tool=producer_tool,
        recipe_name=recipe_name,
        descriptor_version=generation.descriptor_version,
        schema_version=generation.schema_version,
        payload_sha256=generation.payload_sha256,
    )
    descriptor = _canonical_payload(generation.pull_identity())
    with _generation_lock(temp_dir, exclusive=True):
        if _retired_namespace_marker(temp_dir, kitchen_id=kitchen_id).exists():
            raise RecipeArtifactError("recipe artifact namespace is retired")
        directory.mkdir(parents=True, exist_ok=True)
        blob_path = directory / "payload.json"
        descriptor_path = directory / "descriptor.json"
        if blob_path.exists():
            existing_blob = _read_bounded_bytes(
                blob_path,
                max_bytes=len(blob),
                error="content-addressed payload collision",
            )
            if existing_blob != blob:
                raise RecipeArtifactError("content-addressed payload collision")
        if descriptor_path.exists():
            existing_descriptor = _read_bounded_bytes(
                descriptor_path,
                max_bytes=len(descriptor),
                error="content-addressed descriptor collision",
            )
            if existing_descriptor != descriptor:
                raise RecipeArtifactError("content-addressed descriptor collision")
        if not blob_path.exists():
            atomic_write(blob_path, blob.decode("utf-8"))
        if not descriptor_path.exists():
            atomic_write(descriptor_path, descriptor.decode("utf-8"))
    return generation


def load_recipe_artifact(
    temp_dir: Path,
    *,
    kitchen_id: str,
    identity: RecipeArtifactGeneration,
) -> dict[str, Any]:
    """Read and independently verify an exact immutable payload generation."""
    if not identity.has_valid_read_bounds():
        raise RecipeArtifactError("invalid recipe artifact identity bounds")
    directory = _generation_dir(
        temp_dir,
        kitchen_id=kitchen_id,
        producer_tool=identity.producer_tool,
        recipe_name=identity.recipe_name,
        descriptor_version=identity.descriptor_version,
        schema_version=identity.schema_version,
        payload_sha256=identity.payload_sha256,
    )
    with _generation_lock(temp_dir, exclusive=False):
        blob_path = directory / "payload.json"
        descriptor_path = directory / "descriptor.json"
        blob = _read_bounded_bytes(
            blob_path,
            max_bytes=identity.artifact_blob_size_bytes,
            error="artifact blob size mismatch",
        )
        descriptor_bytes = _read_bounded_bytes(
            descriptor_path,
            max_bytes=RECIPE_ARTIFACT_MAX_DESCRIPTOR_BYTES,
            error="recipe generation descriptor exceeds read limit",
        )
    if len(blob) != identity.artifact_blob_size_bytes:
        raise RecipeArtifactError("artifact blob size mismatch")
    if _qualified_sha256(blob) != identity.artifact_blob_sha256:
        raise RecipeArtifactError("artifact blob digest mismatch")
    if _domain_sha256("autoskillit.recipe-payload.v1", blob) != identity.payload_sha256:
        raise RecipeArtifactError("semantic payload digest mismatch")
    try:
        descriptor_raw = descriptor_bytes.decode("utf-8")
        descriptor = json.loads(descriptor_raw)
        payload = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecipeArtifactError("recipe generation is not valid JSON") from exc
    if descriptor != identity.pull_identity():
        raise RecipeArtifactError("generation descriptor mismatch")
    if not isinstance(payload, dict):
        raise RecipeArtifactError("persisted recipe payload is not a mapping")
    expected = _generation_from_payload(
        producer_tool=identity.producer_tool,
        recipe_name=identity.recipe_name,
        blob=blob,
        payload=payload,
        flow_generation=_flow_generation_from_payload(payload),
        descriptor_version=identity.descriptor_version,
        schema_version=identity.schema_version,
    )
    if expected != identity:
        raise RecipeArtifactError("recipe body identity mismatch")
    _validate_recipe_artifact_schema(payload)
    return payload


def retire_recipe_artifacts(temp_dir: Path, *, kitchen_id: str) -> bool:
    """Retire one kitchen namespace after all shared-lock readers finish."""
    try:
        namespace = _artifact_root(temp_dir) / _safe_component(kitchen_id)
        retired_marker = _retired_namespace_marker(temp_dir, kitchen_id=kitchen_id)
        with _generation_lock(temp_dir, exclusive=True):
            retired_marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(retired_marker, "retired\n")
            if namespace.exists():
                shutil.rmtree(namespace)
    except (OSError, RecipeArtifactError, TypeError):
        return False
    try:
        notify_kitchen_retired(kitchen_id)
    except Exception:
        get_logger(__name__).warning(
            "recipe_section_cache_retirement_eviction_failed",
            kitchen_id=kitchen_id,
            exc_info=True,
        )
    return True


def _validate_producer_authorization_policies() -> None:
    policies: dict[str, tuple[bool, bool]] = {}
    for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values():
        policy = (definition.pull_eligible, definition.recreation_eligible)
        existing = policies.setdefault(definition.producer_tool, policy)
        if existing != policy:
            raise RecipeArtifactError(
                "recipe delivery surfaces sharing a producer must share pull policies"
            )


def recipe_pull_producers() -> frozenset[str]:
    """Return public producers authorized to resolve immutable pull generations."""
    _validate_producer_authorization_policies()
    return frozenset(
        definition.producer_tool
        for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
        if definition.pull_eligible
    )


def recipe_recreation_producers() -> frozenset[str]:
    """Return producers whose missing generations may be rebuilt in-session."""
    _validate_producer_authorization_policies()
    return frozenset(
        definition.producer_tool
        for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
        if definition.recreation_eligible
    )


def _initialization_requirements(
    *,
    tool_ctx: ToolContext,
    generation: RecipeArtifactGeneration,
    payload: dict[str, Any],
    entrypoint: str,
    bound_bytes: int,
    initialization_id: str | None,
    backend_name: str,
    completion_required: bool,
) -> tuple[RecipeInitializationRequirement, ...]:
    """Build the exact flow and entrypoint page plans advertised by a manifest."""

    def _entrypoint_content(step_name: str) -> str:
        content = payload.get("content")
        parsed = load_yaml(content) if isinstance(content, str) else None
        steps = parsed.get("steps") if isinstance(parsed, dict) else None
        step = steps.get(step_name) if isinstance(steps, dict) else None
        if not isinstance(step, dict):
            raise RecipeArtifactSchemaError("recipe entrypoint definition is unavailable")
        return fast_dumps({step_name: step})

    requirements: list[RecipeInitializationRequirement] = []
    for section in ("flow_records", entrypoint):
        selected = select_recipe_section(
            payload,
            section,
            dynamic_content_loader=_entrypoint_content,
        )
        selected = replace(selected, initialization_id=initialization_id)
        if not selected.present:
            raise RecipeArtifactSchemaError(
                f"required recipe initialization section is absent: {section}"
            )
        page_plan = get_or_build_recipe_section_page_plan(
            kitchen_id=tool_ctx.kitchen_id,
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=bound_bytes,
        )
        requirements.append(
            RecipeInitializationRequirement(
                section=section,
                page_plan_sha256=page_plan.page_plan_sha256,
                total_parts=page_plan.total_parts,
                compiled_bytes=page_plan.measured_bytes,
            )
        )
    compiled = tuple(requirements)
    planned_calls = 1 + sum(item.total_parts for item in compiled) + int(completion_required)
    calibrated_bound = bound_bytes > OutputBudgetConfig().response_max_bytes
    if calibrated_bound and (
        any(item.total_parts > _MAX_PAGES_PER_INITIALIZATION_SECTION for item in compiled)
        or planned_calls > _MAX_BOUNDED_RECIPE_CALLS
    ):
        raise BoundedDeliveryRoundTripBudgetExceededError(
            recipe=generation.recipe_name,
            backend=backend_name,
            planned_calls=planned_calls,
            budget=_MAX_BOUNDED_RECIPE_CALLS,
        )
    return compiled


def _conservative_token_upper_bound(rendered: str) -> int:
    """Bound tokenizer output without assuming four UTF-8 bytes per token.

    Codex tokenization can merge bytes into one token, but it cannot require
    more tokens than the number of input bytes. Using the exact byte count is
    intentionally conservative for delivery-mode admission.
    """
    return len(rendered.encode("utf-8"))


def _attested_render(
    payload: dict[str, Any],
    generation: RecipeArtifactGeneration,
    *,
    budget: RecipeDeliveryBudgetDef,
    evidence_identity: str,
) -> str:
    body = payload.get("content") if isinstance(payload.get("content"), str) else ""
    metadata = {key: value for key, value in payload.items() if key != "content"}
    control = {
        "recipe_delivery": {
            "mode": RecipeDeliveryMode.ATTESTED_INLINE.value,
            "contract_digest": budget.contract_digest,
            "evidence_identity": evidence_identity,
            "selected_result_token_limit": (
                budget.authoritative_attested_recipe_result_token_limit
            ),
            "recipe_pull": generation.pull_identity(),
            "payload_metadata": metadata,
        }
    }
    prefix = json.dumps(control, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{prefix}\n{RECIPE_BODY_START}\n{body}\n{RECIPE_BODY_END}\n"
        f"{RECIPE_COMPLETION_SENTINEL} {generation.body_sha256}"
    )


def _failure_decision(
    *, producer: str, reason: str, selected_limit: int, contract_digest: str
) -> RecipeDeliveryDecision:
    return RecipeDeliveryDecision(
        mode=RecipeDeliveryMode.ENVELOPE,
        caller_requested_outer_tokens=None,
        host_observed_requested_outer_tokens=None,
        required_outer_tokens=0,
        unnegotiated_tool_result_token_limit=selected_limit,
        selected_result_token_limit=selected_limit,
        contract_digest=contract_digest,
        evidence_identity=None,
        reason=reason,
        producer=producer,
        payload_sha256="sha256:" + ("0" * 64),
        receipt_status="not_reserved",
    )


def finalize_recipe_delivery(
    payload: dict[str, Any],
    *,
    surface: str,
    recipe_name: str,
    tool_ctx: ToolContext,
    finalized_projection: FinalizedRecipeProjection,
    flow_generation: RecipeFlowGeneration,
    canonical_artifact_payload: dict[str, Any],
    execution_snapshot: RecipeExecutionSnapshot,
    normalized_compile_key: str,
    delivery_request: RecipeDeliveryRequest | None = None,
    attestation: RecipeDeliveryAttestation | None = None,
    supported_evidence: RecipeDeliveryEvidenceDef | None = None,
    receipt_ledger: RecipeDeliveryReceiptLedger | None = None,
    now_unix: int | None = None,
) -> FinalizedRecipeResponse:
    """Persist, decide, shape, and transactionally reserve one recipe response."""
    surface_definition = RECIPE_DELIVERY_SURFACE_REGISTRY[surface]
    candidate_capabilities = (
        getattr(tool_ctx.backend, "capabilities", None) if tool_ctx.backend is not None else None
    )
    capabilities = (
        candidate_capabilities
        if isinstance(candidate_capabilities, BackendCapabilities)
        else replace(
            CLAUDE_CODE_CAPABILITIES,
            unnegotiated_tool_result_token_limit=(
                CLAUDE_CODE_CAPABILITIES.unnegotiated_tool_result_token_limit
            ),
            protected_recipe_delivery_capable=False,
            recipe_delivery_budget=None,
        )
    )
    delivery_budget = capabilities.recipe_delivery_budget
    ordinary_limit = resolve_general_output_token_limit(capabilities)
    envelope_byte_limit = resolve_recipe_envelope_byte_limit(capabilities)
    if (
        not isinstance(finalized_projection, FinalizedRecipeProjection)
        or not isinstance(flow_generation, RecipeFlowGeneration)
        or not isinstance(execution_snapshot, RecipeExecutionSnapshot)
        or not isinstance(normalized_compile_key, str)
        or not normalized_compile_key
    ):
        raise TypeError("finalize_recipe_delivery requires a complete prepared generation")
    candidate_payload = dict(canonical_artifact_payload)
    if (
        candidate_payload.get("flow_records") != list(flow_generation.records)
        or candidate_payload.get("recipe_flow") != flow_generation.identity()
    ):
        raise ValueError("canonical artifact payload does not match prepared flow generation")
    surface_payload = dict(payload)
    if "success" not in surface_payload:
        surface_payload["success"] = True
    for generation_field in (
        "finalized_recipe_projection",
        "flow_records",
        RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
        "recipe_flow",
    ):
        surface_payload[generation_field] = candidate_payload[generation_field]
    initialization_id = uuid4().hex if surface_definition.initialization_activating else None
    try:
        generation = persist_recipe_artifact(
            tool_ctx.temp_dir,
            kitchen_id=tool_ctx.kitchen_id,
            producer_tool=surface_definition.producer_tool,
            recipe_name=recipe_name,
            payload=candidate_payload,
            flow_generation=flow_generation,
        )
        get_recipe_generation_store().bind_surface(
            tool_ctx.kitchen_id,
            normalized_compile_key,
            surface,
            generation,
        )
    except (
        OSError,
        RecipeArtifactError,
        RecipeGenerationError,
        TypeError,
        ValueError,
    ):
        decision = _failure_decision(
            producer=surface_definition.producer_tool,
            reason="recipe_artifact_persistence_failed",
            selected_limit=ordinary_limit,
            contract_digest=(delivery_budget.contract_digest if delivery_budget else ""),
        )
        return FinalizedRecipeResponse(
            rendered=json.dumps(
                {"success": False, "error": "recipe_artifact_unavailable"},
                separators=(",", ":"),
            ),
            decision=decision,
        )

    surface_payload["recipe_pull"] = generation.pull_identity()
    if initialization_id is None:
        with tool_ctx.recipe_execution_lock:
            current_initialization = tool_ctx.recipe_initialization_state
        if (
            isinstance(current_initialization, InitializingRecipe)
            and current_initialization.recipe_name == recipe_name
            and current_initialization.artifact_generation.payload_sha256
            == generation.payload_sha256
            and current_initialization.artifact_generation.artifact_blob_sha256
            == generation.artifact_blob_sha256
            and current_initialization.flow_generation == flow_generation
        ):
            initialization_id = current_initialization.initialization_id
    if initialization_id is not None:
        surface_payload["initialization_id"] = initialization_id
    ordinary_rendered = json.dumps(
        surface_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    candidate_evidence = supported_evidence if surface_definition.negotiation_eligible else None
    candidate_attestation = attestation if surface_definition.negotiation_eligible else None
    candidate_request = delivery_request if surface_definition.negotiation_eligible else None
    high_rendered = (
        _attested_render(
            surface_payload,
            generation,
            budget=delivery_budget,
            evidence_identity=(
                candidate_evidence.identity if candidate_evidence is not None else "unsupported"
            ),
        )
        if delivery_budget is not None
        else ordinary_rendered
    )
    ordinary_required_tokens = _conservative_token_upper_bound(ordinary_rendered)
    required_tokens = (
        ordinary_required_tokens
        if ordinary_required_tokens <= ordinary_limit
        else _conservative_token_upper_bound(high_rendered)
    )
    decision = resolve_recipe_delivery_decision(
        capabilities=capabilities,
        required_serialized_tokens=required_tokens,
        budget=delivery_budget,
        producer=surface_definition.producer_tool,
        payload_sha256=generation.payload_sha256,
        request=candidate_request,
        attestation=candidate_attestation,
        supported_evidence=candidate_evidence,
        now_unix=now_unix,
    )
    response_budget = getattr(getattr(tool_ctx, "config", None), "output_budget", None)
    response_max_bytes = getattr(response_budget, "response_max_bytes", None)
    response_ceiling_bytes = (
        response_max_bytes
        if isinstance(response_max_bytes, int) and response_max_bytes > 0
        else None
    )
    section_response_bound_bytes = resolve_recipe_section_bound_bytes(
        (
            response_ceiling_bytes
            if response_ceiling_bytes is not None
            else OutputBudgetConfig().response_max_bytes
        ),
        ordinary_limit,
    )
    if (
        decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
        and surface_definition.response_exemption_tool is None
        and response_ceiling_bytes is not None
        and len(ordinary_rendered.encode("utf-8")) > response_ceiling_bytes
    ):
        decision = replace(
            decision,
            mode=RecipeDeliveryMode.ENVELOPE,
            reason="server_response_budget_requires_envelope",
            receipt_status="not_reserved",
        )
    receipt_handle: RecipeReceiptHandle | None = None
    if decision.mode is RecipeDeliveryMode.ATTESTED_INLINE:
        if (
            delivery_budget is None
            or receipt_ledger is None
            or candidate_request is None
            or candidate_attestation is None
            or candidate_evidence is None
        ):
            decision = replace(
                decision,
                mode=RecipeDeliveryMode.ENVELOPE,
                selected_result_token_limit=ordinary_limit,
                reason="protected_receipt_store_unavailable",
                receipt_status="not_reserved",
            )
        else:
            reservation = receipt_ledger.reserve(
                capabilities=capabilities,
                required_serialized_tokens=required_tokens,
                budget=delivery_budget,
                request=candidate_request,
                attestation=candidate_attestation,
                supported_evidence=candidate_evidence,
                producer=surface_definition.producer_tool,
                payload_sha256=generation.payload_sha256,
                now_unix=int(time.time()) if now_unix is None else now_unix,
            )
            if reservation.handle is None:
                decision = replace(
                    decision,
                    mode=RecipeDeliveryMode.ENVELOPE,
                    selected_result_token_limit=ordinary_limit,
                    reason=reservation.reason,
                    receipt_status="not_reserved",
                )
            else:
                receipt_handle = reservation.handle
                decision = replace(decision, receipt_status="pending")

    # Issue #4399 exemption override: when an exempt surface's ordinary-rendered
    # payload fits within the registered exemption ceiling, upgrade ENVELOPE back
    # to ORDINARY_INLINE so the full recipe body survives. Placed after ALL
    # ENVELOPE-producing branches (initial resolve, response-budget downgrade,
    # receipt-store-missing downgrade, reservation-failure downgrade) so the
    # override catches every path. Without this, exempt surfaces like
    # open_kitchen on Claude Code (protected_recipe_delivery_capable=False) get
    # ENVELOPE for any payload exceeding the 46.5K ordinary_limit, producing a
    # degenerate formatter output with no recipe body.
    #
    # Scoped to non-protected backends (those without a recipe_delivery_budget,
    # i.e. Claude Code). Codex has its own recipe_delivery_budget and bounded
    # envelope semantics — applying the override to Codex would regress
    # `test_codex_without_supported_host_evidence_uses_bounded_envelope`.
    if (
        decision.mode is RecipeDeliveryMode.ENVELOPE
        and surface_definition.response_exemption_tool is not None
        and capabilities.recipe_delivery_budget is None
    ):
        _exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.get(
            surface_definition.response_exemption_tool
        )
        if (
            _exemption is not None
            and len(ordinary_rendered.encode("utf-8")) <= _exemption.max_utf8_bytes
        ):
            decision = replace(
                decision,
                mode=RecipeDeliveryMode.ORDINARY_INLINE,
                selected_result_token_limit=_exemption.max_utf8_bytes // 4,
                reason="exemption_overrides_envelope",
                receipt_status="not_required",
            )

    initialization_requirements: tuple[RecipeInitializationRequirement, ...] = ()
    if decision.mode is RecipeDeliveryMode.ORDINARY_INLINE:
        rendered = ordinary_rendered
    elif decision.mode is RecipeDeliveryMode.ATTESTED_INLINE:
        rendered = high_rendered
    else:
        envelope_bound_bytes = envelope_byte_limit
        if (
            surface_definition.response_exemption_tool is None
            and response_ceiling_bytes is not None
        ):
            envelope_bound_bytes = min(envelope_bound_bytes, response_ceiling_bytes)
        try:
            initialization_requirements = _initialization_requirements(
                tool_ctx=tool_ctx,
                generation=generation,
                payload=candidate_payload,
                entrypoint=finalized_projection.entrypoint,
                bound_bytes=section_response_bound_bytes,
                initialization_id=initialization_id,
                backend_name=(
                    getattr(tool_ctx.backend, "name", None)
                    or capabilities.process_name
                    or "unknown"
                ),
                completion_required=surface_definition.initialization_activating,
            )
            rendered = json.dumps(
                build_recipe_envelope(
                    candidate_payload,
                    recipe_name=recipe_name,
                    generation=generation,
                    flow_generation=flow_generation,
                    entrypoint=finalized_projection.entrypoint,
                    bound_bytes=envelope_bound_bytes,
                    initialization_id=initialization_id,
                    initialization_requirements=initialization_requirements,
                    completion_required=(surface_definition.initialization_activating),
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except Exception:
            get_logger(__name__).error(
                "recipe initialization manifest planning failed",
                recipe_name=recipe_name,
                exc_info=True,
            )
            rendered = json.dumps(
                {"success": False, "error": "recipe_initialization_plan_failed"},
                separators=(",", ":"),
            )
    kitchen_transition_token = None
    if (
        surface.startswith("open_kitchen")
        and hasattr(tool_ctx, "kitchen_transition_lock")
        and hasattr(tool_ctx, "kitchen_open_state")
    ):
        with tool_ctx.kitchen_transition_lock:
            state = tool_ctx.kitchen_open_state
            serving_effect = next(
                (effect for effect in state.effects if effect.name == _RECIPE_SERVING),
                None,
            )
            if serving_effect is not None:
                kitchen_transition_token = KitchenTransitionToken(
                    operation_id=state.operation_id,
                    effect_id=serving_effect.effect_id,
                )
    return FinalizedRecipeResponse(
        rendered=rendered,
        decision=decision,
        receipt_handle=receipt_handle,
        receipt_ledger=receipt_ledger if receipt_handle is not None else None,
        artifact_generation=generation,
        finalized_projection=finalized_projection,
        flow_generation=flow_generation,
        execution_snapshot=execution_snapshot,
        normalized_compile_key=normalized_compile_key,
        tool_ctx=(
            tool_ctx
            if execution_snapshot is not None or kitchen_transition_token is not None
            else None
        ),
        recipe_name=recipe_name,
        initialization_activating=surface_definition.initialization_activating,
        initialization_id=initialization_id,
        initialization_requirements=initialization_requirements,
        kitchen_transition_token=kitchen_transition_token,
    )


def complete_finalized_recipe_response(
    finalized: FinalizedRecipeResponse,
    enforced: Any,
    *,
    now_unix: int | None = None,
) -> Any:
    """Commit receipt and lifecycle state only for exact enforced response bytes."""
    handle = finalized.receipt_handle
    ledger = finalized.receipt_ledger
    parsed: dict[str, Any] | None = None
    prepared_execution: Any = None
    previous_initialization_state: Any = None
    transition_token = finalized.kitchen_transition_token
    if enforced == finalized.rendered and transition_token is not None:
        transition_owned = False
        if (
            finalized.tool_ctx is not None
            and hasattr(finalized.tool_ctx, "kitchen_transition_lock")
            and hasattr(finalized.tool_ctx, "kitchen_open_state")
        ):
            with finalized.tool_ctx.kitchen_transition_lock:
                state = finalized.tool_ctx.kitchen_open_state
                transition_owned = state.operation_id == transition_token.operation_id and any(
                    effect.name == _RECIPE_SERVING
                    and effect.effect_id == transition_token.effect_id
                    for effect in state.effects
                )
        if not transition_owned:
            enforced = json.dumps(
                {
                    "success": False,
                    "error": "kitchen_transition_ownership_mismatch",
                },
                separators=(",", ":"),
            )
    if enforced == finalized.rendered and finalized.initialization_activating:
        required_values = (
            finalized.tool_ctx,
            finalized.recipe_name,
            finalized.artifact_generation,
            finalized.flow_generation,
            finalized.execution_snapshot,
            finalized.normalized_compile_key,
            finalized.initialization_id,
        )
        if any(value is None or value == "" for value in required_values):
            enforced = json.dumps(
                {"success": False, "error": "recipe_initialization_identity_missing"},
                separators=(",", ":"),
            )
        else:
            assert finalized.tool_ctx is not None
            assert finalized.execution_snapshot is not None
            try:
                candidate = (
                    json.loads(finalized.rendered)
                    if finalized.decision.mode is not RecipeDeliveryMode.ATTESTED_INLINE
                    else {"success": True}
                )
            except json.JSONDecodeError:
                candidate = {"success": False}
            if not isinstance(candidate, dict) or candidate.get("success") is False:
                enforced = json.dumps(
                    {"success": False, "error": "recipe_initialization_failed"},
                    separators=(",", ":"),
                )
            else:
                parsed = candidate
    if enforced == finalized.rendered and finalized.initialization_activating:
        assert finalized.tool_ctx is not None
        assert finalized.recipe_name is not None
        assert finalized.artifact_generation is not None
        assert finalized.flow_generation is not None
        assert finalized.execution_snapshot is not None
        assert finalized.normalized_compile_key is not None
        assert finalized.initialization_id is not None
        assert parsed is not None
        with finalized.tool_ctx.recipe_execution_lock:
            previous_initialization_state = finalized.tool_ctx.recipe_initialization_state
        try:
            stage_recipe_initialization(
                finalized.tool_ctx,
                recipe_name=finalized.recipe_name,
                artifact_generation=finalized.artifact_generation,
                flow_generation=finalized.flow_generation,
                initialization_id=finalized.initialization_id,
                staged_snapshot=finalized.execution_snapshot,
                requirements=(
                    finalized.initialization_requirements
                    if parsed.get("delivery_bound_spill") is True
                    else ()
                ),
                generation_store_key=finalized.normalized_compile_key,
            )
            if parsed.get("delivery_bound_spill") is not True:
                prepared_execution = prepare_recipe_execution(
                    finalized.tool_ctx,
                    snapshot=finalized.execution_snapshot,
                )
                install_recipe_execution(
                    finalized.tool_ctx,
                    prepared_execution=prepared_execution,
                    completion_receipt=_qualified_sha256(
                        (
                            finalized.initialization_id
                            + finalized.artifact_generation.payload_sha256
                        ).encode("utf-8")
                    ),
                )
        except Exception:
            with finalized.tool_ctx.recipe_execution_lock:
                current_state = finalized.tool_ctx.recipe_initialization_state
                if current_state is not previous_initialization_state and isinstance(
                    current_state, InitializingRecipe
                ):
                    finalized.tool_ctx.audit_admission_ledger.retire_installation(
                        recipe_execution_id=RecipeExecutionId(
                            current_state.staged_snapshot.execution_id
                        ),
                        installation_version=current_state.installation_version,
                    )
                elif current_state is not previous_initialization_state and isinstance(
                    current_state, ReadyRecipe
                ):
                    finalized.tool_ctx.audit_admission_ledger.retire_installation(
                        recipe_execution_id=RecipeExecutionId(
                            current_state.installed_execution.snapshot.execution_id
                        ),
                        installation_version=(
                            current_state.installed_execution.installation_version
                        ),
                    )
                finalized.tool_ctx.recipe_initialization_state = previous_initialization_state
            get_logger(__name__).error(
                "recipe execution snapshot installation failed",
                initialization_id=finalized.initialization_id,
                exc_info=True,
            )
            enforced = json.dumps(
                {
                    "success": False,
                    "error": "recipe_execution_install_failed",
                },
                separators=(",", ":"),
            )
    if enforced == finalized.rendered and handle is not None:
        try:
            receipt_committed = ledger is not None and ledger.commit(
                handle,
                now_unix=int(time.time()) if now_unix is None else now_unix,
            )
        except Exception:
            receipt_committed = False
            get_logger(__name__).error(
                "recipe delivery receipt commit failed",
                exc_info=True,
            )
        if not receipt_committed:
            if finalized.initialization_activating:
                assert finalized.tool_ctx is not None
                with finalized.tool_ctx.recipe_execution_lock:
                    finalized.tool_ctx.recipe_initialization_state = previous_initialization_state
            enforced = json.dumps(
                {"success": False, "error": "recipe_delivery_receipt_commit_failed"},
                separators=(",", ":"),
            )
        else:
            handle = None
    if handle is not None and (ledger is None or not ledger.abort(handle)):
        enforced = json.dumps(
            {"success": False, "error": "recipe_delivery_receipt_abort_failed"},
            separators=(",", ":"),
        )
    _complete_kitchen_serving_transition(finalized, enforced)
    return enforced


def _complete_kitchen_serving_transition(
    finalized: FinalizedRecipeResponse,
    enforced: Any,
) -> None:
    """Close the owned serving effect at the response-enforcement boundary."""
    transition_token = finalized.kitchen_transition_token
    tool_ctx = finalized.tool_ctx
    if transition_token is None or tool_ctx is None:
        return
    with tool_ctx.kitchen_transition_lock:
        state = tool_ctx.kitchen_open_state
        if state.operation_id != transition_token.operation_id:
            return
        effect = next(
            (
                candidate
                for candidate in state.effects
                if candidate.name == _RECIPE_SERVING
                and candidate.effect_id == transition_token.effect_id
            ),
            None,
        )
        if effect is None or effect.phase is not KitchenEffectPhase.STARTED:
            return
        if enforced == finalized.rendered:
            state = confirm_kitchen_effect(
                state,
                effect.name,
                receipt=f"response:{effect.effect_id}",
            )
        else:
            state = mark_kitchen_effect_ambiguous(
                state,
                effect.name,
                evidence="finalized recipe response changed during enforcement",
            )
        tool_ctx.kitchen_open_state = state


def enforce_recipe_resource_response(
    finalized: FinalizedRecipeResponse,
    *,
    tool_ctx: ToolContext,
) -> str:
    """Apply the ordinary response backstop and complete the receipt transaction."""
    configured_budget = getattr(tool_ctx.config, "output_budget", None)
    output_budget = (
        configured_budget
        if isinstance(configured_budget, OutputBudgetConfig)
        else OutputBudgetConfig()
    )
    temp_dir = getattr(tool_ctx, "temp_dir", None)
    enforced = enforce_response_budget(
        finalized.rendered,
        tool_name="get_recipe",
        artifact_dir=(
            temp_dir / "responses" / "get_recipe" if isinstance(temp_dir, Path) else None
        ),
        config=output_budget,
        selected_result_token_limit=finalized.decision.selected_result_token_limit,
    )
    completed = complete_finalized_recipe_response(finalized, enforced)
    if isinstance(completed, str):
        return completed
    return json.dumps(completed, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "FinalizedRecipeResponse",
    "PreparedRecipeGeneration",
    "RECIPE_ARTIFACT_DESCRIPTOR_VERSION",
    "RECIPE_ARTIFACT_SCHEMA_VERSION",
    "RECIPE_BODY_END",
    "RECIPE_BODY_START",
    "RECIPE_COMPLETION_SENTINEL",
    "RecipeArtifactError",
    "RecipeArtifactSchemaError",
    "RecipeArtifactGeneration",
    "complete_finalized_recipe_response",
    "enforce_recipe_resource_response",
    "finalize_recipe_delivery",
    "load_recipe_artifact",
    "persist_recipe_artifact",
    "prepare_recipe_delivery_generation",
    "recipe_pull_producers",
    "retire_recipe_artifacts",
]
