"""Server-authoritative ingredient override helpers.

Shared between sibling tool modules (tools_kitchen, tools_recipe) that need
to inject runtime-derived values into recipe ingredient overrides. Each
helper returns a plain dict suitable for merging into the
``ingredient_overrides`` keyword of ``load_and_validate``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend


def _backend_capability_overrides(backend: CodingAgentBackend | None) -> dict[str, str]:
    """Return ingredient overrides derived from backend capabilities.

    The ``backend_supports_git_write`` ingredient is resolved from the active
    backend's ``git_metadata_writable`` capability. A ``None`` backend is
    treated as writable (safe default for test/dev contexts where no backend
    is wired).
    """
    git_writable = backend is None or backend.capabilities.git_metadata_writable
    return {"backend_supports_git_write": "true" if git_writable else "false"}
