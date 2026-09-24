"""Typed backend-authority composition root for interactive CLI sessions."""

from __future__ import annotations

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    CodexRuntimeSpec,
    CodingAgentBackend,
    LaunchResolver,
    ManagedSessionHome,
    managed_route_backend,
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


def verify_launch_home(
    backend: CodingAgentBackend, managed_home: ManagedSessionHome | None
) -> None:
    """Refuse a launch when its projected home no longer matches its attestation."""
    if managed_home is None:
        return
    projection = managed_home.managed_projection
    if projection is None:
        return
    managed = managed_route_backend(backend)
    if managed is None:
        raise ValueError(
            "managed generated home was projected for a backend without managed routes"
        )
    errors = managed.verify_managed_session_dir(
        managed_home.generated_home, projection.attestation, projection.route
    )
    if errors:
        raise ValueError(
            "managed generated home no longer matches its attestation: "
            + "; ".join(errors)
            + "; restart the AutoSkillit session to re-attest"
        )
