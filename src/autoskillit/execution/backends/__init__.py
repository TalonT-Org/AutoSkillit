"""Backend registry and re-exports."""

from __future__ import annotations

from .claude import (
    ClaudeCodeBackend,
    ClaudeEnvPolicy,
    ClaudeResultParser,
    ClaudeSessionLocator,
    ClaudeStreamParser,
)

BACKEND_REGISTRY: dict[str, type] = {
    "claude-code": ClaudeCodeBackend,
}


def get_backend(name: str) -> ClaudeCodeBackend:
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
    "get_backend",
]
