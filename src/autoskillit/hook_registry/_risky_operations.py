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

from ._hooks_defs import HookDef, LifecycleContractDef, ProtectionWaiverDef

# The RISKY_* constants are NOT imported here directly. They are resolved
# lazily through ``autoskillit.hook_registry.__getattr__`` (PEP 562) which
# in turn reads them from ``autoskillit.hooks``. Importing them eagerly
# would observe a partially-initialized ``autoskillit.hooks`` module —
# this file loads during ``autoskillit.hook_registry.__init__``, which is
# itself imported by ``autoskillit.hooks.__init__`` before that module's
# import sequence finishes — and raise ``ImportError``. Deferring the
# resolution means by the time any consumer asks for the constants, both
# packages have finished initializing.
__all__ = [
    "hook_applies_to_backend",
    "validate_protection_coverage",
    "validate_lifecycle_contracts",
]

_PROTECTION_MECHANISM_BACKENDS = {
    "hook": frozenset({"claude_code", "codex"}),
    "not-applicable": frozenset({"claude_code", "codex"}),
    "codex-sandbox": frozenset({"codex"}),
}


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


def _validate_protection_waiver_delegate(
    registry: Sequence[HookDef],
    waiver: ProtectionWaiverDef,
    registered: set[str],
    *,
    backend: Literal["claude_code", "codex"],
) -> None:
    allowed_backends = _PROTECTION_MECHANISM_BACKENDS.get(waiver.covering_mechanism)
    if allowed_backends is None:
        raise ValueError(
            f"unknown protection delegate for {waiver.guard_script!r}: "
            f"covering_mechanism={waiver.covering_mechanism!r}"
        )
    if waiver.backend not in allowed_backends:
        raise ValueError(
            f"protection delegate is invalid for {waiver.backend}: "
            f"guard_script={waiver.guard_script!r}, "
            f"covering_mechanism={waiver.covering_mechanism!r}, "
            f"allowed={sorted(allowed_backends)}"
        )
    if waiver.covering_mechanism != "hook" or waiver.guard_script not in registered:
        return
    if waiver.covering_guard_script is None:
        raise ValueError(
            f"hook delegate missing for {waiver.guard_script!r}: "
            f"excluded_scope={waiver.excluded_scope!r}, backend={waiver.backend!r}"
        )
    if waiver.excluded_scope == "all":
        raise ValueError(
            f"hook delegate requires one excluded session class: "
            f"guard_script={waiver.guard_script!r}"
        )
    if not any(
        waiver.covering_guard_script in candidate.scripts
        and candidate.mechanism == "deny"
        and hook_applies_to_backend(
            candidate,
            backend=backend,
            session_scope=waiver.excluded_scope,
        )
        for candidate in registry
    ):
        raise ValueError(
            f"unreachable protection delegate for {waiver.guard_script!r}: "
            f"covering_guard_script={waiver.covering_guard_script!r}, "
            f"excluded_scope={waiver.excluded_scope!r}, backend={waiver.backend!r}"
        )


def validate_protection_coverage(
    registry: Sequence[HookDef],
    waivers: Sequence[ProtectionWaiverDef],
    *,
    backend: Literal["claude_code", "codex"],
) -> None:
    """Reject undeclared exclusions and unreachable hook delegates."""
    keys = [(waiver.guard_script, waiver.excluded_scope, waiver.backend) for waiver in waivers]
    covered = dict(zip(keys, waivers, strict=True))
    if len(covered) != len(keys):
        raise ValueError("protection waivers contain duplicate keys")
    registered = {script for hook_def in registry for script in hook_def.scripts}
    for waiver in waivers:
        _validate_protection_waiver_delegate(
            registry,
            waiver,
            registered,
            backend=backend,
        )
    for hook_def in registry:
        if hook_def.mechanism != "deny" or hook_def.session_scope == "any":
            continue
        excluded = "interactive" if hook_def.session_scope == "headless_only" else "headless"
        for script in hook_def.scripts:
            key = (script, excluded, backend)
            if key not in covered:
                raise ValueError(f"deny guard {script!r} has no protection waiver for {backend}")
    internal_exclusions = [("guards/git_ops_guard.py", "interactive")]
    if backend in _PROTECTION_MECHANISM_BACKENDS["codex-sandbox"]:
        internal_exclusions.append(("guards/write_guard.py", "all"))
    for script, excluded in internal_exclusions:
        if script in registered and (script, excluded, backend) not in covered:
            raise ValueError(
                f"internal policy exclusion {script!r} has no protection waiver: "
                f"excluded={excluded!r}, backend={backend!r}"
            )


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
            if "session_start" in contract.required_owner_roles and not any(
                hook_def.event_type == "SessionStart"
                and contract.resource in hook_def.reclaims_resources
                and hook_applies_to_backend(
                    hook_def,
                    backend=backend,
                    session_scope=session_scope,
                )
                for hook_def in registry
            ):
                raise ValueError(
                    f"lifecycle resource {contract.resource!r} has no SessionStart "
                    f"owner for {backend}/{session_scope}"
                )
