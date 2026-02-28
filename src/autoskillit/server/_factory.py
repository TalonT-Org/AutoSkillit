"""Composition Root: make_context() is the only location that legally instantiates
all 10 service contracts simultaneously.

server/ is L3 — the only layer permitted to import from both L1 (pipeline/)
and L2 (recipe/, migration/) at the same time. This module is the canonical
factory for wiring a fully-populated ToolContext, replacing the ad-hoc
construction scattered across callers.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.config import AutomationConfig
from autoskillit.core.types import SubprocessRunner
from autoskillit.pipeline.audit import AuditLog
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.gate import GateState
from autoskillit.pipeline.tokens import TokenLog


def _default_plugin_dir() -> str:
    """Resolve the autoskillit package root (parent of server/)."""
    return str(Path(__file__).parent.parent)


def make_context(
    config: AutomationConfig,
    *,
    runner: SubprocessRunner | None = None,
    plugin_dir: str | None = None,
) -> ToolContext:
    """Create a fully-wired ToolContext.

    This is the Composition Root — the only location that should instantiate
    all service fields simultaneously (AuditLog, TokenLog, GateState).

    Args:
        config: The loaded AutomationConfig (use load_config() to obtain it).
        runner: Subprocess runner implementation. Defaults to None (tests use
                MockSubprocessRunner; production sets RealSubprocessRunner).
        plugin_dir: Absolute path to the autoskillit plugin directory. Defaults
                    to the autoskillit package directory (parent of server/).

    Returns:
        ToolContext with gate starting closed (enabled=False). Call
        gate.enable() (via the open_kitchen prompt) to activate gated tools.
    """
    return ToolContext(
        config=config,
        audit=AuditLog(),
        token_log=TokenLog(),
        gate=GateState(enabled=False),
        plugin_dir=plugin_dir if plugin_dir is not None else _default_plugin_dir(),
        runner=runner,
    )
