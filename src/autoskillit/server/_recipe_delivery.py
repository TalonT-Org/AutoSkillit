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

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    CLAUDE_CODE_CAPABILITIES,
    RECIPE_DELIVERY_SURFACE_REGISTRY,
    BackendCapabilities,
    RecipeDeliveryAttestation,
    RecipeDeliveryDecision,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    atomic_write,
    get_logger,
    resolve_general_output_token_limit,
    resolve_recipe_delivery_decision,
)
from autoskillit.execution import (
    RecipeDeliveryReceiptLedger,
    RecipeReceiptHandle,
    codex_recipe_delivery_calling_contract,
)
from autoskillit.recipe import _extract_routing_edges, step_byte_ranges_from_yaml
from autoskillit.server._response_budget import enforce_response_budget

if TYPE_CHECKING:
    from autoskillit.core import RecipeDeliveryBudgetDef, RecipeDeliveryEvidenceDef
    from autoskillit.pipeline import ToolContext

RECIPE_ARTIFACT_DESCRIPTOR_VERSION = 1
RECIPE_ARTIFACT_SCHEMA_VERSION = 1
RECIPE_ARTIFACT_MAX_BLOB_BYTES = 1_000_000
RECIPE_ARTIFACT_MAX_DESCRIPTOR_BYTES = 16_384
RECIPE_BODY_START = "--- AUTOSKILLIT RECIPE BODY START ---"
RECIPE_BODY_END = "--- AUTOSKILLIT RECIPE BODY END ---"
RECIPE_COMPLETION_SENTINEL = "AUTOSKILLIT_RECIPE_DELIVERY_COMPLETE"


def document_recipe_delivery_contract(function: Any) -> Any:
    """Append the generated Codex contract before FastMCP reads a tool docstring."""
    description = function.__doc__ or ""
    function.__doc__ = f"{description.rstrip()}\n\n{codex_recipe_delivery_calling_contract()}\n"
    return function


class RecipeArtifactError(RuntimeError):
    """A requested immutable recipe generation is absent or corrupt."""


@dataclass(frozen=True, slots=True)
class RecipeArtifactGeneration:
    """Exact identities for one immutable canonical recipe payload."""

    producer_tool: str
    recipe_name: str
    descriptor_version: int
    schema_version: int
    payload_sha256: str
    artifact_blob_sha256: str
    artifact_blob_size_bytes: int
    body_sha256: str
    body_size_bytes: int

    def has_valid_read_bounds(self) -> bool:
        """Return whether caller-provided sizes stay within server ceilings."""
        return (
            self.descriptor_version > 0
            and self.schema_version > 0
            and 0 < self.artifact_blob_size_bytes <= RECIPE_ARTIFACT_MAX_BLOB_BYTES
            and 0 <= self.body_size_bytes <= self.artifact_blob_size_bytes
        )

    def pull_identity(self) -> dict[str, str | int]:
        return {
            "producer_tool": self.producer_tool,
            "recipe_name": self.recipe_name,
            "descriptor_version": self.descriptor_version,
            "schema_version": self.schema_version,
            "payload_sha256": self.payload_sha256,
            "artifact_blob_sha256": self.artifact_blob_sha256,
            "artifact_blob_size_bytes": self.artifact_blob_size_bytes,
            "body_sha256": self.body_sha256,
            "body_size_bytes": self.body_size_bytes,
            "pull_tool": "get_recipe_section",
        }


@dataclass(frozen=True, slots=True)
class FinalizedRecipeResponse:
    """Internal carrier consumed before FastMCP result conversion."""

    rendered: str
    decision: RecipeDeliveryDecision
    receipt_handle: RecipeReceiptHandle | None = None
    receipt_ledger: RecipeDeliveryReceiptLedger | None = None


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
    safe = "".join(
        character if character.isalnum() or character in "._-" else "_" for character in value
    )
    if safe in {".", ".."}:
        raise RecipeArtifactError("unsafe recipe artifact path component")
    return safe or "unknown"


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
    *, producer_tool: str, recipe_name: str, blob: bytes, payload: dict[str, Any]
) -> RecipeArtifactGeneration:
    body = payload.get("content")
    body_bytes = body.encode("utf-8") if isinstance(body, str) else b""
    return RecipeArtifactGeneration(
        producer_tool=producer_tool,
        recipe_name=recipe_name,
        descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
        schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
        payload_sha256=_domain_sha256("autoskillit.recipe-payload.v1", blob),
        artifact_blob_sha256=_qualified_sha256(blob),
        artifact_blob_size_bytes=len(blob),
        body_sha256=_qualified_sha256(body_bytes),
        body_size_bytes=len(body_bytes),
    )


def persist_recipe_artifact(
    temp_dir: Path,
    *,
    kitchen_id: str,
    producer_tool: str,
    recipe_name: str,
    payload: dict[str, Any],
) -> RecipeArtifactGeneration:
    """Publish an immutable canonical payload and its generation descriptor."""
    blob = _canonical_payload(payload)
    generation = _generation_from_payload(
        producer_tool=producer_tool,
        recipe_name=recipe_name,
        blob=blob,
        payload=payload,
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
    )
    if expected != identity:
        raise RecipeArtifactError("recipe body identity mismatch")
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
    return True


def recipe_pull_producers() -> frozenset[str]:
    """Return public producers authorized to resolve immutable pull generations."""
    return frozenset(
        definition.producer_tool
        for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
        if definition.pull_eligible
    )


def _step_one_line_summary(step: Any) -> str:
    """Return a compact single-line summary for one recipe step."""
    description = getattr(step, "description", "") or ""
    if description.strip():
        return description.strip().splitlines()[0][:160]
    message = getattr(step, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip().splitlines()[0][:160]
    tool = getattr(step, "tool", None)
    action = getattr(step, "action", None)
    if tool:
        return f"tool={tool}"
    if action:
        return f"action={action}"
    return ""


def _compute_step_byte_ranges(content: str) -> dict[str, tuple[int, int]]:
    """Return UTF-8 step-body offsets from the canonical YAML helper."""
    return step_byte_ranges_from_yaml(content)


def extract_step_skeleton(
    post_prune_step_names: list[str],
    routing_edges_by_step: dict[str, list[tuple[str, str]]],
    step_summaries: dict[str, str] | None = None,
    byte_ranges: dict[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Build the compact routing and byte-range index carried by an envelope."""
    skeleton: list[dict[str, Any]] = []
    for name in post_prune_step_names:
        entry: dict[str, Any] = {
            "name": name,
            "edges": [
                {"type": edge_type, "target": target}
                for edge_type, target in routing_edges_by_step.get(name) or []
                if target
            ],
        }
        summary = (step_summaries or {}).get(name) or ""
        if summary:
            entry["summary"] = summary
        span = (byte_ranges or {}).get(name)
        if span is not None:
            entry["byte_range"] = list(span)
        skeleton.append(entry)
    return {"step_count": len(skeleton), "steps": skeleton}


def _safe_utf8_truncate(data: bytes) -> str:
    """Decode a byte prefix without retaining a partial UTF-8 codepoint."""
    while data:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            data = data[: exc.start]
    return ""


def build_step_summaries(active_recipe_steps: Any) -> dict[str, str]:
    """Build step-name to one-line-summary metadata from parsed recipe steps."""
    if not isinstance(active_recipe_steps, dict) or not active_recipe_steps:
        return {}
    return {
        name: _step_one_line_summary(step)
        for name, step in active_recipe_steps.items()
        if isinstance(name, str) and name
    }


def build_routing_edges_by_step(
    active_recipe_steps: Any,
    *,
    edge_extractor: Any = _extract_routing_edges,
) -> dict[str, list[tuple[str, str]]]:
    """Build outgoing routing-edge metadata from parsed recipe steps."""
    if not isinstance(active_recipe_steps, dict) or not active_recipe_steps:
        return {}
    edges_by_step: dict[str, list[tuple[str, str]]] = {}
    for name, step in active_recipe_steps.items():
        if not isinstance(name, str) or not name:
            continue
        extracted = edge_extractor(step) if edge_extractor is not None else []
        edges_by_step[name] = [
            (edge.edge_type, edge.target)
            for edge in (extracted or [])
            if getattr(edge, "target", None)
        ]
    return edges_by_step


def build_recipe_envelope(
    payload: dict[str, Any],
    *,
    recipe_name: str,
    generation: RecipeArtifactGeneration,
    skeleton_source: ToolContext,
    bound_bytes: int,
) -> dict[str, Any]:
    """Build the bounded pull envelope used by every recipe delivery surface."""
    post_prune_raw = payload.get("post_prune_step_names") or []
    if not isinstance(post_prune_raw, list):
        post_prune_raw = []
    post_prune_names = [name for name in post_prune_raw if isinstance(name, str)]
    active_recipe_steps: dict[str, Any] | None = None
    if getattr(skeleton_source, "recipe_name", "") == recipe_name:
        active_recipe_steps = skeleton_source.active_recipe_steps
    summaries = build_step_summaries(active_recipe_steps)
    edges = build_routing_edges_by_step(active_recipe_steps)
    byte_ranges = _compute_step_byte_ranges(payload.get("content") or "")
    skeleton = extract_step_skeleton(
        post_prune_names,
        edges,
        summaries,
        byte_ranges=byte_ranges,
    )
    pull_identity = generation.pull_identity()

    def _pullable_skeleton_size(candidate: dict[str, Any]) -> int:
        pullable = {
            "success": payload.get("success", True),
            "step_flow_skeleton": candidate,
            "recipe_pull": pull_identity,
            "delivery_bound_spill": True,
        }
        return len(json.dumps(pullable, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    if _pullable_skeleton_size(skeleton) > bound_bytes:
        for summary_limit in (120, 80, 64, 48, 32, 24, 16, 8, 0):
            bounded_summaries = (
                {name: summary[:summary_limit] for name, summary in summaries.items()}
                if summary_limit
                else {}
            )
            skeleton = extract_step_skeleton(
                post_prune_names,
                edges,
                bounded_summaries,
                byte_ranges=byte_ranges,
            )
            if _pullable_skeleton_size(skeleton) <= bound_bytes:
                break

    envelope: dict[str, Any] = {"success": payload.get("success", True)}
    envelope_bytes = len(json.dumps(envelope["success"]).encode("utf-8"))
    for key in (
        "kitchen",
        "version",
        "valid",
        "dispatch_feasible",
        "errors",
        "warnings",
        "hooks",
        "post_prune_step_names",
        "post_prune_routing_edges",
        "requires_packs",
        "requires_features",
    ):
        if key in payload and payload[key] is not None:
            envelope[key] = payload[key]
            envelope_bytes += len(
                json.dumps({key: payload[key]}, ensure_ascii=False).encode("utf-8")
            )

    skeleton_overhead = len(
        json.dumps({"step_flow_skeleton": skeleton}, ensure_ascii=False).encode("utf-8")
    )
    pull_overhead = len(
        json.dumps(
            {"recipe_pull": pull_identity, "delivery_bound_spill": True},
            ensure_ascii=False,
        ).encode("utf-8")
    )
    remaining = max(
        0,
        bound_bytes - skeleton_overhead - pull_overhead - envelope_bytes - 64,
    )

    def _project_priority_strings(keys: tuple[str, ...]) -> None:
        nonlocal remaining
        candidates = [
            key for key in keys if isinstance(payload.get(key), str) and payload.get(key)
        ]
        overhead = {
            key: len(json.dumps({key: ""}, ensure_ascii=False).encode("utf-8"))
            for key in candidates
        }
        present: list[str] = []
        budget = remaining
        for key in candidates:
            if overhead[key] < budget:
                present.append(key)
                budget -= overhead[key]
        if not present:
            return

        lengths = {key: len(payload[key].encode("utf-8")) for key in present}
        allocation: dict[str, int] = dict.fromkeys(present, 0)
        active = list(present)
        pool = remaining - sum(overhead[key] for key in present)
        while active and pool > 0:
            share, extra = divmod(pool, len(active))
            still_active: list[str] = []
            for index, key in enumerate(active):
                give = share + (1 if index < extra else 0)
                take = min(give, lengths[key] - allocation[key], pool)
                allocation[key] += take
                pool -= take
                if allocation[key] < lengths[key]:
                    still_active.append(key)
            active = still_active

        for key in present:
            take = allocation[key]
            if take <= 0:
                continue
            value_bytes = payload[key].encode("utf-8")
            if len(value_bytes) <= take:
                envelope[key] = payload[key]
                remaining -= len(value_bytes) + overhead[key]
            else:
                envelope[key] = _safe_utf8_truncate(value_bytes[:take])
                remaining -= take + overhead[key]
                get_logger(__name__).warning(
                    "recipe_envelope_priority_field_truncated",
                    recipe_name=recipe_name,
                    field=key,
                    alloc_bytes=take,
                )

    _project_priority_strings(("orchestration_rules", "stop_step_semantics"))
    for key in ("ingredients_table", "suggestions"):
        value = payload.get(key)
        if value is None:
            continue
        serialized_value = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(serialized_value) + 32 <= remaining:
            envelope[key] = value
            remaining -= len(serialized_value) + 32

    envelope["step_flow_skeleton"] = skeleton
    envelope["recipe_pull"] = pull_identity
    envelope["delivery_bound_spill"] = True
    if (
        len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        <= bound_bytes
    ):
        return envelope

    fallback_candidates: tuple[dict[str, Any], ...] = (
        {
            "success": payload.get("success", True),
            "step_flow_skeleton": skeleton,
            "recipe_pull": pull_identity,
            "delivery_bound_spill": True,
        },
        {
            "success": False,
            "error": "recipe_envelope_exceeds_delivery_bound",
            "recipe_pull": pull_identity,
        },
        {"success": False, "error": "recipe_envelope_exceeds_delivery_bound"},
        {},
    )
    for fallback in fallback_candidates:
        if (
            len(json.dumps(fallback, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            <= bound_bytes
        ):
            return fallback
    raise ValueError("delivery bound is too small for a JSON object")


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
    try:
        generation = persist_recipe_artifact(
            tool_ctx.temp_dir,
            kitchen_id=tool_ctx.kitchen_id,
            producer_tool=surface_definition.producer_tool,
            recipe_name=recipe_name,
            payload=payload,
        )
    except (OSError, RecipeArtifactError, TypeError, ValueError):
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

    ordinary_rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    candidate_evidence = supported_evidence if surface_definition.negotiation_eligible else None
    candidate_attestation = attestation if surface_definition.negotiation_eligible else None
    candidate_request = delivery_request if surface_definition.negotiation_eligible else None
    high_rendered = (
        _attested_render(
            payload,
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

    if decision.mode is RecipeDeliveryMode.ORDINARY_INLINE:
        rendered = ordinary_rendered
    elif decision.mode is RecipeDeliveryMode.ATTESTED_INLINE:
        rendered = high_rendered
    else:
        envelope_bound_bytes = ordinary_limit * 4
        if (
            surface_definition.response_exemption_tool is None
            and response_ceiling_bytes is not None
        ):
            envelope_bound_bytes = min(envelope_bound_bytes, response_ceiling_bytes)
        rendered = json.dumps(
            build_recipe_envelope(
                payload,
                recipe_name=recipe_name,
                generation=generation,
                skeleton_source=tool_ctx,
                bound_bytes=envelope_bound_bytes,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return FinalizedRecipeResponse(
        rendered=rendered,
        decision=decision,
        receipt_handle=receipt_handle,
        receipt_ledger=receipt_ledger if receipt_handle is not None else None,
    )


def complete_finalized_recipe_response(
    finalized: FinalizedRecipeResponse,
    enforced: Any,
    *,
    now_unix: int | None = None,
) -> Any:
    """Commit only an exact enforced response; otherwise abort its pending receipt."""
    handle = finalized.receipt_handle
    ledger = finalized.receipt_ledger
    if enforced == finalized.rendered:
        if handle is None:
            return enforced
        if ledger is not None and ledger.commit(
            handle,
            now_unix=int(time.time()) if now_unix is None else now_unix,
        ):
            return enforced
        enforced = json.dumps(
            {"success": False, "error": "recipe_delivery_receipt_commit_failed"},
            separators=(",", ":"),
        )
    if handle is not None and (ledger is None or not ledger.abort(handle)):
        return json.dumps(
            {"success": False, "error": "recipe_delivery_receipt_abort_failed"},
            separators=(",", ":"),
        )
    return enforced


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
    "RECIPE_ARTIFACT_DESCRIPTOR_VERSION",
    "RECIPE_ARTIFACT_SCHEMA_VERSION",
    "RECIPE_BODY_END",
    "RECIPE_BODY_START",
    "RECIPE_COMPLETION_SENTINEL",
    "RecipeArtifactError",
    "RecipeArtifactGeneration",
    "complete_finalized_recipe_response",
    "enforce_recipe_resource_response",
    "finalize_recipe_delivery",
    "load_recipe_artifact",
    "persist_recipe_artifact",
    "recipe_pull_producers",
    "retire_recipe_artifacts",
]
