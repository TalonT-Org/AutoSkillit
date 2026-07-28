"""Registry-level contracts for persistent hook resource ownership."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HOOK_REGISTRY_HASH,
    LIFECYCLE_CONTRACTS,
    RETIRED_SCRIPT_BASENAMES,
    HookDef,
    LifecycleContractDef,
    compute_registry_hash,
    generate_hooks_json,
    validate_lifecycle_contracts,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_RESOURCE = "shell-captures"


def _replace_hook(script: str, **changes: object) -> list[HookDef]:
    return [
        dataclasses.replace(hook_def, **changes) if script in hook_def.scripts else hook_def
        for hook_def in HOOK_REGISTRY
    ]


def test_resource_fields_are_immutable_with_independent_factories() -> None:
    fields = {field.name: field for field in dataclasses.fields(HookDef)}
    for name in (
        "produces_resources",
        "reclaims_resources",
        "self_reclaims_resources",
    ):
        assert fields[name].default_factory is frozenset
        assert isinstance(getattr(HookDef(matcher="one"), name), frozenset)


def test_real_lifecycle_contract_passes_every_generator_boundary() -> None:
    validate_lifecycle_contracts(HOOK_REGISTRY, LIFECYCLE_CONTRACTS, backend="codex")
    validate_lifecycle_contracts(HOOK_REGISTRY, LIFECYCLE_CONTRACTS, backend="claude_code")
    assert generate_codex_hooks_config()
    assert generate_hooks_json()["hooks"]


def test_every_resource_producer_requires_a_contract() -> None:
    registry = [
        *HOOK_REGISTRY,
        HookDef(
            matcher="Bash",
            scripts=["orphaned_producer.py"],
            produces_resources=frozenset({"orphaned-resource"}),
            enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
        ),
    ]
    with pytest.raises(ValueError, match="has no lifecycle contract"):
        validate_lifecycle_contracts(registry, LIFECYCLE_CONTRACTS, backend="codex")


def test_every_reachable_backend_requires_its_own_contract() -> None:
    producer = HookDef(
        matcher="Bash",
        scripts=["multi_backend_producer.py"],
        produces_resources=frozenset({"multi-backend-resource"}),
        reclaims_resources=frozenset({"multi-backend-resource"}),
        self_reclaims_resources=frozenset({"multi-backend-resource"}),
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    )
    codex_contract = LifecycleContractDef(
        resource="multi-backend-resource",
        producer_script="multi_backend_producer.py",
        backend="codex",
        session_scope="any",
        required_owner_roles=frozenset({"same_runner"}),
    )

    validate_lifecycle_contracts([producer], [codex_contract], backend="codex")
    with pytest.raises(ValueError, match="has no lifecycle contract"):
        validate_lifecycle_contracts([producer], [codex_contract], backend="claude_code")


def test_same_runner_metadata_is_required() -> None:
    registry = _replace_hook(
        "shell_capture_hook.py",
        self_reclaims_resources=frozenset(),
    )
    with pytest.raises(ValueError, match="no same-runner owner"):
        generate_codex_hooks_config(registry=registry)


@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "PostToolUse", "matcher": "Bash"},
        {"session_scope": "interactive_only"},
        {
            "codex_status": "not-applicable",
            "enforcement_strength": {
                "claude_code": "soft",
                "codex": "not-applicable",
            },
        },
    ],
)
def test_session_start_owner_must_remain_reachable(changes: dict[str, object]) -> None:
    registry = _replace_hook("capture_lifecycle_hook.py", **changes)
    with pytest.raises(ValueError, match="no SessionStart owner"):
        generate_codex_hooks_config(registry=registry)


def test_generator_validates_the_exact_registry_passed_by_caller() -> None:
    contract = LifecycleContractDef(
        resource="custom-resource",
        producer_script="custom_producer.py",
        backend="claude_code",
        session_scope="any",
        required_owner_roles=frozenset({"same_runner", "session_start"}),
    )
    producer = HookDef(
        matcher="Bash",
        scripts=["custom_producer.py"],
        produces_resources=frozenset({"custom-resource"}),
        reclaims_resources=frozenset({"custom-resource"}),
        self_reclaims_resources=frozenset({"custom-resource"}),
        enforcement_strength={"claude_code": "soft", "codex": "not-applicable"},
        codex_status="not-applicable",
    )
    owner = HookDef(
        event_type="SessionStart",
        scripts=["custom_owner.py"],
        reclaims_resources=frozenset({"custom-resource"}),
        enforcement_strength={"claude_code": "soft", "codex": "not-applicable"},
        codex_status="not-applicable",
    )

    assert generate_hooks_json([producer, owner], [contract])["hooks"]
    with pytest.raises(ValueError, match="no SessionStart owner"):
        generate_hooks_json([producer], [contract])


@pytest.mark.parametrize(
    ("field_name", "replacement", "message"),
    [
        ("resource", 1, "resource"),
        ("producer_script", None, "producer_script"),
        ("backend", "unknown", "backend"),
        ("session_scope", "unknown", "session_scope"),
        ("required_owner_roles", {"same_runner"}, "required_owner_roles"),
        ("required_owner_roles", frozenset({"unknown"}), "invalid role"),
    ],
)
def test_lifecycle_contract_rejects_runtime_invalid_fields(
    field_name: str,
    replacement: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(
            LIFECYCLE_CONTRACTS[0],
            **{field_name: replacement},
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("produces_resources", frozenset({_RESOURCE, "other"})),
        ("reclaims_resources", frozenset()),
        ("self_reclaims_resources", frozenset()),
    ],
)
def test_registry_hash_is_sensitive_to_resource_metadata(
    field_name: str,
    replacement: frozenset[str],
) -> None:
    registry = _replace_hook("shell_capture_hook.py", **{field_name: replacement})
    assert (
        compute_registry_hash(
            registry,
            RETIRED_SCRIPT_BASENAMES,
            LIFECYCLE_CONTRACTS,
        )
        != HOOK_REGISTRY_HASH
    )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("resource", "other-resource"),
        ("producer_script", "other_producer.py"),
        ("backend", "claude_code"),
        ("session_scope", "headless_only"),
        ("required_owner_roles", frozenset({"same_runner"})),
    ],
)
def test_registry_hash_is_sensitive_to_every_lifecycle_contract_field(
    field_name: str,
    replacement: object,
) -> None:
    contract = dataclasses.replace(
        LIFECYCLE_CONTRACTS[0],
        **{field_name: replacement},
    )
    assert (
        compute_registry_hash(
            HOOK_REGISTRY,
            RETIRED_SCRIPT_BASENAMES,
            (contract,),
        )
        != HOOK_REGISTRY_HASH
    )
