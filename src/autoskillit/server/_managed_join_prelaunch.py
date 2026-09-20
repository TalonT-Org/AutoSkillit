"""Mint managed-join adaptation evidence before catalog exposure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import CodingAgentBackend, SemanticAdaptationContext
from autoskillit.execution.backends._codex_hooks import managed_codex_route_digest
from autoskillit.hook_registry import HOOK_REGISTRY_HASH
from autoskillit.server._managed_join_attestation import (
    DefaultManagedJoinAttestationAuthority,
    ManagedJoinRecordStore,
)


@dataclass(frozen=True, slots=True)
class ManagedJoinIssuanceRefusal:
    """A recoverable reason a launch cannot use the managed join route."""

    reason: str


def render_managed_join_refusal(refusal: ManagedJoinIssuanceRefusal) -> str:
    """Render one operator-visible managed-join issuance refusal."""
    return f"managed join issuance refused: {refusal.reason}"


def prepare_managed_join_context(
    *,
    backend: CodingAgentBackend,
    configured_model: str,
    state_root: Path,
    parent_id: str,
    launch_context: str,
) -> SemanticAdaptationContext | ManagedJoinIssuanceRefusal:
    """Issue persisted managed-join evidence from trusted launch inputs."""
    if not backend.capabilities.managed_fixed_batch_route_capable:
        return ManagedJoinIssuanceRefusal(
            reason=f"backend {backend.name!r} has no managed fixed-batch route"
        )
    try:
        resolve_identity = getattr(backend, "resolve_managed_parent_identity")
        project_catalog = getattr(backend, "project_source_catalog")
        model, effort = resolve_identity(configured_model)
        projection = project_catalog(model, effort)
        return DefaultManagedJoinAttestationAuthority(
            record_store=ManagedJoinRecordStore(state_root),
            backend=backend,
        ).issue(
            backend=backend.name,
            launch_context=launch_context,
            parent_session_id=parent_id,
            direct_tool_mode=True,
            resolved_model=model,
            resolved_reasoning_effort=effort,
            codex_catalog_digest=projection.projected_sha256.removeprefix("sha256:"),
            fixed_batch_tool_registry_digest=managed_codex_route_digest(),
            hook_registry_digest=HOOK_REGISTRY_HASH,
            skill_load_applies=True,
            guards_apply=True,
        )
    except (OSError, ValueError) as exc:
        return ManagedJoinIssuanceRefusal(reason=str(exc))
