"""Immutable recipe artifact persistence and canonical generation building."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RECIPE_ARTIFACT_MAX_DESCRIPTOR_BYTES,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_DELIVERY_SURFACE_REGISTRY,
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    RECIPE_FLOW_SCHEMA_VERSION,
    FinalizedRecipeProjection,
    RecipeArtifactGeneration,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
    atomic_write,
    build_recipe_execution_credential,
    get_logger,
    validate_recipe_artifact_sections,
)
from autoskillit.server._recipe_generation import (
    RecipeGenerationError,
    RecipeGenerationRecord,
    generation_json_primitive,
    get_recipe_generation_store,
)
from autoskillit.server.recipe_section._lifecycle import notify_kitchen_retired

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


class RecipeArtifactError(RuntimeError):
    """A requested immutable recipe generation is absent or corrupt."""


class RecipeArtifactSchemaError(RecipeArtifactError):
    """A recipe artifact violates the static pullable-section schema."""


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
    value_bytes = value.encode("utf-8")
    encoded: list[str] = []
    for byte in value_bytes:
        character = chr(byte)
        if character.isascii() and (character.isalnum() or character in "._-"):
            encoded.append(character)
        else:
            encoded.append(f"~{byte:02x}")
    component = "".join(encoded)
    if len(component) <= 255:
        return component
    digest = hashlib.sha256(value_bytes).hexdigest()
    prefix_length = 255 - len(digest) - 1
    return f"{component[:prefix_length]}~{digest}"


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


__all__ = [
    "PreparedRecipeGeneration",
    "RecipeArtifactError",
    "RecipeArtifactSchemaError",
    "build_canonical_recipe_artifact_payload",
    "build_recipe_flow_generation",
    "load_recipe_artifact",
    "persist_recipe_artifact",
    "prepare_recipe_delivery_generation",
    "recipe_pull_producers",
    "recipe_recreation_producers",
    "retire_recipe_artifacts",
]
