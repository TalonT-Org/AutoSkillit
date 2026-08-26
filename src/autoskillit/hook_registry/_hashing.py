"""Canonical registry payload + sha256 hash computation.

The hash input is the JSON serialization produced by
``_canonical_registry_payload``; the JSON shape is the single authority for
"what counts as the canonical registry" for every publisher (marketplace,
self-heal, projection staging, startup drift check). Adding or removing a
field here changes the committed ``registry.sha256`` and must be paired
with a ``task sync-hooks-hash`` re-run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from ._hooks_defs import HookDef, LifecycleContractDef


def _canonical_registry_payload(
    registry: Sequence[HookDef],
    retired: frozenset[str],
    lifecycle_contracts: Sequence[LifecycleContractDef],
) -> str:
    registry_rows = sorted(
        [
            {
                "codex_status": h.codex_status,
                "enforcement_strength": dict(sorted(h.enforcement_strength.items())),
                "event_type": h.event_type,
                "exempt_session_types": sorted(h.exempt_session_types),
                "exempt_skills": sorted(h.exempt_skills),
                "matcher": h.matcher,
                "mechanism": h.mechanism,
                "produces_resources": sorted(h.produces_resources),
                "reclaims_resources": sorted(h.reclaims_resources),
                "scripts": list(h.scripts),
                "self_reclaims_resources": sorted(h.self_reclaims_resources),
                "session_scope": h.session_scope,
                "timeout_seconds": h.timeout_seconds,
            }
            for h in registry
        ],
        key=lambda row: (row["event_type"], row["matcher"], tuple(row["scripts"])),  # type: ignore[arg-type]
    )
    lifecycle_rows = sorted(
        [
            {
                "backend": contract.backend,
                "producer_script": contract.producer_script,
                "required_owner_roles": sorted(contract.required_owner_roles),
                "resource": contract.resource,
                "session_scope": contract.session_scope,
            }
            for contract in lifecycle_contracts
        ],
        key=lambda row: (
            row["resource"],
            row["producer_script"],
            row["backend"],
            row["session_scope"],
        ),
    )
    return json.dumps(
        {
            "format_version": 4,
            "lifecycle_contracts": lifecycle_rows,
            "registry": registry_rows,
            "retired": sorted(retired),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_registry_hash(
    registry: Sequence[HookDef],
    retired: frozenset[str],
    lifecycle_contracts: Sequence[LifecycleContractDef],
) -> str:
    """Compute a stable sha256 over the hook and lifecycle registries."""
    payload = _canonical_registry_payload(registry, retired, lifecycle_contracts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ``HOOK_REGISTRY_HASH`` is computed in the package ``__init__.py`` after
# ``HOOK_REGISTRY`` is populated (the cycle through ``autoskillit.hooks``
# prevents computing it here at module load time).
