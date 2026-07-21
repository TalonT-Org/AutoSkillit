"""Unified serve-pipeline helpers.

The only legal call site for load_and_validate in server/tools/.
All five serve surfaces (open_kitchen normal, open_kitchen deferred-recall,
load_recipe, get_recipe, get_recipe_section) must call serve_recipe() instead
of calling ctx.recipes.load_and_validate() directly. This structural invariant
is enforced by tests/arch/test_serve_surface_registry.py.
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
    resolve_effective_delivery_bound,
)
from autoskillit.execution import resolve_worst_case_delivery_bound
from autoskillit.server._response_budget import (
    _artifact_path,
    build_recipe_envelope,
    extract_step_routing,
)

if TYPE_CHECKING:
    from autoskillit.core import BackendCapabilities, CodingAgentBackend
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


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


def _safe_backend_name(tool_ctx: Any) -> str | None:
    """Return ``tool_ctx.backend.name`` if a backend is set, else ``None``.

    Helper factored out of the recipe_artifact_state builder so pyright can
    resolve ``backend.name`` against the typed ``CodingAgentBackend`` rather
    than the Optional ``Any`` returned by ``getattr(..., None)``.
    """
    backend = getattr(tool_ctx, "backend", None)
    if backend is None:
        return None
    name_attr = getattr(backend, "name", None)
    return name_attr if isinstance(name_attr, str) else None


def resolve_envelope_delivery_bound(tool_ctx: Any) -> int:
    """Resolve the envelope construction-time bound in bytes.

    Mirrors ``track_response_size.wrapper``'s resolution so the envelope is
    constructed against the same gate that enforcement applies. Backend
    capabilities are preferred; falls back to the smallest registered backend
    bound (worst case) when capabilities are unavailable or non-positive.
    """
    backend = getattr(tool_ctx, "backend", None)
    caps = getattr(backend, "capabilities", None) if backend is not None else None
    token_limit: int | None = None
    if caps is not None:
        try:
            token_limit = resolve_effective_delivery_bound(caps)
        except Exception:  # noqa: BLE001
            logger.warning("resolve_effective_delivery_bound_failed", exc_info=True)
            token_limit = None
    # Coerce to int; MagicMock or non-numeric values fall through to the
    # conservative default so envelope construction never crashes on a
    # misconfigured backend (e.g., a test mock with a MagicMock capabilities).
    if not isinstance(token_limit, int) or token_limit <= 0:
        try:
            fallback = resolve_worst_case_delivery_bound()
        except Exception:  # noqa: BLE001
            logger.warning("resolve_worst_case_delivery_bound_failed", exc_info=True)
            fallback = 0
        if isinstance(fallback, int) and fallback > 0:
            token_limit = fallback
        else:
            token_limit = 10_000
    return token_limit * 4


def persist_recipe_artifact(
    *,
    tool_ctx: Any,
    tool_name: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    """Persist the full recipe payload and return (artifact_path, sha256).

    Uses the deterministic path scheme from ``_response_budget._artifact_path``
    so the persisted file is stable across calls with the same content.
    """
    artifact_dir = tool_ctx.temp_dir / "responses" / tool_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=False)
    payload_bytes = serialized.encode("utf-8")
    content_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    path = _artifact_path(artifact_dir, tool_name, content_sha256)
    atomic_write(path, serialized)
    return str(path.resolve()), content_sha256


def build_and_record_recipe_envelope(
    *,
    tool_ctx: Any,
    tool_name: str,
    payload: dict[str, Any],
    result: dict[str, Any],
    kitchen_label: str,
    version: str,
    overrides: dict[str, str] | None,
    recipe_name: str,
    ingredients_only: bool,
) -> dict[str, Any]:
    """Persist the full artifact and build the compact envelope in one call.

    Populates ``ctx.recipe_artifact_state`` with the artifact_path, sha256,
    and the recipe-load parameters ``get_recipe_section`` needs to recreate
    the artifact if it is later missing from disk. Returns the envelope dict
    so the caller can serialize via ``render_served_response``.
    """
    artifact_path, content_sha256 = persist_recipe_artifact(
        tool_ctx=tool_ctx,
        tool_name=tool_name,
        payload=payload,
    )
    post_prune_step_names_raw = result.get("post_prune_step_names", [])
    post_prune_step_names: list[str] = (
        list(post_prune_step_names_raw) if isinstance(post_prune_step_names_raw, list) else []
    )
    content_text = result.get("content", "")
    step_flow_skeleton = (
        extract_step_routing(content_text or "", post_prune_step_names)
        if not ingredients_only and isinstance(content_text, str)
        else []
    )
    step_index = {step_name: f"step:{step_name}" for step_name in post_prune_step_names}
    tool_ctx.recipe_artifact_state = {
        "artifact_path": artifact_path,
        "sha256": content_sha256,
        "tool_name": tool_name,
        "recipe_name": recipe_name,
        "ingredient_overrides": dict(overrides) if overrides else {},
        "backend_name": _safe_backend_name(tool_ctx),
        "kitchen_id": getattr(tool_ctx, "kitchen_id", ""),
    }
    envelope_bound = resolve_envelope_delivery_bound(tool_ctx)
    return build_recipe_envelope(
        result,
        artifact_path=artifact_path,
        sha256=content_sha256,
        bound=envelope_bound,
        success=True,
        kitchen=kitchen_label,
        version=version,
        step_flow_skeleton=step_flow_skeleton,
        step_index=step_index,
    )


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
