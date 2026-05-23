"""Exception types for recipe loading failures."""

from __future__ import annotations

__all__ = [
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
