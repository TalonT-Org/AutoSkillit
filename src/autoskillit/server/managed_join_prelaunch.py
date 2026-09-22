"""Mint managed-join adaptation evidence before catalog exposure."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import CodingAgentBackend, SemanticAdaptationContext, get_logger

logger = get_logger(__name__)
from autoskillit.execution.backends import managed_codex_route_digest
from autoskillit.hook_registry import HOOK_REGISTRY_HASH
from autoskillit.server._managed_join_attestation import (
    DefaultManagedJoinAttestationAuthority,
    ManagedJoinRecordStore,
)


@dataclass(frozen=True, slots=True)
class ManagedJoinIssuanceRefusal:
    """A recoverable reason a launch cannot use the managed join route."""

    reason: str


ManagedJoinRefusalHandler = Callable[[ManagedJoinIssuanceRefusal], None]


def render_managed_join_refusal(refusal: ManagedJoinIssuanceRefusal) -> str:
    """Render one operator-visible managed-join issuance refusal."""
    return f"managed join issuance refused: {refusal.reason}"


@dataclass(frozen=True, slots=True)
class ManagedJoinEvidence:
    """Resolved managed-join evidence paired with its parent identity."""

    context: SemanticAdaptationContext
    parent_id: str


def acquire_managed_join_evidence(
    *,
    backend: CodingAgentBackend,
    configured_model: str,
    state_root: Path,
    parent_id: str,
    launch_context: str,
    on_refusal: ManagedJoinRefusalHandler | None = None,
) -> ManagedJoinEvidence | None:
    """Issue managed-join evidence, returning ``None`` when the backend refuses.

    The shared helper consolidates the launch-boundary boilerplate
    (``prepare_managed_join_context`` call + refusal handling) that
    previously appeared verbatim across every CLI and server launch path.
    ``on_refusal`` defaults to the operator-visible WARNING-print used by CLI
    launch paths; pass an alternative callable (e.g. a structlog adapter) for
    non-CLI callers.
    """
    issuance = prepare_managed_join_context(
        backend=backend,
        configured_model=configured_model,
        state_root=state_root,
        parent_id=parent_id,
        launch_context=launch_context,
    )
    if isinstance(issuance, ManagedJoinIssuanceRefusal):
        (on_refusal or _default_managed_join_refusal_handler)(issuance)
        return None
    return ManagedJoinEvidence(context=issuance, parent_id=parent_id)


def _default_managed_join_refusal_handler(refusal: ManagedJoinIssuanceRefusal) -> None:
    """Log a one-line operator-visible warning for a managed-join refusal."""
    logger.warning("managed_join_issuance_refused", reason=render_managed_join_refusal(refusal))


def prepare_managed_join_context(
    *,
    backend: CodingAgentBackend,
    configured_model: str,
    state_root: Path,
    parent_id: str,
    launch_context: str,
) -> SemanticAdaptationContext | ManagedJoinIssuanceRefusal:
    """Issue persisted managed-join evidence from trusted launch inputs."""
    if not getattr(backend.capabilities, "managed_fixed_batch_route_capable", False):
        return ManagedJoinIssuanceRefusal(
            reason=f"backend {backend.name!r} has no managed fixed-batch route"
        )
    try:
        resolve_identity = getattr(backend, "resolve_managed_parent_identity", None)
        project_catalog = getattr(backend, "project_source_catalog", None)
        if not callable(resolve_identity) or not callable(project_catalog):
            return ManagedJoinIssuanceRefusal(
                reason=f"backend {backend.name!r} cannot issue a managed Codex context"
            )
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
