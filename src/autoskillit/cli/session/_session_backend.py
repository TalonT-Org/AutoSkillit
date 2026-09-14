"""Typed backend-authority composition root for interactive CLI sessions."""

from __future__ import annotations

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    CodexRuntimeSpec,
    CodingAgentBackend,
    LaunchResolver,
)
from autoskillit.execution import DefaultLaunchResolver


def resolve_global_backend(
    backend_name: str,
    *,
    launch_resolver: LaunchResolver | None = None,
    codex_runtime_spec: CodexRuntimeSpec | None = None,
) -> CodingAgentBackend:
    """Resolve the configured global backend through one typed authority boundary."""
    resolver = launch_resolver or DefaultLaunchResolver(codex_runtime_spec=codex_runtime_spec)
    return resolver.backend_for_authority(
        BackendAuthority(
            backend=backend_name,
            kind=BackendAuthorityKind.GLOBAL,
            tier=BackendAuthorityTier.GLOBAL,
            key_path="agent_backend.backend",
        )
    )
