"""Subset/feature tool-visibility reconciliation after enabling the kitchen."""

from __future__ import annotations

import inspect

from fastmcp import Context

from autoskillit.server.tools import tools_kitchen as _tk_pkg


async def _redisable_subsets(
    ctx: Context,
    disabled: list[str],
    features: dict[str, bool] | None = None,
    *,
    experimental_enabled: bool = False,
) -> None:
    """Re-disable subset-tagged and feature-disabled tools after enabling kitchen.

    Pass 1 (existing): Re-disable config-disabled subset tags so dual-tagged tools
    (e.g. kitchen+github) that are server-disabled are not accidentally revealed.

    Pass 2: Suppress tool tags for disabled features via `_collect_disabled_feature_tags`.
    Shared tools with kitchen-core retain visibility via the kitchen-core tag
    (FastMCP union model).

    ``features`` defaults to ``None`` (treated as ``{}``, i.e. all features use
    ``FeatureDef.default_enabled``). Pass ``config.features`` from the call site.
    """

    async def _disable_tag(tag: str) -> None:
        result = ctx.disable_components(tags={tag})
        if inspect.isawaitable(result):
            await result

    # Pass 1: subset re-disable (existing)
    for subset in disabled:
        await _disable_tag(subset)

    # Pass 2: feature gate — suppress tool tags for disabled features
    _features = features or {}
    for tag in _tk_pkg._collect_disabled_feature_tags(
        _features, experimental_enabled=experimental_enabled
    ):
        await _disable_tag(tag)
