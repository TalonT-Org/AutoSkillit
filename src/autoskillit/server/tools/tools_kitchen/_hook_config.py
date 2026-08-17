"""Hook config snapshot writer and payload builders for subprocess bridge.

Writes ``.autoskillit/temp/.hook_config.json`` for hook subprocesses so they
can apply user settings without importing the full autoskillit package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict

from autoskillit.core import atomic_write
from autoskillit.server._misc import _hook_config_path
from autoskillit.server.tools._overlay_state import OverlayStateError, read_overlay

if TYPE_CHECKING:
    from autoskillit.config.settings import OutputBudgetConfig, QuotaGuardConfig

_PR_CREATE_RECIPES: frozenset[str] = frozenset(
    {"merge-prs", "implementation", "implementation-groups", "remediation"}
)


class QuotaGuardHookPayload(TypedDict):
    cache_max_age: int
    cache_path: str
    buffer_seconds: int
    disabled: bool


class OutputBudgetPolicyHookPayload(TypedDict):
    disabled: bool
    shell_max_inline_bytes: int
    capture_capacity: NotRequired[dict[str, int]]


def _quota_guard_hook_payload(cfg: QuotaGuardConfig) -> QuotaGuardHookPayload:
    """Return the quota_guard section of .hook_config.json for a given config.

    This is the single authoritative definition of which QuotaGuardConfig fields
    cross the stdlib-only boundary into hook subprocesses. When adding a field to
    QuotaHookSettings, add the corresponding source field here AND update
    QUOTA_GUARD_HOOK_PAYLOAD_KEYS in _hook_settings.py. The contract test
    test_hook_bridge_coverage.py enforces that both stay in sync.
    """
    return {
        "cache_max_age": cfg.cache_max_age,
        "cache_path": cfg.cache_path,
        "buffer_seconds": cfg.buffer_seconds,
        "disabled": not cfg.enabled,
    }


def _output_budget_policy_hook_payload(
    cfg: OutputBudgetConfig,
) -> OutputBudgetPolicyHookPayload:
    """Return the output-budget guard section of ``.hook_config.json``.

    Keep these keys in sync with ``OUTPUT_BUDGET_POLICY_HOOK_PAYLOAD_KEYS``
    in the stdlib-only hook settings bridge.
    """
    payload: OutputBudgetPolicyHookPayload = {
        "disabled": not cfg.guard_enabled,
        "shell_max_inline_bytes": cfg.shell_max_inline_bytes,
    }
    if cfg.capture_capacity is not None:
        payload["capture_capacity"] = cfg.capture_capacity
    return payload


def _write_hook_config() -> None:
    """Write hook policy snapshots to .autoskillit/temp/.hook_config.json.

    Hook subprocesses read this file to apply user settings without importing
    the autoskillit package.
    """
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    response_temp_root = (
        ctx.temp_dir
        if isinstance(getattr(ctx, "temp_dir", None), Path)
        else ctx.project_dir / ".autoskillit" / "temp"
    )
    payload = {
        "quota_guard": _quota_guard_hook_payload(ctx.config.quota_guard),
        "output_budget_policy": _output_budget_policy_hook_payload(ctx.config.output_budget),
        "response_temp_root": str(response_temp_root.resolve()),
        "kitchen_id": ctx.kitchen_id,
        "git_ops_policy": {},
    }
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    try:
        hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_write_failed", path=str(hook_cfg_path))


def _update_hook_config_with_recipe() -> None:
    """Enrich .hook_config.json with recipe-level authorization after recipe loading."""
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    try:
        payload = json.loads(hook_cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("hook_config_recipe_update_read_failed", path=str(hook_cfg_path))
        return
    if ctx.recipe_name in _PR_CREATE_RECIPES:
        payload["recipe_allows_pr_create"] = True
    try:
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_recipe_update_write_failed", path=str(hook_cfg_path))


def _update_hook_config_with_git_ops_policy() -> None:
    """Propagate recipe-level git_ops_policy overlay to .hook_config.json.

    Reads the overlay from the hook config overlay file and merges it into the
    base config's git_ops_policy dict. Currently no recipe sets this; the
    mechanism exists for future recipes that legitimately need destructive git ops
    (e.g. allow_push for a release automation recipe).
    """
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    try:
        payload = json.loads(hook_cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("hook_config_git_ops_policy_update_read_failed", path=str(hook_cfg_path))
        return
    git_ops_policy: dict = payload.get("git_ops_policy", {})
    try:
        overlay_policy = read_overlay(ctx.project_dir).get("git_ops_policy", {})
    except (OSError, OverlayStateError):
        logger.warning("hook_config_git_ops_policy_overlay_invalid", exc_info=True)
        return
    if overlay_policy:
        git_ops_policy = {**git_ops_policy, **overlay_policy}
    payload["git_ops_policy"] = git_ops_policy
    try:
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_git_ops_policy_update_write_failed", path=str(hook_cfg_path))
