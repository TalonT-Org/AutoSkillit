from __future__ import annotations

from autoskillit.core import CodingAgentBackend

from ._codex_config import (
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

BACKEND_REGISTRY: dict[str, type[CodingAgentBackend]] = {
    "claude-code": ClaudeCodeBackend,
    "codex": CodexBackend,
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
    "CodexSessionLocator",
    "CodexStreamParser",
    "ensure_codex_mcp_registered",
    "get_backend",
]
