"""Risky-operation predicates and lifecycle-contract validation.

This module owns:

- ``RISKY_GH_SUBCOMMANDS`` / ``RISKY_GIT_OPERATIONS`` re-exports from
  ``autoskillit.hooks._hook_constants`` (the canonical authority). The
  re-exports preserve the historical ``autoskillit.hook_registry.RISKY_*``
  import path for every existing consumer (the values themselves were
  moved to ``_hook_constants`` in Step A1 so that guard scripts and the
  registry now share a single source of truth).
- ``hook_applies_to_backend`` — whether a HookDef is reachable for a given
  backend/session-scope pair.
- ``_contract_session_scopes`` — internal helper mapping a
  LifecycleContractDef's session_scope to the set of deployed session
  scopes the contract applies to.
- ``validate_lifecycle_contracts`` — fail-closed validation: every
  persistent resource produced by a reachable hook has exactly one
  cleanup owner; that owner's lifecycle metadata matches the contract;
  the producer is applicable on every scope it advertises; same-runner
  reclaim and SessionStart ownership obligations are satisfied.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from autoskillit.hooks._hook_constants import (  # noqa: F401
    RISKY_GH_SUBCOMMANDS,
    RISKY_GIT_OPERATIONS,
)

from ._hooks_defs import HookDef, LifecycleContractDef

# The RISKY_* constants are re-exported under their historical names so that
# every existing consumer (tests, contract checks, third-party scripts) keeps
# importing from ``autoskillit.hook_registry`` unchanged. The values themselves
# live in ``autoskillit.hooks._hook_constants`` (Step A1) — the single source
# of truth shared with the guard scripts.
__all__ = [
    "RISKY_GH_SUBCOMMANDS",
    "RISKY_GIT_OPERATIONS",
    "hook_applies_to_backend",
    "validate_lifecycle_contracts",
]


def hook_applies_to_backend(
    hook_def: HookDef,
    *,
    backend: Literal["claude_code", "codex"],
    session_scope: Literal["headless", "interactive"],
) -> bool:
    """Return whether a hook is reachable for one deployed backend/session scope."""
    if backend not in ("claude_code", "codex"):
        raise ValueError(f"unsupported hook backend: {backend!r}")
    if session_scope not in ("headless", "interactive"):
        raise ValueError(f"unsupported hook session scope: {session_scope!r}")
    match backend:
        case "codex":
            if hook_def.codex_status in {
                "fix-required",
                "not-applicable",
            }:
                return False
            return hook_def.session_scope == "any" or (
                session_scope == "headless"
                and hook_def.session_scope == "headless_only"
                or session_scope == "interactive"
                and hook_def.session_scope == "interactive_only"
            )
        case "claude_code":
            if hook_def.enforcement_strength.get("claude_code") == "not-applicable":
                return False
            return hook_def.session_scope == "any" or (
                session_scope == "headless"
                and hook_def.session_scope == "headless_only"
                or session_scope == "interactive"
                and hook_def.session_scope == "interactive_only"
            )


def _contract_session_scopes(
    contract: LifecycleContractDef,
) -> tuple[Literal["headless", "interactive"], ...]:
    if contract.session_scope == "headless_only":
        return ("headless",)
    if contract.session_scope == "interactive_only":
        return ("interactive",)
    return ("headless", "interactive")


def validate_lifecycle_contracts(
    registry: Sequence[HookDef],
    lifecycle_contracts: Sequence[LifecycleContractDef],
    *,
    backend: Literal["claude_code", "codex"],
) -> None:
    """Fail closed when a deployed producer loses a required cleanup owner."""
    contract_keys = {
        (contract.resource, contract.producer_script, contract.backend)
        for contract in lifecycle_contracts
    }
    for hook_def in registry:
        for resource in hook_def.produces_resources:
            reachable = hook_applies_to_backend(
                hook_def,
                backend=backend,
                session_scope="headless",
            ) or hook_applies_to_backend(
                hook_def,
                backend=backend,
                session_scope="interactive",
            )
            if reachable and not any(
                (resource, producer_script, backend) in contract_keys
                for producer_script in hook_def.scripts
            ):
                raise ValueError(f"persistent resource {resource!r} has no lifecycle contract")

    applicable_contracts = [
        contract for contract in lifecycle_contracts if contract.backend == backend
    ]
    for contract in applicable_contracts:
        producers = [
            hook_def
            for hook_def in registry
            if contract.producer_script in hook_def.scripts
            and contract.resource in hook_def.produces_resources
        ]
        if len(producers) != 1:
            raise ValueError(
                f"lifecycle producer {contract.producer_script!r} for "
                f"{contract.resource!r} must resolve exactly once"
            )
        producer = producers[0]
        if producer.session_scope != contract.session_scope:
            raise ValueError(
                f"lifecycle producer {contract.producer_script!r} scope "
                f"{producer.session_scope!r} does not match contract "
                f"{contract.session_scope!r}"
            )

        for session_scope in _contract_session_scopes(contract):
            if not hook_applies_to_backend(
                producer,
                backend=backend,
                session_scope=session_scope,
            ):
                raise ValueError(
                    f"lifecycle producer {contract.producer_script!r} is not applicable "
                    f"to {backend}/{session_scope}"
                )
            if "same_runner" in contract.required_owner_roles and not (
                contract.resource in producer.reclaims_resources
                and contract.resource in producer.self_reclaims_resources
            ):
                raise ValueError(
                    f"lifecycle resource {contract.resource!r} has no same-runner owner "
                    f"for {backend}/{session_scope}"
                )
            if "session_start" in contract.required_owner_roles:
                session_start_owners = [
                    hook_def
                    for hook_def in registry
                    if hook_def.event_type == "SessionStart"
                    and contract.resource in hook_def.reclaims_resources
                    and hook_applies_to_backend(
                        hook_def,
                        backend=backend,
                        session_scope=session_scope,
                    )
                ]
                if not session_start_owners:
                    raise ValueError(
                        f"lifecycle resource {contract.resource!r} has no SessionStart "
                        f"owner for {backend}/{session_scope}"
                    )
