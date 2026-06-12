"""Exception types for recipe loading failures."""

from __future__ import annotations

__all__ = [
    "CapabilityNotSupportedError",
    "RecipeLoadError",
    "ProcessStaleError",
    "RecipeNotFoundError",
]


class RecipeLoadError(Exception):
    """Base exception for load_and_validate failures."""


class ProcessStaleError(RecipeLoadError):
    """MCP server process is running stale code — restart required."""


class RecipeNotFoundError(RecipeLoadError):
    """Named recipe could not be found in any scan directory."""


class CapabilityNotSupportedError(Exception):
    """Backend does not support the requested capability."""

    def __init__(self, capability: str, backend_name: str) -> None:
        self.capability = capability
        self.backend_name = backend_name
        super().__init__(f"{backend_name!r} does not support capability {capability!r}")
