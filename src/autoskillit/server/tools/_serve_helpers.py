"""Unified serve-pipeline helpers.

The only legal call site for load_and_validate in server/tools/.
All four serve surfaces (open_kitchen normal, open_kitchen deferred-recall,
load_recipe, get_recipe) must call serve_recipe() instead of calling
ctx.recipes.load_and_validate() directly. This structural invariant is
enforced by tests/arch/test_serve_surface_registry.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.core import BackendCapabilities, CodingAgentBackend
    from autoskillit.pipeline.context import ToolContext


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
