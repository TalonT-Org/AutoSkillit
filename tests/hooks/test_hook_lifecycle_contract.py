"""Registry-level contracts for persistent hook resource ownership."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

import autoskillit.hooks._capture_lifecycle as capture_lifecycle
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
    hook_applies_to_backend,
    validate_lifecycle_contracts,
)
from autoskillit.hooks._capture_artifacts import CAPTURE_PATH_COMPONENTS
from autoskillit.hooks._capture_lifecycle import LEDGER_NAME, LOCK_NAME

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_RESOURCE = "shell-captures"


def _replace_hook(script: str, **changes: object) -> list[HookDef]:
    return [
        dataclasses.replace(hook_def, **changes) if script in hook_def.scripts else hook_def
        for hook_def in HOOK_REGISTRY
    ]


def test_capture_shards_use_canonical_store_ports() -> None:
    capture_dir = Path(capture_lifecycle.__file__).with_name("_capture")
    for filename in ("_delivery.py", "_resolver.py", "_sweep.py"):
        tree = ast.parse((capture_dir / filename).read_text())
        local_store_protocols = [
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef) and "Store" in node.name
        ]
        assert not local_store_protocols, (
            f"{filename} defines local store protocols: {local_store_protocols}"
        )
        store_annotations = [
            ast.unparse(argument.annotation)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            if argument.arg == "store" and argument.annotation is not None
        ]
        assert store_annotations
        assert all(annotation.startswith("_store_port.") for annotation in store_annotations), (
            f"{filename} has noncanonical store annotations: {store_annotations}"
        )


def test_resource_fields_are_immutable_with_independent_factories() -> None:
    fields = {field.name: field for field in dataclasses.fields(HookDef)}
    for name in (
        "produces_resources",
        "reclaims_resources",
        "self_reclaims_resources",
    ):
        assert fields[name].default_factory is frozenset
        assert isinstance(getattr(HookDef(matcher="one"), name), frozenset)


@pytest.mark.parametrize(
    "field_name",
    (
        "produces_resources",
        "reclaims_resources",
        "self_reclaims_resources",
    ),
)
@pytest.mark.parametrize(
    "replacement",
    (
        {_RESOURCE},
        _RESOURCE,
        frozenset({""}),
        frozenset({1}),
    ),
)
def test_hook_def_rejects_runtime_invalid_resource_metadata(
    field_name: str,
    replacement: object,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        HookDef(matcher="Bash", **{field_name: replacement})


def test_real_lifecycle_contract_passes_every_generator_boundary() -> None:
    validate_lifecycle_contracts(HOOK_REGISTRY, LIFECYCLE_CONTRACTS, backend="codex")
    validate_lifecycle_contracts(HOOK_REGISTRY, LIFECYCLE_CONTRACTS, backend="claude_code")
    assert generate_codex_hooks_config()
    assert generate_hooks_json()["hooks"]


def test_snapshot_reference_and_reader_reuse_one_registered_resource_owner() -> None:
    capture_contracts = [
        contract for contract in LIFECYCLE_CONTRACTS if contract.resource == _RESOURCE
    ]
    assert len(capture_contracts) == 1
    assert capture_contracts[0].required_owner_roles == {
        "same_runner",
        "session_start",
    }
    capture_hooks = [
        hook
        for hook in HOOK_REGISTRY
        if _RESOURCE
        in (hook.produces_resources | hook.reclaims_resources | hook.self_reclaims_resources)
    ]
    assert {script for hook in capture_hooks for script in hook.scripts} == {
        "shell_capture_hook.py",
        "capture_lifecycle_hook.py",
    }
    assert {resource for hook in capture_hooks for resource in hook.produces_resources} == {
        _RESOURCE
    }
    assert CAPTURE_PATH_COMPONENTS == (".autoskillit", "temp", "shell_capture")
    assert {LEDGER_NAME, LOCK_NAME} == {
        ".capture-lifecycle.ledger",
        ".capture-lifecycle.lock",
    }


def test_every_shell_capture_persistent_path_constant_is_registered() -> None:
    hooks_dir = Path(capture_lifecycle.__file__).resolve().parent
    capture_dir = hooks_dir / "_capture"
    observed: set[tuple[str, str]] = set()
    for path in (*capture_dir.glob("*.py"), hooks_dir / "_capture_lifecycle.py"):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if (
                    name == "CAPTURE_PATH_COMPONENTS"
                    or name.endswith("_NAME")
                    or name.endswith("_NAME_RE")
                ):
                    observed.add((path.name, name))

    assert observed == {
        ("_authority.py", "CAPTURE_PATH_COMPONENTS"),
        ("_syntax.py", "PUBLIC_NAME_RE"),
        ("_syntax.py", "QUARANTINE_NAME_RE"),
        ("_syntax.py", "STAGING_NAME_RE"),
        ("_capture_lifecycle.py", "LEDGER_NAME"),
        ("_capture_lifecycle.py", "LOCK_NAME"),
    }


@pytest.mark.parametrize(
    ("backend", "session_scope"),
    (
        ("unknown", "headless"),
        ("codex", "unknown"),
        ("claude_code", "unknown"),
    ),
)
def test_hook_reachability_rejects_runtime_invalid_boundaries(
    backend: str,
    session_scope: str,
) -> None:
    with pytest.raises(ValueError, match="unsupported hook"):
        hook_applies_to_backend(
            HookDef(matcher="Bash"),
            backend=backend,  # type: ignore[arg-type]
            session_scope=session_scope,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("hook_scope", "session_scope", "expected"),
    (
        ("any", "headless", True),
        ("any", "interactive", True),
        ("headless_only", "headless", True),
        ("headless_only", "interactive", False),
        ("interactive_only", "headless", False),
        ("interactive_only", "interactive", True),
    ),
)
def test_codex_hook_reachability_honors_session_scope(
    hook_scope: str,
    session_scope: str,
    *,
    expected: bool,
) -> None:
    hook_def = HookDef(
        matcher="Bash",
        session_scope=hook_scope,  # type: ignore[arg-type]
        codex_status="works-as-is",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    )

    assert (
        hook_applies_to_backend(
            hook_def,
            backend="codex",
            session_scope=session_scope,  # type: ignore[arg-type]
        )
        is expected
    )


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
