from __future__ import annotations

from autoskillit.core import CodingAgentBackend

from ._codex_config import (
    _is_autoskillit_registered,
    _read_codex_config,
    _write_codex_config,
    ensure_codex_mcp_registered,
)
from ._codex_parse import CodexResultParser, CodexStreamParser
from .claude import (
    ClaudeCodeBackend,
    ClaudeEnvPolicy,
    ClaudeResultParser,
    ClaudeSessionLocator,
    ClaudeStreamParser,
)
from .codex import (
    CodexBackend,
    CodexEnvPolicy,
    CodexFlags,
    CodexSessionLocator,
)
from .codex_scenario_player import (
    CodexScenarioPlayer,
    make_codex_scenario_player,
)

BACKEND_REGISTRY: dict[str, type[CodingAgentBackend]] = {
    "claude-code": ClaudeCodeBackend,  # type: ignore[dict-item]
    "codex": CodexBackend,  # type: ignore[dict-item]
}


def get_backend(name: str) -> CodingAgentBackend:
    try:
        cls = BACKEND_REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(BACKEND_REGISTRY))
        msg = f"Unknown backend {name!r}. Valid names: {valid}"
        raise ValueError(msg) from None
    return cls()


__all__ = [
    "BACKEND_REGISTRY",
    "ClaudeCodeBackend",
    "ClaudeEnvPolicy",
    "ClaudeResultParser",
    "ClaudeSessionLocator",
    "ClaudeStreamParser",
    "CodexBackend",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexResultParser",
    "CodexScenarioPlayer",
    "CodexSessionLocator",
    "CodexStreamParser",
    "_is_autoskillit_registered",
    "_read_codex_config",
    "_write_codex_config",
    "ensure_codex_mcp_registered",
    "get_backend",
    "make_codex_scenario_player",
]
