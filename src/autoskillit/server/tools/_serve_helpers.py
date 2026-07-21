"""Unified serve-pipeline helpers.

The only legal call site for load_and_validate in server/tools/.
All four serve surfaces (open_kitchen normal, open_kitchen deferred-recall,
load_recipe, get_recipe) must call serve_recipe() instead of calling
ctx.recipes.load_and_validate() directly. This structural invariant is
enforced by tests/arch/test_serve_surface_registry.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    atomic_write,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import BackendCapabilities, CodingAgentBackend
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.recipe.schema import RecipeStep


def build_backend_capabilities_map(
    effective_backend_map: dict[str, str] | None,
    orchestrator_backend: CodingAgentBackend | None,
) -> dict[str, BackendCapabilities]:
    """Build a backend-name → BackendCapabilities map for the static recipe rule.

    Uses the supplied orchestrator backend as the authority for its own name and
    resolves every distinct per-step backend via get_backend(). Invalid names are
    surfaced so admission cannot silently skip hard-capability diagnostics.
    """
    from autoskillit.server._misc import get_backend  # circular-break

    out: dict[str, BackendCapabilities] = {}
    if orchestrator_backend is not None:
        orchestrator_name = orchestrator_backend.name
        if isinstance(orchestrator_name, str) and orchestrator_name:
            out[orchestrator_name] = orchestrator_backend.capabilities

    for step_name, backend_name in (effective_backend_map or {}).items():
        if not isinstance(backend_name, str) or not backend_name:
            raise ValueError(
                f"effective backend for step {step_name!r} must be a non-empty string"
            )
        if backend_name not in out:
            out[backend_name] = get_backend(backend_name).capabilities
    return out


def response_backstop_tool_meta(
    tool_name: str, *, always_load: bool = False
) -> dict[str, bool | int | str]:
    """Build transport metadata from the measured exemption authority."""
    definition = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY[tool_name]
    metadata: dict[str, bool | int | str] = {
        "anthropic/maxResultSizeChars": definition.max_chars,
        "autoskillit/responseBackstopMeasurement": definition.measurement_id,
        "autoskillit/responseBackstopMaxUtf8Bytes": definition.max_utf8_bytes,
        "autoskillit/responseBackstopRegistryDigest": (
            RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
        ),
    }
    if always_load:
        metadata["anthropic/alwaysLoad"] = True
    return metadata


def render_served_response(payload: dict[str, Any]) -> str:
    """Render the authoritative pre-backstop response used by recipe serve tools."""
    return json.dumps(payload)


def build_open_kitchen_recipe_payload(result: dict[str, Any], *, version: str) -> dict[str, Any]:
    """Add the routing fields shared by every recipe-bearing open-kitchen response."""
    payload = dict(result)
    payload.update(success=True, kitchen="open", version=version)
    if not payload.get("ingredients_table"):
        payload["ingredients_table"] = None
    return payload


def _build_serve_override_stack(
    ctx: ToolContext,
    caller_overrides: dict[str, str] | None,
    *,
    config_default: dict[str, str],
    session_overrides: dict[str, str],
    config_layer: dict[str, str],
    backend_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Single source of truth for serve override stack construction.

    Priority (highest wins): config_layer > backend_overrides > caller_overrides
    > snapshot_baseline > session_overrides > config_default
    """
    snapshot_baseline: dict[str, str] = {}
    if ctx.session_serve_overrides is not None:
        snapshot_baseline = dict(ctx.session_serve_overrides)
    return {
        **config_default,
        **session_overrides,
        **snapshot_baseline,
        **(caller_overrides or {}),
        **(backend_overrides or {}),
        **config_layer,
    }


def _resolve_serve_defer_unresolved(
    ctx: ToolContext,
    caller_overrides: dict[str, str] | None,
) -> bool:
    """Resolve defer_unresolved from session snapshot or compute fresh."""
    if ctx.session_serve_overrides is not None:
        return ctx.session_serve_defer_unresolved
    return not bool(caller_overrides)


def reset_session_serve_overrides(ctx: ToolContext) -> None:
    """Clear the session serve-overrides snapshot on ctx.

    Called by the test _reset_mcp_tags autouse fixture to prevent snapshot
    state from leaking between tests on the same xdist worker.  In production,
    _close_kitchen_handler performs the equivalent reset.
    """
    ctx.session_serve_overrides = None
    ctx.session_serve_defer_unresolved = False


def serve_recipe(
    ctx: ToolContext,
    name: str,
    caller_overrides: dict[str, str] | None,
    *,
    config_default: dict[str, str],
    session_overrides: dict[str, str],
    config_layer: dict[str, str],
    backend_overrides: dict[str, str] | None = None,
    ingredients_only: bool = False,
    resolved_defaults: dict[str, str] | None = None,
    effective_backend_map: dict[str, str] | None = None,
    suppressed: list[str] | None = None,
    backend_name: str | None = None,
    temp_dir: Path | str | None = None,
    temp_dir_relpath: str | None = None,
    backend_capabilities_map: dict[str, BackendCapabilities] | None = None,
    backend_origin_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Unified recipe serve path. Only legal caller of load_and_validate in server/tools/."""
    ingredient_overrides = _build_serve_override_stack(
        ctx,
        caller_overrides,
        config_default=config_default,
        session_overrides=session_overrides,
        config_layer=config_layer,
        backend_overrides=backend_overrides,
    )
    defer_unresolved = _resolve_serve_defer_unresolved(ctx, caller_overrides)
    kwargs: dict[str, Any] = {
        "ingredient_overrides": ingredient_overrides,
        "defer_unresolved": defer_unresolved,
    }
    if resolved_defaults is not None:
        kwargs["resolved_defaults"] = resolved_defaults
    if effective_backend_map is not None:
        kwargs["effective_backend_map"] = effective_backend_map
    if backend_capabilities_map is not None:
        kwargs["backend_capabilities_map"] = backend_capabilities_map
    if backend_origin_map is not None:
        kwargs["backend_origin_map"] = backend_origin_map
    if suppressed is not None:
        kwargs["suppressed"] = suppressed
    if backend_name is not None:
        kwargs["backend_name"] = backend_name
    if ingredients_only:
        kwargs["ingredients_only"] = ingredients_only
    if temp_dir is not None:
        kwargs["temp_dir"] = temp_dir
    if temp_dir_relpath is not None:
        kwargs["temp_dir_relpath"] = temp_dir_relpath
    if ctx.recipes is None:
        raise RuntimeError("serve_recipe() called with ctx.recipes=None")
    return ctx.recipes.load_and_validate(name, ctx.project_dir, **kwargs)


# ---------------------------------------------------------------------------
# Bounded envelope (Part B of #4304)
# ---------------------------------------------------------------------------


def _step_one_line_summary(step: RecipeStep) -> str:
    """Return a compact one-line summary of *step* for the envelope skeleton.

    Used by the open_kitchen / load_recipe integration paths to populate
    ``step_summaries`` before calling ``extract_step_skeleton``. Prefers
    an explicit ``description`` field; falls back to a 160-char head of
    the step's ``message`` or a tool/action signature. Always single-line.
    """
    desc = getattr(step, "description", "") or ""
    if desc.strip():
        return desc.strip().splitlines()[0][:160]
    msg = getattr(step, "message", None)
    if isinstance(msg, str) and msg.strip():
        return msg.strip().splitlines()[0][:160]
    tool = getattr(step, "tool", None)
    action = getattr(step, "action", None)
    if tool:
        return f"tool={tool}"
    if action:
        return f"action={action}"
    return ""


def _local_extract_routing_edges(step: Any) -> list[Any]:
    """Extract (edge_type, target) routing edges from a step without
    importing the recipe package at module level.

    Mirrors the production ``_extract_routing_edges`` from
    ``recipe/_analysis_graph.py`` for the fields the envelope skeleton
    needs: ``on_success``, ``on_failure``, ``on_context_limit``,
    ``on_rate_limit``, ``on_exhausted``, ``on_result.conditions[].route``,
    and ``on_result.routes``. Kept local to avoid a cross-package
    submodule import (REQ-ARCH-001). The recipe module is the canonical
    source of truth; this helper exists to keep the import graph clean.
    """
    edges: list[Any] = []

    for field_name, edge_type in (
        ("on_success", "success"),
        ("on_failure", "failure"),
        ("on_context_limit", "context_limit"),
        ("on_rate_limit", "rate_limit"),
        ("on_exhausted", "exhausted"),
    ):
        target = getattr(step, field_name, None)
        if target:
            edges.append(_LocalRouteEdge(edge_type=edge_type, target=target))

    on_result = getattr(step, "on_result", None)
    if on_result is not None:
        conditions = getattr(on_result, "conditions", None)
        if conditions:
            for cond in conditions:
                route = getattr(cond, "route", None)
                when = getattr(cond, "when", None)
                if route:
                    edges.append(
                        _LocalRouteEdge(edge_type="result_condition", target=route, condition=when)
                    )
        routes = getattr(on_result, "routes", None)
        if routes:
            for key, target in routes.items():
                if target:
                    edges.append(
                        _LocalRouteEdge(edge_type="result_condition", target=target, condition=key)
                    )
    return edges


class _LocalRouteEdge:
    """Lightweight substitute for ``RouteEdge`` from ``recipe/_analysis_graph.py``.

    Defined locally to keep ``_serve_helpers.py`` free of recipe-package
    imports (REQ-ARCH-001 cross-package submodule prohibition).
    """

    __slots__ = ("edge_type", "target", "condition")

    def __init__(self, edge_type: str, target: str, condition: str | None = None) -> None:
        self.edge_type = edge_type
        self.target = target
        self.condition = condition


def extract_step_skeleton(
    post_prune_step_names: list[str],
    routing_edges_by_step: dict[str, list[tuple[str, str]]],
    step_summaries: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a compact step-flow skeleton from parsed post-prune step data.

    The skeleton is a list of per-step dicts (name + one-line summary +
    outgoing routing edges). Combined with the byte-range index in the
    persisted artifact, the orchestrator can route without pulling a full
    step body, and pull-on-demand only the step it is about to execute.

    *post_prune_step_names* — order-preserving list of step names (from
        ``load_and_validate``'s ``post_prune_step_names`` result field).
    *routing_edges_by_step* — name → list of (edge_type, target) tuples
        derived from ``_extract_routing_edges`` for each step.
    *step_summaries* — optional name → one-line summary override.
    """
    skeleton: list[dict[str, Any]] = []
    for name in post_prune_step_names:
        edges = routing_edges_by_step.get(name) or []
        summary = (step_summaries or {}).get(name) or ""
        skeleton.append(
            {
                "name": name,
                "summary": summary,
                "edges": [
                    {"type": edge_type, "target": target} for edge_type, target in edges if target
                ],
            }
        )
    return {
        "step_count": len(skeleton),
        "steps": skeleton,
    }


def build_recipe_envelope(
    payload: dict[str, Any],
    *,
    artifact_path: str,
    artifact_sha256: str,
    skeleton: dict[str, Any],
    bound_bytes: int,
) -> dict[str, Any]:
    """Build a bounded envelope that fits the smallest backend delivery bound.

    The envelope is the orchestrator-visible replacement for the full
    recipe payload when the payload exceeds a backend's effective delivery
    token limit. It carries:

    - routing metadata (``success``, ``kitchen``, ``version``, ``valid``,
      ``dispatch_feasible``);
    - verbatim priority content (``orchestration_rules``,
      ``stop_step_semantics``, ``errors``, ``warnings``, ``hooks``);
    - ingredients schema (``ingredients_table``, ``suggestions``);
    - the step-flow skeleton (post-prune step names, summaries, routing
      edges) — enough for the orchestrator to route between steps without
      pulling a full step body;
    - the post-prune step list and routing edge list (so callers that
      only need the routing graph don't need to parse the skeleton);
    - a pull reference pointing to the deterministic artifact path
      (overwritten by every open_kitchen / load_recipe call) and the
      ``get_recipe_section`` MCP tool name.

    The envelope omits the full ``content`` field. The orchestrator pulls
    each step's body via ``get_recipe_section(section=<step_name>)`` at
    execution time, bounded to the delivery limit and chunked with a
    continuation token for steps larger than one chunk.

    ``bound_bytes`` is the effective delivery byte ceiling (smallest
    backend bound × 4). Large string fields like ``orchestration_rules``
    are projected to fit alongside the skeleton + pull reference; the
    full content lives only in the persisted artifact.
    """
    envelope: dict[str, Any] = {}
    envelope_bytes = 0
    serialized = json.dumps(payload.get("success", True)).encode("utf-8")
    envelope_bytes += len(serialized)
    envelope["success"] = payload.get("success", True)
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
            envelope_bytes += len(json.dumps({key: payload[key]}, ensure_ascii=False))

    # Fixed-size overhead: skeleton JSON + pull reference + delivery_bound_spill.
    skeleton_overhead = len(
        json.dumps({"step_flow_skeleton": skeleton}, ensure_ascii=False).encode("utf-8")
    )
    pull_overhead = len(
        json.dumps(
            {
                "recipe_pull": {
                    "artifact_path": artifact_path,
                    "sha256": artifact_sha256,
                    "pull_tool": "get_recipe_section",
                },
                "delivery_bound_spill": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )

    remaining = max(0, bound_bytes - skeleton_overhead - pull_overhead - envelope_bytes)

    def _project_string(key: str) -> None:
        nonlocal remaining
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            return
        key_overhead = len(json.dumps({key: ""}, ensure_ascii=False).encode("utf-8"))
        alloc = max(0, remaining - key_overhead)
        if len(value.encode("utf-8")) <= alloc:
            envelope[key] = value
            remaining -= len(value.encode("utf-8"))
        else:
            envelope[key] = value[:alloc]
            remaining = 0

    for key in ("orchestration_rules", "stop_step_semantics"):
        _project_string(key)

    # ingredients_table and suggestions are deprioritized — serialize them
    # only if budget allows; otherwise omit. The orchestrator can pull the
    # full ingredients_table via ``get_recipe_section(section="ingredients_table")``.
    for key in ("ingredients_table", "suggestions"):
        value = payload.get(key)
        if value is None:
            continue
        serialized_value = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(serialized_value) + 32 <= remaining:
            envelope[key] = value
            remaining -= len(serialized_value) + 32

    envelope["step_flow_skeleton"] = skeleton
    envelope["recipe_pull"] = {
        "artifact_path": artifact_path,
        "sha256": artifact_sha256,
        "pull_tool": "get_recipe_section",
    }
    envelope["delivery_bound_spill"] = True
    return envelope


def persist_recipe_artifact(
    artifact_dir: Path,
    *,
    tool_name: str,
    recipe_name: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    """Atomically persist the full recipe payload to the deterministic path.

    Returns (artifact_path, sha256) for inclusion in the envelope's
    ``recipe_pull`` block. Uses ``atomic_write`` so concurrent open_kitchen
    / load_recipe calls do not see a half-written file.

    The path is deterministic per (tool, recipe_name) so the pull tool
    can reconstruct it from ``tool_ctx.recipe_name`` without the caller
    having to thread it through every surface. Re-opening the same recipe
    overwrites the artifact (idempotent).
    """
    from autoskillit.server._response_budget import _recipe_artifact_path  # circular-break

    path = _recipe_artifact_path(artifact_dir, tool_name, recipe_name)
    serialized = json.dumps(payload, ensure_ascii=False)
    atomic_write(path, serialized)
    sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return str(path.resolve()), sha256


def build_step_summaries(active_recipe_steps: Any) -> dict[str, str]:
    """Build a {step_name: one_line_summary} dict from the parsed Recipe.steps.

    Falls back to an empty string per step if the parsed structure is
    unavailable (e.g. the recipe was loaded but post-prune filtering
    stripped the steps out, or the serve path passed no Recipe object).
    """
    if not isinstance(active_recipe_steps, dict) or not active_recipe_steps:
        return {}
    summaries: dict[str, str] = {}
    for name, step in active_recipe_steps.items():
        if not isinstance(name, str) or not name:
            continue
        summaries[name] = _step_one_line_summary(step)
    return summaries


def build_routing_edges_by_step(
    active_recipe_steps: Any,
    *,
    edge_extractor: Any,
) -> dict[str, list[tuple[str, str]]]:
    """Build a {step_name: [(edge_type, target), ...]} dict via edge_extractor.

    ``edge_extractor`` is the existing ``_extract_routing_edges`` callable
    from ``recipe/_analysis_graph.py``. We avoid importing it here to keep
    the helper importable from lightweight contexts; the caller passes the
    callable in. Steps whose extractor returns nothing map to an empty
    list (not omitted) so callers don't have to ``.get(name) or []``.
    """
    if not isinstance(active_recipe_steps, dict) or not active_recipe_steps:
        return {}
    edges_by_step: dict[str, list[tuple[str, str]]] = {}
    for name, step in active_recipe_steps.items():
        if not isinstance(name, str) or not name:
            continue
        try:
            extracted = edge_extractor(step) if edge_extractor is not None else []
        except Exception:
            get_logger(__name__).warning(
                "build_routing_edges_by_step_extractor_failed",
                step_name=name,
                exc_info=True,
            )
            extracted = []
        edges_by_step[name] = [
            (edge.edge_type, edge.target)
            for edge in (extracted or [])
            if getattr(edge, "target", None)
        ]
    return edges_by_step


def maybe_envelope_recipe_response(
    payload: dict[str, Any],
    *,
    tool_name: str,
    recipe_name: str,
    tool_ctx: ToolContext,
    effective_delivery_token_limit: int | None,
) -> dict[str, Any]:
    """Conditionally replace a recipe payload with a bounded envelope.

    If the payload's estimated token count exceeds
    ``effective_delivery_token_limit``, persists the full payload to the
    deterministic artifact path and returns ``build_recipe_envelope(...)``
    so the orchestrator can pull each step's body on demand.

    Otherwise returns the payload unchanged (Claude backend path: the
    full payload fits inline; backward compatible).

    On persistence failure: returns the original payload unchanged —
    the caller is then subject to ``track_response_size``'s spill path
    (which is more permissive but loses the pull guarantee). This is
    a fail-open at the persistence layer; the spill path itself remains
    fail-closed for shape violations.
    """
    if effective_delivery_token_limit is None or effective_delivery_token_limit <= 0:
        return payload

    serialized = json.dumps(payload, ensure_ascii=False)
    estimated_tokens = (len(serialized.encode("utf-8")) + 3) // 4
    if estimated_tokens <= effective_delivery_token_limit:
        return payload

    artifact_dir = getattr(tool_ctx, "temp_dir", None)
    if not isinstance(artifact_dir, Path):
        return payload

    post_prune = payload.get("post_prune_step_names") or []
    if not isinstance(post_prune, list):
        post_prune = []

    active_recipe_steps = getattr(tool_ctx, "active_recipe_steps", None)
    summaries = build_step_summaries(active_recipe_steps)

    try:
        edges = build_routing_edges_by_step(
            active_recipe_steps, edge_extractor=_local_extract_routing_edges
        )
    except Exception:
        get_logger(__name__).warning(
            "maybe_envelope_routing_edges_failed",
            recipe_name=recipe_name,
            exc_info=True,
        )
        edges = {}

    skeleton = extract_step_skeleton(
        [str(n) for n in post_prune if isinstance(n, str)],
        edges,
        summaries,
    )

    try:
        artifact_path, sha256 = persist_recipe_artifact(
            artifact_dir,
            tool_name=tool_name,
            recipe_name=recipe_name,
            payload=payload,
        )
    except OSError:
        return payload

    bound_bytes = effective_delivery_token_limit * 4
    return build_recipe_envelope(
        payload,
        artifact_path=artifact_path,
        artifact_sha256=sha256,
        skeleton=skeleton,
        bound_bytes=bound_bytes,
    )
