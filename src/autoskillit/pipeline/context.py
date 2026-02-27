"""ToolContext: explicit dependency container for server tool implementations.

pipeline/ module — the only pipeline sub-module that imports from config/.
Replaces four mutable module-level singletons in server.py:
  _config, _tools_enabled, _audit_log, _token_log
"""

from __future__ import annotations

from dataclasses import dataclass

from autoskillit.config import AutomationConfig
from autoskillit.core.types import SubprocessRunner
from autoskillit.pipeline.audit import AuditLog
from autoskillit.pipeline.gate import GateState
from autoskillit.pipeline.tokens import TokenLog


@dataclass
class ToolContext:
    """Single dependency container threaded through all MCP tool implementations.

    Constructed once in cli.py serve() and injected into server.py via
    server._initialize(ctx). Tests construct isolated instances per-test
    to avoid global state leakage.

    Fields
    ------
    config:     AutomationConfig loaded from .autoskillit/config.yaml
    audit:      AuditLog instance for recording pipeline failures
    token_log:  TokenLog instance for per-step token tracking
    gate:       GateState (frozen) — replace with GateState(enabled=True/False) to toggle
    plugin_dir: Absolute path string to the autoskillit package directory
    runner:     SubprocessRunner implementation (RealSubprocessRunner in production,
                MockSubprocessRunner in tests)
    """

    config: AutomationConfig
    audit: AuditLog
    token_log: TokenLog
    gate: GateState
    plugin_dir: str
    runner: SubprocessRunner | None
