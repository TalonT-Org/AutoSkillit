"""Codec helpers for the skill session contract store.

Extracted from `_skill_session_contract_store.py`. These helpers own
the validation, source/exploration-vector/execution-identity serialization,
contract to/from dict, manifest construction, and digest computation.

The parent module re-exports `_digest_json` so existing callers using
`from autoskillit.execution.session._skill_session_contract_store import
_digest_json` continue to resolve.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autoskillit.core import (
    ExecutionIdentity,
    ExplorationVectorDef,
    ManagedHeadlessSessionLineageRef,
    SkillSessionContract,
    SkillSourceRef,
    atomic_write,
)
from autoskillit.execution.session._skill_session_contract_store import (
    _STORE_MANIFEST_SCHEMA_VERSION,
    _MANIFEST_FILENAME,
)


def _validate_raw_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id or "\x00" in session_id:
        raise ValueError(f"Invalid session ID: {session_id!r}")


def _finalized_contract_path(sessions_root: Path, session_id: str) -> Path:
    _validate_raw_session_id(session_id)
    key = hashlib.sha256(session_id.encode()).hexdigest()
    path = sessions_root / key
    if not path.resolve().is_relative_to(sessions_root.resolve()):
        raise ValueError(f"Skill session contract path escapes sessions root: {path}")
    return path


def _delete_finalized_contract(sessions_root: Path, session_id: str) -> None:
    shutil.rmtree(
        _finalized_contract_path(sessions_root, session_id),
        ignore_errors=True,
    )


def _validate_digest_map(
    field_name: str,
    digests: Mapping[str, str],
    closure: set[str],
) -> None:
    if set(digests) != closure:
        raise SkillContractError(f"{field_name} keys must exactly match closure")
    for name, digest in digests.items():
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise SkillContractError(f"Invalid {field_name} digest for {name!r}")


def _validate_contract(contract: SkillSessionContract) -> None:
    if contract.schema_version != SKILL_SESSION_CONTRACT_SCHEMA_VERSION:
        raise SkillContractError("Unsupported skill session contract schema")
    if contract.launch_contract is not None:
        if contract.launch_contract_digest != contract.launch_contract.digest:
            raise SkillContractError("Skill session launch contract digest mismatch")
        if contract.launch_contract.effective_backend != contract.backend:
            raise SkillContractError("Skill session launch backend mismatch")
        if contract.launch_contract.cwd != contract.cwd:
            raise SkillContractError("Skill session launch cwd mismatch")
    if not contract.root_name or not contract.closure:
        raise SkillContractError("Skill session contract requires a root and closure")
    closure = set(contract.closure)
    if len(closure) != len(contract.closure) or contract.root_name not in closure:
        raise SkillContractError("Skill session contract closure is invalid")
    if set(contract.source_refs) != closure:
        raise SkillContractError("source_refs keys must exactly match closure")
    if set(contract.member_roles) != closure:
        raise SkillContractError("member_roles keys must exactly match closure")
    if set(contract.member_capabilities) != closure:
        raise SkillContractError("member_capabilities keys must exactly match closure")
    if set(contract.member_activate_deps) != closure:
        raise SkillContractError("member_activate_deps keys must exactly match closure")
    if set(contract.canonical_contents) != closure:
        raise SkillContractError("canonical_contents keys must exactly match closure")
    if set(contract.exploration_vectors) != closure:
        raise SkillContractError("exploration_vectors keys must exactly match closure")
    if set(contract.exploration_sidecar_digests) != closure:
        raise SkillContractError("exploration_sidecar_digests keys must exactly match closure")
    for name in contract.closure:
        source_ref = contract.source_refs[name]
        if not isinstance(source_ref, SkillSourceRef):
            raise SkillContractError(f"source reference for {name!r} must be typed")
        if source_ref.logical_name != name:
            raise SkillContractError(f"source reference logical name mismatch for {name!r}")
        role = contract.member_roles[name]
        capabilities = contract.member_capabilities[name]
        validate_skill_capability_roles(capabilities, role)
        if role is not contract.execution_role:
            raise SkillContractError(f"member role mismatch for {name!r}")
        canonical_digest = hashlib.sha256(contract.canonical_contents[name].encode()).hexdigest()
        if canonical_digest != contract.canonical_digests[name]:
            raise SkillContractError(f"canonical content digest mismatch for {name!r}")
        vectors = contract.exploration_vectors[name]
        if any(not isinstance(vector, ExplorationVectorDef) for vector in vectors):
            raise SkillContractError(f"exploration vectors for {name!r} must be typed")
        vector_ids = tuple(vector.id for vector in vectors)
        if len(vector_ids) != len(set(vector_ids)):
            raise SkillContractError(f"exploration vector ids for {name!r} must be unique")
    member_capability_union = frozenset().union(
        *(contract.member_capabilities[name] for name in contract.closure)
    )
    if member_capability_union != contract.capability_union:
        raise SkillContractError("member capability union does not match contract")
    _validate_digest_map("canonical_digests", contract.canonical_digests, closure)
    _validate_digest_map("projected_digests", contract.projected_digests, closure)
    if contract.projection_version != SKILL_PROJECTION_VERSION:
        raise SkillContractError(
            f"unsupported projection_version {contract.projection_version}; "
            f"expected {SKILL_PROJECTION_VERSION}"
        )
    if not contract.project_root or not contract.cwd:
        raise SkillContractError("project_root and cwd are required")
    if not contract.backend or not contract.resolved_command:
        raise SkillContractError("backend and resolved_command are required")
    validate_skill_capability_roles(contract.capability_union, contract.execution_role)


def _validate_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Projected snapshot path must be a non-empty relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError(f"Unsafe projected snapshot path: {value!r}")
    return Path(*relative.parts)


def _validate_snapshot_mapping(
    contract: SkillSessionContract,
    snapshot: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(snapshot, Mapping):
        raise ValueError("Projected snapshot must be a mapping")
    by_skill: dict[str, str] = {}
    seen_paths: set[Path] = set()
    for raw_relative_path, content in snapshot.items():
        relative_path = _validate_relative_path(raw_relative_path)
        if relative_path in seen_paths:
            raise ValueError(f"Duplicate projected snapshot path: {raw_relative_path!r}")
        if relative_path.name != "SKILL.md" or len(relative_path.parts) < 2:
            raise ValueError(
                f"Projected snapshot path must end in <skill>/SKILL.md: {raw_relative_path!r}"
            )
        if not isinstance(content, str):
            raise ValueError(f"Projected snapshot content must be text: {raw_relative_path!r}")
        skill_name = relative_path.parent.name
        if skill_name in by_skill:
            raise ValueError(f"Multiple projected documents for skill {skill_name!r}")
        digest = hashlib.sha256(content.encode()).hexdigest()
        if contract.projected_digests.get(skill_name) != digest:
            raise ValueError(f"Projected snapshot digest mismatch for {skill_name!r}")
        by_skill[skill_name] = relative_path.as_posix()
        seen_paths.add(relative_path)
    if set(by_skill) != set(contract.closure):
        raise ValueError("Projected snapshot documents must exactly match closure")
    return by_skill


def _source_ref_to_dict(source_ref: SkillSourceRef) -> dict[str, Any]:
    return {
        "origin": source_ref.origin.value,
        "logical_name": source_ref.logical_name,
        "skill_path": str(source_ref.skill_path),
        "search_dir": source_ref.search_dir,
        "precedence": source_ref.precedence,
    }


def _source_ref_from_dict(data: Mapping[str, Any]) -> SkillSourceRef:
    try:
        return SkillSourceRef(
            origin=SkillSource(str(data["origin"])),
            logical_name=str(data["logical_name"]),
            skill_path=Path(str(data["skill_path"])),
            search_dir=(str(data["search_dir"]) if data.get("search_dir") is not None else None),
            precedence=(int(data["precedence"]) if data.get("precedence") is not None else None),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid skill source reference") from exc


_SERIALIZED_EXPLORATION_VECTOR_KEYS = frozenset(
    {
        "id",
        "disposition",
        "rationale",
        "applicability",
        "role",
        "profile",
        "relationship_classes",
        "task_id",
        "frontier_item_id",
        "depends_on",
        "scope",
        "body",
        "digest",
    }
)
_EXECUTION_IDENTITY_KEYS = frozenset(ExecutionIdentity.empty().to_dict())
_CHILD_EXECUTION_IDENTITY_KEYS = frozenset(
    {
        "task_id",
        "role",
        "plan_digest",
        "definition_digest",
        "requested_backend",
        "effective_backend",
        "requested_model",
        "effective_model",
        "requested_effort",
        "effective_effort",
        "session_id",
    }
)


def _exploration_vector_to_dict(vector: ExplorationVectorDef) -> dict[str, Any]:
    task = vector.task
    return {
        "id": vector.id,
        "disposition": vector.disposition.value,
        "rationale": vector.rationale,
        "applicability": vector.applicability.value,
        "role": vector.role,
        "profile": vector.profile.value,
        "relationship_classes": [item.value for item in vector.relationship_classes],
        "task_id": task.task_id,
        "frontier_item_id": task.frontier_item_id,
        "depends_on": list(task.depends_on),
        "scope": list(task.scope),
        "body": vector.body,
        "digest": vector.digest,
    }


def _exploration_vector_from_dict(value: object) -> ExplorationVectorDef:
    if not isinstance(value, dict) or set(value) != _SERIALIZED_EXPLORATION_VECTOR_KEYS:
        raise ValueError("serialized exploration vector keys are invalid")
    for field_name in ("relationship_classes", "depends_on", "scope"):
        field_value = value[field_name]
        if not isinstance(field_value, list) or any(
            not isinstance(item, str) for item in field_value
        ):
            raise ValueError(f"serialized exploration vector {field_name} is invalid")
    for field_name in (
        "id",
        "disposition",
        "rationale",
        "applicability",
        "profile",
        "task_id",
        "frontier_item_id",
        "body",
        "digest",
    ):
        if not isinstance(value[field_name], str):
            raise ValueError(f"serialized exploration vector {field_name} is invalid")
    if value["role"] is not None and not isinstance(value["role"], str):
        raise ValueError("serialized exploration vector role is invalid")
    profile = RepositoryProfileId(value["profile"])
    vector = ExplorationVectorDef(
        id=value["id"],
        disposition=ExplorationVectorDisposition(value["disposition"]),
        rationale=value["rationale"],
        applicability=ExplorationVectorApplicabilityId(value["applicability"]),
        role=value["role"],
        profile=profile,
        relationship_classes=tuple(
            RelationshipKind(item) for item in value["relationship_classes"]
        ),
        task=ExplorationTaskSpec(
            task_id=value["task_id"],
            frontier_item_id=value["frontier_item_id"],
            profile=profile,
            depends_on=tuple(value["depends_on"]),
            scope=tuple(value["scope"]),
        ),
        body=value["body"],
    )
    if value["digest"] != vector.digest:
        raise ValueError("serialized exploration vector digest mismatch")
    return vector


def _execution_identity_from_dict(value: object) -> ExecutionIdentity:
    if not isinstance(value, dict) or set(value) != _EXECUTION_IDENTITY_KEYS:
        raise ValueError("serialized execution identity keys are invalid")
    children = value.get("children")
    if not isinstance(children, list):
        raise ValueError("serialized execution identity children must be a list")
    scalar_values = {key: item for key, item in value.items() if key != "children"}
    if any(not isinstance(item, str) for item in scalar_values.values()):
        raise ValueError("serialized execution identity values must be text")
    parsed_children: list[ChildExecutionIdentity] = []
    for child in children:
        if (
            not isinstance(child, dict)
            or set(child) != _CHILD_EXECUTION_IDENTITY_KEYS
            or any(not isinstance(item, str) for item in child.values())
        ):
            raise ValueError("serialized child execution identity is invalid")
        parsed_children.append(ChildExecutionIdentity(**child))
    return ExecutionIdentity(**scalar_values, children=tuple(parsed_children))


def _contract_to_dict(contract: SkillSessionContract) -> dict[str, Any]:
    return {
        "schema_version": contract.schema_version,
        "root_name": contract.root_name,
        "execution_role": contract.execution_role.value,
        "source_refs": {
            name: _source_ref_to_dict(source_ref)
            for name, source_ref in sorted(contract.source_refs.items())
        },
        "closure": list(contract.closure),
        "capability_union": sorted(contract.capability_union),
        "canonical_digests": dict(sorted(contract.canonical_digests.items())),
        "projected_digests": dict(sorted(contract.projected_digests.items())),
        "projection_version": contract.projection_version,
        "project_root": contract.project_root,
        "cwd": contract.cwd,
        "backend": contract.backend,
        "resolved_command": contract.resolved_command,
        "member_roles": {name: role.value for name, role in sorted(contract.member_roles.items())},
        "member_capabilities": {
            name: sorted(capabilities)
            for name, capabilities in sorted(contract.member_capabilities.items())
        },
        "member_activate_deps": {
            name: list(dependencies)
            for name, dependencies in sorted(contract.member_activate_deps.items())
        },
        "canonical_contents": dict(sorted(contract.canonical_contents.items())),
        "exploration_vectors": {
            name: [_exploration_vector_to_dict(vector) for vector in vectors]
            for name, vectors in sorted(contract.exploration_vectors.items())
        },
        "exploration_sidecar_digests": dict(sorted(contract.exploration_sidecar_digests.items())),
        "resolved_exploration_profile": (
            contract.resolved_exploration_profile.value
            if contract.resolved_exploration_profile is not None
            else None
        ),
        "active_exploration_applicabilities": sorted(
            item.value for item in contract.active_exploration_applicabilities
        ),
        "expected_output_patterns": list(contract.expected_output_patterns),
        "write_behavior": {
            "mode": contract.write_behavior.mode,
            "expected_when": list(contract.write_behavior.expected_when),
        },
        "read_only": contract.read_only,
        "scope_discipline": contract.scope_discipline,
        "parent_sandbox_mode": contract.parent_sandbox_mode,
        "completion_required": contract.completion_required,
        "skill_contract_json": contract.skill_contract_json,
        "projection_substitutions": [list(item) for item in contract.projection_substitutions],
        "projection_gating": contract.projection_gating,
        "projection_namespace": contract.projection_namespace,
        "launch_contract": (
            json.loads(contract.launch_contract.canonical_json)
            if contract.launch_contract is not None
            else None
        ),
        "launch_contract_digest": contract.launch_contract_digest,
        "execution_identity": contract.execution_identity.to_dict(),
    }


def _contract_from_dict(data: Mapping[str, Any]) -> SkillSessionContract:
    try:
        source_refs_raw = data["source_refs"]
        if not isinstance(source_refs_raw, dict):
            raise ValueError("source_refs must be an object")
        read_only = data.get("read_only", False)
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        scope_discipline = data.get("scope_discipline", False)
        if not isinstance(scope_discipline, bool):
            raise ValueError("scope_discipline must be a boolean")
        parent_sandbox_mode = data["parent_sandbox_mode"]
        if not isinstance(parent_sandbox_mode, str):
            raise ValueError("parent_sandbox_mode must be text")
        completion_required = data.get("completion_required", False)
        if not isinstance(completion_required, bool):
            raise ValueError("completion_required must be a boolean")
        projection_gating = data.get("projection_gating")
        if projection_gating is not None and not isinstance(projection_gating, bool):
            raise ValueError("projection_gating must be a boolean or null")
        projection_substitutions = data.get("projection_substitutions", [])
        if not isinstance(projection_substitutions, list) or any(
            not isinstance(item, list) or len(item) != 2 for item in projection_substitutions
        ):
            raise ValueError("projection_substitutions entries must be two-element lists")
        launch_payload = data.get("launch_contract")
        launch_digest = data.get("launch_contract_digest", "")
        if launch_payload is not None and not isinstance(launch_payload, dict):
            raise ValueError("launch_contract must be an object or null")
        if not isinstance(launch_digest, str):
            raise ValueError("launch_contract_digest must be a string")
        launch_contract = (
            ResolvedLaunchContract.from_payload(
                launch_payload,
                expected_digest=launch_digest,
            )
            if launch_payload is not None
            else None
        )
        exploration_vectors_raw = data["exploration_vectors"]
        if not isinstance(exploration_vectors_raw, dict) or any(
            not isinstance(vectors, list) for vectors in exploration_vectors_raw.values()
        ):
            raise ValueError("exploration_vectors must be an object of lists")
        resolved_exploration_profile_raw = data["resolved_exploration_profile"]
        if resolved_exploration_profile_raw is not None and not isinstance(
            resolved_exploration_profile_raw, str
        ):
            raise ValueError("resolved_exploration_profile must be text or null")
        active_applicabilities_raw = data["active_exploration_applicabilities"]
        if not isinstance(active_applicabilities_raw, list) or any(
            not isinstance(item, str) for item in active_applicabilities_raw
        ):
            raise ValueError("active_exploration_applicabilities must be a list of text")
        return SkillSessionContract(
            root_name=str(data["root_name"]),
            execution_role=SkillExecutionRole(str(data["execution_role"])),
            source_refs={
                str(name): _source_ref_from_dict(source_ref)
                for name, source_ref in source_refs_raw.items()
                if isinstance(source_ref, dict)
            },
            closure=tuple(str(name) for name in data["closure"]),
            capability_union=frozenset(str(cap) for cap in data["capability_union"]),
            canonical_digests={
                str(name): str(digest) for name, digest in data["canonical_digests"].items()
            },
            projected_digests={
                str(name): str(digest) for name, digest in data["projected_digests"].items()
            },
            projection_version=int(data["projection_version"]),
            project_root=str(data["project_root"]),
            cwd=str(data["cwd"]),
            backend=str(data["backend"]),
            resolved_command=str(data["resolved_command"]),
            member_roles={
                str(name): SkillExecutionRole(str(role))
                for name, role in data["member_roles"].items()
            },
            member_capabilities={
                str(name): frozenset(str(capability) for capability in capabilities)
                for name, capabilities in data["member_capabilities"].items()
            },
            member_activate_deps={
                str(name): tuple(str(dependency) for dependency in dependencies)
                for name, dependencies in data["member_activate_deps"].items()
            },
            canonical_contents={
                str(name): str(content) for name, content in data["canonical_contents"].items()
            },
            exploration_vectors={
                str(name): tuple(_exploration_vector_from_dict(vector) for vector in vectors)
                for name, vectors in exploration_vectors_raw.items()
            },
            exploration_sidecar_digests={
                str(name): str(digest)
                for name, digest in data.get("exploration_sidecar_digests", {}).items()
            },
            resolved_exploration_profile=(
                RepositoryProfileId(resolved_exploration_profile_raw)
                if resolved_exploration_profile_raw is not None
                else None
            ),
            active_exploration_applicabilities=frozenset(
                ExplorationVectorApplicabilityId(item) for item in active_applicabilities_raw
            ),
            expected_output_patterns=tuple(
                str(pattern) for pattern in data.get("expected_output_patterns", [])
            ),
            write_behavior=WriteBehaviorSpec(
                mode=(
                    str(data.get("write_behavior", {}).get("mode"))
                    if data.get("write_behavior", {}).get("mode") is not None
                    else None
                ),
                expected_when=tuple(
                    str(pattern)
                    for pattern in data.get("write_behavior", {}).get("expected_when", [])
                ),
            ),
            read_only=read_only,
            scope_discipline=scope_discipline,
            parent_sandbox_mode=parent_sandbox_mode,
            completion_required=completion_required,
            skill_contract_json=str(data.get("skill_contract_json", "")),
            projection_substitutions=tuple(
                (str(item[0]), str(item[1])) for item in projection_substitutions
            ),
            projection_gating=projection_gating,
            projection_namespace=(
                str(data["projection_namespace"])
                if data.get("projection_namespace") is not None
                else None
            ),
            launch_contract=launch_contract,
            launch_contract_digest=launch_digest,
            execution_identity=_execution_identity_from_dict(data["execution_identity"]),
            schema_version=int(data["schema_version"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError, SkillContractError) as exc:
        raise ValueError("Invalid serialized skill session contract") from exc


def _digest_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _build_manifest(
    *,
    contract: SkillSessionContract,
    raw_session_id: str | None,
    candidate_session_ids: tuple[str, ...],
    snapshot_paths: Mapping[str, str],
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
) -> dict[str, Any]:
    contract_data = _contract_to_dict(contract)
    return {
        "schema_version": _STORE_MANIFEST_SCHEMA_VERSION,
        "raw_session_id": raw_session_id,
        "candidate_session_ids": list(candidate_session_ids),
        "managed_lineage_ref": (
            managed_lineage_ref.to_dict() if managed_lineage_ref is not None else None
        ),
        "contract": contract_data,
        "contract_digest": _digest_json(contract_data),
        "snapshot_paths": dict(sorted(snapshot_paths.items())),
    }


def _managed_lineage_ref_from_manifest(
    manifest: Mapping[str, Any],
) -> ManagedHeadlessSessionLineageRef | None:
    if "managed_lineage_ref" not in manifest:
        raise ValueError("Skill session manifest is missing managed lineage reference")
    value = manifest.get("managed_lineage_ref")
    if value is None:
        return None
    try:
        return ManagedHeadlessSessionLineageRef.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid skill session managed lineage reference") from exc
