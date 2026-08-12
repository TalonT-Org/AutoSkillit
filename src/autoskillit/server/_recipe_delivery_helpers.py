"""Recipe delivery decision-support: attestation, margins, and manifest planning."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from autoskillit._recipe_delivery_framing import (
    RECIPE_BODY_END,
    RECIPE_BODY_START,
    RECIPE_COMPLETION_SENTINEL,
)
from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    CONSERVATIVE_ADMISSION_POLICY,
    BoundedDeliveryRoundTripBudgetExceededError,
    HostClientAttestation,
    RecipeArtifactGeneration,
    RecipeDeliveryDecision,
    RecipeDeliveryMode,
    RecipeExemptionFitnessError,
    RecipeFlowGeneration,
    Utf8ByteLimit,
    fast_dumps,
    load_yaml,
)
from autoskillit.pipeline import RecipeInitializationRequirement
from autoskillit.server._recipe_artifact import RecipeArtifactSchemaError
from autoskillit.server._recipe_initialization import build_embedded_completion_response
from autoskillit.server._recipe_section_pagination import (
    get_or_build_recipe_section_page_plan,
    select_recipe_section,
)

if TYPE_CHECKING:
    from autoskillit.core import RecipeDeliveryBudgetDef, RecipeExecutionSnapshot
    from autoskillit.pipeline import ToolContext

# Only genuinely non-terminating bounded delivery plans are rejected; a plan
# that needs more pages than this default budget is slower but terminates
# correctly. Mirrors _recipe_delivery.py's own bounded-call ceiling.
_MAX_BOUNDED_RECIPE_CALLS = 4


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
    flow_generation: RecipeFlowGeneration,
    execution_snapshot: RecipeExecutionSnapshot,
    char_ceiling: int | None = None,
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
        completion_response = (
            build_embedded_completion_response(
                initialization_id=initialization_id,
                recipe_name=generation.recipe_name,
                artifact_generation=generation,
                flow_generation=flow_generation,
                snapshot=execution_snapshot,
            )
            if completion_required and initialization_id is not None and section == entrypoint
            else None
        )
        selected = replace(
            selected,
            initialization_id=initialization_id,
            completion_response=completion_response,
        )
        if not selected.present:
            raise RecipeArtifactSchemaError(
                f"required recipe initialization section is absent: {section}"
            )
        page_plan = get_or_build_recipe_section_page_plan(
            kitchen_id=tool_ctx.kitchen_id,
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=bound_bytes,
            char_ceiling=char_ceiling,
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
    validate_compiled_recipe_delivery_budget(
        recipe=generation.recipe_name,
        backend=backend_name,
        section_page_counts=tuple(item.total_parts for item in compiled),
    )
    return compiled


def validate_compiled_recipe_delivery_budget(
    *,
    recipe: str,
    backend: str,
    section_page_counts: tuple[int, ...],
) -> None:
    """Reject only genuinely non-terminating bounded delivery plans.

    A plan that needs more pages than the default budget is slower but
    terminates correctly. Only plans with zero-element sections (where
    the bound is below the floor) are non-terminating.
    """
    if any(parts <= 0 for parts in section_page_counts):
        planned_calls = 1 + sum(section_page_counts) + 1
        raise BoundedDeliveryRoundTripBudgetExceededError(
            recipe=recipe,
            backend=backend,
            planned_calls=planned_calls,
            budget=_MAX_BOUNDED_RECIPE_CALLS,
        )


def validate_recipe_exemption_fitness(
    *,
    recipe: str,
    surface: str,
    backend: str,
    ordinary_rendered: str,
    ceiling_bytes: int,
) -> None:
    """Reject inline packaging that has consumed its reserved ten-percent margin."""
    rendered_bytes = len(ordinary_rendered.encode("utf-8"))
    admitted_bytes = _recipe_exemption_admitted_bytes(ceiling_bytes)
    if rendered_bytes > admitted_bytes:
        raise RecipeExemptionFitnessError(
            recipe=recipe,
            surface=surface,
            backend=backend,
            rendered_bytes=rendered_bytes,
            ceiling_bytes=ceiling_bytes,
            margin_bytes=ceiling_bytes - admitted_bytes,
        )


def _recipe_exemption_admitted_bytes(ceiling_bytes: int) -> int:
    """Return the byte exemption ceiling after reserving the packaging margin."""
    return ceiling_bytes * _EXEMPTION_HEADROOM_NUMERATOR // _EXEMPTION_HEADROOM_DENOMINATOR


# Shared 90% headroom factor for exemption admission — reserves 10% of the
# registered ceiling for packaging overhead (JSON envelope, counters, hashes).
# Used by both the byte-domain and char-domain exemption helpers.
_EXEMPTION_HEADROOM_NUMERATOR = 9
_EXEMPTION_HEADROOM_DENOMINATOR = 10


def _recipe_exemption_admitted_chars(ceiling_chars: int) -> int:
    """Return the char exemption ceiling after reserving the packaging margin.

    Typed counterpart of ``_recipe_exemption_admitted_bytes`` for the
    client-measured serialized-character domain. Both helpers share the
    same 90% headroom factor.
    """
    return ceiling_chars * _EXEMPTION_HEADROOM_NUMERATOR // _EXEMPTION_HEADROOM_DENOMINATOR


def _conservative_token_upper_bound(rendered: str) -> int:
    """Bound tokenizer output via the conservative 1:1 admission policy.

    Codex tokenization can merge bytes into one token, but it cannot require
    more tokens than the number of input bytes. The CONSERVATIVE_ADMISSION_POLICY
    (1 byte = 1 token) is the named core policy for this bound.
    """
    return CONSERVATIVE_ADMISSION_POLICY.to_tokens(
        Utf8ByteLimit(len(rendered.encode("utf-8")))
    ).value


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


# Context-owned host client attestation — populated once by
# initialize_host_client_attestation() (called from make_context()) and
# consumed by finalize_recipe_delivery() when no explicit attestation
# argument is supplied. Tests that monkeypatch env vars can either pass
# host_client_attestation explicitly or call initialize_host_client_attestation()
# after the monkeypatch to refresh.
_CONTEXT_HOST_CLIENT_ATTESTATION: HostClientAttestation | None = None
_CONTEXT_HOST_CLIENT_ATTESTATION_INITIALIZED: bool = False


def initialize_host_client_attestation() -> HostClientAttestation | None:
    """Read the launcher-injected host client attestation once at startup.

    Called by ``make_context()`` — the composition root — to read the env
    exactly once. Subsequent calls to ``finalize_recipe_delivery`` consume
    the cached value without rereading ``os.environ``.
    """
    global _CONTEXT_HOST_CLIENT_ATTESTATION, _CONTEXT_HOST_CLIENT_ATTESTATION_INITIALIZED  # noqa: PLW0603
    _CONTEXT_HOST_CLIENT_ATTESTATION = _resolve_host_client_attestation()
    _CONTEXT_HOST_CLIENT_ATTESTATION_INITIALIZED = True
    return _CONTEXT_HOST_CLIENT_ATTESTATION


def get_context_host_client_attestation() -> HostClientAttestation | None:
    """Return the context-owned attestation.

    Returns ``None`` when ``initialize_host_client_attestation()`` has not
    yet run — it never falls back to reading ``os.environ`` directly, since
    that would violate the single-read-at-startup contract this module
    documents on ``initialize_host_client_attestation()``.
    """
    if _CONTEXT_HOST_CLIENT_ATTESTATION_INITIALIZED:
        return _CONTEXT_HOST_CLIENT_ATTESTATION
    return None


def _resolve_host_client_attestation() -> HostClientAttestation | None:
    """Read the launcher-injected host client attestation from the environment.

    Every Claude-launched AutoSkillit command builder injects
    ``AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS`` and ``AUTOSKILLIT_ATTESTED_META_SUPPORT``
    (see ``claude.py``'s ``_CLAUDE_HOST_ATTESTATION_ENV``) — the launcher's
    attestation of what the connected host client supports. Absent or
    malformed values conservatively resolve to None, which routes recipe
    delivery decisions to ``RecipeDeliveryMode.ENVELOPE`` rather than trusting
    an unattested per-call claim.
    """
    raw_gate_tokens = os.environ.get(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS)
    raw_meta_support = os.environ.get(AUTOSKILLIT_ATTESTED_META_SUPPORT)
    if raw_gate_tokens is None or raw_meta_support is None:
        return None
    if raw_meta_support not in ("0", "1"):
        return None  # malformed → conservative default
    try:
        gate_tokens = int(raw_gate_tokens)
    except (ValueError, TypeError):
        return None
    if gate_tokens <= 0:
        return None
    return HostClientAttestation(
        attested_client_gate_tokens=gate_tokens,
        annotation_support=raw_meta_support == "1",
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


__all__ = [
    "get_context_host_client_attestation",
    "initialize_host_client_attestation",
    "validate_compiled_recipe_delivery_budget",
    "validate_recipe_exemption_fitness",
]
