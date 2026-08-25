"""Always-on persistence contracts for resumable projected skill sessions."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from autoskillit.core import SKILL_PROJECTION_VERSION, SkillExecutionRole
from tests._helpers import inject_vanishing_subtree_on_descent

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_exploration_vector_contract_versions_invalidate_stale_artifacts() -> None:
    from autoskillit.core import SKILL_SESSION_CONTRACT_SCHEMA_VERSION

    assert SKILL_PROJECTION_VERSION == 7
    assert SKILL_SESSION_CONTRACT_SCHEMA_VERSION == 5


def _contract(tmp_path: Path, projected_text: str):
    from autoskillit.core import (
        ChildExecutionIdentity,
        ExecutionIdentity,
        ExplorationTaskSpec,
        ExplorationVectorApplicabilityId,
        ExplorationVectorDef,
        ExplorationVectorDisposition,
        RelationshipKind,
        RepositoryProfileId,
        SkillExecutionRole,
        SkillSource,
        SkillSourceRef,
    )
    from autoskillit.execution.session import SkillSessionContract

    projected_digest = hashlib.sha256(projected_text.encode()).hexdigest()
    return SkillSessionContract(
        root_name="root",
        execution_role=SkillExecutionRole.SESSION,
        source_refs={
            "root": SkillSourceRef(
                origin=SkillSource.PROJECT_LOCAL,
                logical_name="root",
                skill_path=tmp_path / "project" / ".claude/skills/root/SKILL.md",
                search_dir=".claude/skills",
                precedence=0,
            )
        },
        closure=("root",),
        capability_union=frozenset({"github_api_write"}),
        canonical_digests={"root": hashlib.sha256(projected_text.encode()).hexdigest()},
        projected_digests={"root": projected_digest},
        projection_version=SKILL_PROJECTION_VERSION,
        project_root=str(tmp_path / "project"),
        cwd=str(tmp_path / "worktree"),
        backend="claude-code",
        resolved_command="/root do work",
        member_roles={"root": SkillExecutionRole.SESSION},
        member_capabilities={"root": frozenset({"github_api_write"})},
        member_activate_deps={"root": ()},
        canonical_contents={"root": projected_text},
        exploration_vectors={
            "root": (
                ExplorationVectorDef(
                    id="inspect-consumers",
                    disposition=ExplorationVectorDisposition.MIGRATED,
                    rationale="Native semantic navigation covers the reviewed vector.",
                    applicability=(ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP),
                    role="semantic-code-navigator",
                    profile=RepositoryProfileId.GENERIC_PYTHON,
                    relationship_classes=(RelationshipKind.REFERENCES,),
                    task=ExplorationTaskSpec(
                        task_id="inspect-consumers-task",
                        frontier_item_id="inspect-consumers-frontier",
                        profile=RepositoryProfileId.GENERIC_PYTHON,
                        scope=("src",),
                    ),
                    body="Inspect consumers.",
                ),
            )
        },
        read_only=True,
        parent_sandbox_mode="read-only",
        execution_identity=ExecutionIdentity(
            children=(
                ChildExecutionIdentity(
                    task_id="inspect-consumers-task",
                    role="semantic-code-navigator",
                    plan_digest="plan-digest",
                    definition_digest="definition-digest",
                    requested_backend="codex",
                    requested_model="gpt-5.6-luna",
                    requested_effort="max",
                ),
            ),
        ),
    )


def test_contract_accepts_opaque_vectors_for_a_member_without_typed_vectors(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path, "projected\n")
    root_ref = contract.source_refs["root"]
    sibling_ref = replace(
        root_ref,
        logical_name="sibling",
        skill_path=tmp_path / "project" / ".claude/skills/sibling/SKILL.md",
    )

    resumed = replace(
        contract,
        closure=("root", "sibling"),
        source_refs={**contract.source_refs, "sibling": sibling_ref},
        canonical_digests={**contract.canonical_digests, "sibling": "a" * 64},
        projected_digests={**contract.projected_digests, "sibling": "b" * 64},
        member_roles={**contract.member_roles, "sibling": SkillExecutionRole.SESSION},
        member_capabilities={**contract.member_capabilities, "sibling": frozenset()},
        member_activate_deps={**contract.member_activate_deps, "sibling": ()},
        canonical_contents={**contract.canonical_contents, "sibling": "sibling\n"},
        exploration_vectors={"root": contract.exploration_vectors["root"]},
        opaque_exploration_vectors={"sibling": ((0, {"future": "vector"}),)},
    )

    assert resumed.exploration_vectors.get("sibling", ()) == ()
    assert resumed.opaque_exploration_vectors["sibling"][0][0] == 0


def _lineage_ref(tmp_path: Path):
    from autoskillit.core import ManagedHeadlessSessionLineageRef

    anchor = tmp_path.resolve()
    stat_result = anchor.stat()
    return ManagedHeadlessSessionLineageRef(
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor=str(anchor),
        anchor_device=stat_result.st_dev,
        anchor_inode=stat_result.st_ino,
    )


def test_skill_session_contract_rejects_incoherent_persisted_authority(tmp_path: Path) -> None:
    from autoskillit.core import (
        ExecutionIdentity,
        ExplorationVectorApplicabilityId,
        RepositoryProfileId,
        SkillContractError,
    )

    contract = _contract(tmp_path, "projected")

    with pytest.raises(SkillContractError, match="does not match.*read_only"):
        replace(contract, read_only=False)
    with pytest.raises(SkillContractError, match="cannot be auto"):
        replace(contract, resolved_exploration_profile=RepositoryProfileId.AUTO)
    with pytest.raises(SkillContractError, match="must include always"):
        replace(
            contract,
            active_exploration_applicabilities=frozenset(
                {ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP}
            ),
        )
    with pytest.raises(SkillContractError, match="execution identity must be typed"):
        replace(contract, execution_identity=cast(ExecutionIdentity, object()))


def test_store_round_trip_preserves_machine_contract_and_projected_snapshot(
    tmp_path: Path,
) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    root = tmp_path / "contracts"
    text = "---\nname: root\n---\nprojected body\n"
    contract = _contract(tmp_path, text)
    store = DefaultSkillSessionContractStore(root=root)

    correlation_key = store.create_provisional(
        contract=contract,
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    store.observe_candidate(correlation_key, "provider-attempt-1")
    store.finalize(correlation_key, "backend/session:final")
    stored = store.load("backend/session:final")

    assert stored.contract == contract
    assert stored.contract.exploration_vectors["root"][0].body == "Inspect consumers."
    assert (
        stored.contract.exploration_vectors["root"][0].applicability.value
        == "planner-extract-domain-deep"
    )
    assert stored.contract.execution_identity.children[0].requested_model == "gpt-5.6-luna"
    assert stored.contract.parent_sandbox_mode == "read-only"
    assert stored.raw_session_id == "backend/session:final"
    assert (stored.snapshot_dir / ".claude/skills/root/SKILL.md").read_text() == text
    assert stored.snapshot_dir.resolve().is_relative_to(root.resolve())


def test_store_persists_lineage_binding_separately_and_rebinds_verified_final_id(
    tmp_path: Path,
) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    root = tmp_path / "contracts"
    text = "projected\n"
    lineage_ref = _lineage_ref(tmp_path)
    store = DefaultSkillSessionContractStore(root=root)
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
        managed_lineage_ref=lineage_ref,
    )
    store.observe_candidate(correlation_key, "candidate-only")
    store.finalize(correlation_key, "resume-request")
    stored = store.load("resume-request")
    assert stored.managed_lineage_ref == lineage_ref
    assert not hasattr(stored.contract, "managed_lineage_ref")

    store.rebind_final_session("resume-request", "resume-final", lineage_ref)
    rebound = store.load("resume-final")
    assert rebound.raw_session_id == "resume-final"
    assert rebound.managed_lineage_ref == lineage_ref
    with pytest.raises(FileNotFoundError):
        store.load("resume-request")
    with pytest.raises(FileNotFoundError):
        store.load("candidate-only")


def test_store_rebind_requires_exact_persisted_lineage_reference(tmp_path: Path) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    text = "projected\n"
    lineage_ref = _lineage_ref(tmp_path)
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
        managed_lineage_ref=lineage_ref,
    )
    store.finalize(correlation_key, "resume-request")

    with pytest.raises(ValueError, match="lineage reference mismatch"):
        store.rebind_final_session(
            "resume-request",
            "resume-final",
            replace(lineage_ref, lineage_digest="c" * 64),
        )
    assert store.load("resume-request").managed_lineage_ref == lineage_ref


def test_contract_digest_authority_is_copied_and_immutable(tmp_path: Path) -> None:
    canonical_digests = {"root": "a" * 64}
    projected_digests = {"root": "b" * 64}
    contract = replace(
        _contract(tmp_path, "projected\n"),
        canonical_digests=canonical_digests,
        projected_digests=projected_digests,
    )

    canonical_digests["root"] = "c" * 64
    projected_digests["root"] = "d" * 64

    assert contract.canonical_digests["root"] == "a" * 64
    assert contract.projected_digests["root"] == "b" * 64
    with pytest.raises(TypeError):
        contract.canonical_digests["root"] = "e" * 64  # type: ignore[index]
    with pytest.raises(TypeError):
        contract.projected_digests["root"] = "f" * 64  # type: ignore[index]


def test_store_keys_are_distinct_contained_collision_safe_and_final_only(tmp_path: Path) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    root = tmp_path / "contracts"
    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=root)
    first = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    second = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    assert first != second

    store.observe_candidate(first, "../../superseded")
    store.observe_candidate(first, "../../superseded")
    store.finalize(first, "a/b")
    store.finalize(second, "a_b")

    with pytest.raises((FileNotFoundError, KeyError)):
        store.load("../../superseded")
    slash = store.load("a/b")
    underscore = store.load("a_b")
    assert slash.snapshot_dir != underscore.snapshot_dir
    assert slash.snapshot_dir.resolve().is_relative_to(root.resolve())
    assert underscore.snapshot_dir.resolve().is_relative_to(root.resolve())

    with pytest.raises(ValueError, match="session"):
        store.load("")


def test_store_rejects_projected_digest_tampering_and_deletes_only_explicitly(
    tmp_path: Path,
) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    root = tmp_path / "contracts"
    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=root)
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    store.finalize(correlation_key, "resumable")
    stored = store.load("resumable")
    projected = stored.snapshot_dir / ".claude/skills/root/SKILL.md"
    projected.write_text("tampered\n")

    with pytest.raises(ValueError, match="digest"):
        store.load("resumable")

    store.delete("resumable")
    with pytest.raises((FileNotFoundError, KeyError)):
        store.load("resumable")


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"schema_version": 999}, "schema"),
        (
            {"member_roles": {"root": SkillExecutionRole.ORCHESTRATOR}},
            "role",
        ),
        (
            {
                "member_capabilities": {"root": frozenset({"run_skill"})},
                "capability_union": frozenset({"run_skill"}),
            },
            "capabilit|execution role",
        ),
        ({"canonical_contents": {"root": "changed canonical bytes"}}, "canonical"),
        ({"projection_version": 0}, "projection_version"),
        ({"projection_version": 999}, "projection_version"),
    ],
)
def test_store_rejects_incompatible_machine_contracts_before_persistence(
    tmp_path: Path,
    change: dict[str, object],
    match: str,
) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    text = "projected\n"
    contract = replace(_contract(tmp_path, text), **change)
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")

    with pytest.raises(SkillContractError, match=match):
        store.create_provisional(
            contract=contract,
            snapshot={".claude/skills/root/SKILL.md": text},
        )


def test_store_rejects_opaque_or_incomplete_source_identity(tmp_path: Path) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    text = "projected\n"
    contract = replace(
        _contract(tmp_path, text),
        source_refs={"root": "project-local:root"},  # type: ignore[dict-item]
    )

    with pytest.raises(SkillContractError, match="typed"):
        DefaultSkillSessionContractStore(root=tmp_path / "contracts").create_provisional(
            contract=contract,
            snapshot={".claude/skills/root/SKILL.md": text},
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("read_only", "false"),
        ("completion_required", 1),
        ("projection_gating", "true"),
        ("projection_substitutions", [["incomplete"]]),
        ("exploration_vectors", {"root": [{"id": "incomplete"}]}),
        ("execution_identity", {"unexpected": "field"}),
    ],
)
def test_store_rejects_malformed_serialized_contract_authority(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore
    from autoskillit.execution.session._skill_session_contract_codec import _digest_json

    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    entry = store._provisional_path(correlation_key)  # noqa: SLF001
    manifest = store._read_manifest(entry)  # noqa: SLF001
    contract_data = manifest["contract"]
    contract_data[field] = invalid_value
    manifest["contract_digest"] = _digest_json(contract_data)
    store._write_manifest(entry, manifest)  # noqa: SLF001

    with pytest.raises(ValueError, match="Invalid serialized"):
        store.finalize(correlation_key, "malformed")


def test_store_rejects_exploration_vector_body_digest_tampering(tmp_path: Path) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore
    from autoskillit.execution.session._skill_session_contract_codec import _digest_json

    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    entry = store._provisional_path(correlation_key)  # noqa: SLF001
    manifest = store._read_manifest(entry)  # noqa: SLF001
    contract_data = manifest["contract"]
    contract_data["exploration_vectors"]["root"][0]["body"] = "tampered body"
    manifest["contract_digest"] = _digest_json(contract_data)
    store._write_manifest(entry, manifest)  # noqa: SLF001

    with pytest.raises(ValueError, match="Invalid serialized"):
        store.finalize(correlation_key, "tampered-vector")


def test_stale_projection_version_rejected_before_enum_construction(tmp_path: Path) -> None:
    """The raw pre-gate must reject a stale projection_version before
    ``_contract_from_dict`` attempts to construct enum members that no longer
    exist (here, the retired ``investigate-standard`` applicability id)."""
    from autoskillit.execution.session import DefaultSkillSessionContractStore
    from autoskillit.execution.session._skill_session_contract_codec import _digest_json

    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    entry = store._provisional_path(correlation_key)  # noqa: SLF001
    manifest = store._read_manifest(entry)  # noqa: SLF001
    contract_data = manifest["contract"]
    contract_data["projection_version"] = 5
    contract_data["active_exploration_applicabilities"] = ["investigate-standard"]
    manifest["contract_digest"] = _digest_json(contract_data)
    store._write_manifest(entry, manifest)  # noqa: SLF001

    with pytest.raises(ValueError, match="unsupported projection_version 5; expected 7"):
        store.finalize(correlation_key, "stale-projection")


@pytest.mark.parametrize(
    ("field", "future_value"),
    [
        ("profile", "future-profile"),
        ("disposition", "future-disposition"),
        ("applicability", "future-applicability"),
        ("relationship_classes", ["future-relationship"]),
    ],
)
def test_store_quarantines_unknown_exploration_vector_enums_and_preserves_raw_record(
    tmp_path: Path,
    field: str,
    future_value: object,
) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore
    from autoskillit.execution.session._skill_session_contract_codec import (
        _contract_to_dict,
        _digest_json,
    )

    text = "projected\n"
    contract = _contract(tmp_path, text)
    first = contract.exploration_vectors["root"][0]
    sibling = replace(
        first,
        id="inspect-siblings",
        task=replace(
            first.task,
            task_id="inspect-siblings-task",
            frontier_item_id="inspect-siblings-frontier",
        ),
    )
    contract = replace(contract, exploration_vectors={"root": (first, sibling)})
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(
        contract=contract,
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    entry = store._provisional_path(correlation_key)  # noqa: SLF001
    manifest = store._read_manifest(entry)  # noqa: SLF001
    raw_vector = manifest["contract"]["exploration_vectors"]["root"][0]
    raw_vector[field] = future_value
    expected_raw = deepcopy(raw_vector)
    manifest["contract_digest"] = _digest_json(manifest["contract"])
    store._write_manifest(entry, manifest)  # noqa: SLF001

    store.finalize(correlation_key, "future-vector")
    stored = store.load("future-vector")

    assert tuple(vector.id for vector in stored.contract.exploration_vectors["root"]) == (
        "inspect-siblings",
    )
    opaque_index, opaque_vector = stored.contract.opaque_exploration_vectors["root"][0]
    assert opaque_index == 0
    assert opaque_vector["id"] == expected_raw["id"]
    assert _contract_to_dict(stored.contract)["exploration_vectors"]["root"][0] == expected_raw


def test_store_preserves_unknown_top_level_exploration_enums_without_routing_them(
    tmp_path: Path,
) -> None:
    from autoskillit.core import ExplorationVectorApplicabilityId
    from autoskillit.execution.session import DefaultSkillSessionContractStore
    from autoskillit.execution.session._skill_session_contract_codec import (
        _contract_to_dict,
        _digest_json,
    )

    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    entry = store._provisional_path(correlation_key)  # noqa: SLF001
    manifest = store._read_manifest(entry)  # noqa: SLF001
    contract_data = manifest["contract"]
    contract_data["resolved_exploration_profile"] = "future-profile"
    contract_data["active_exploration_applicabilities"] = [
        "always",
        "future-applicability",
    ]
    manifest["contract_digest"] = _digest_json(contract_data)
    store._write_manifest(entry, manifest)  # noqa: SLF001

    store.finalize(correlation_key, "future-top-level-enums")
    contract = store.load("future-top-level-enums").contract

    assert contract.resolved_exploration_profile is None
    assert contract.opaque_resolved_exploration_profile == "future-profile"
    assert contract.active_exploration_applicabilities == frozenset(
        {ExplorationVectorApplicabilityId.ALWAYS}
    )
    assert _contract_to_dict(contract)["resolved_exploration_profile"] == "future-profile"
    assert _contract_to_dict(contract)["active_exploration_applicabilities"] == [
        "always",
        "future-applicability",
    ]


def test_store_load_classifies_future_outer_schema(tmp_path: Path) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    store.finalize(correlation_key, "future-schema")
    entry = store._session_path("future-schema")  # noqa: SLF001
    manifest_path = entry / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 3
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SkillContractError) as exc_info:
        store.load("future-schema")

    assert exc_info.value.reason == "unsupported_future"  # type: ignore[attr-defined]
    assert exc_info.value.observed_version == 3  # type: ignore[attr-defined]
    assert exc_info.value.current_version == 2  # type: ignore[attr-defined]


def test_store_rejects_symlink_planted_in_projected_snapshot(tmp_path: Path) -> None:
    """Issue #4770 Registry Trace: the pre-fix ``snapshot_root.rglob("*")`` /
    ``path.is_file()`` completeness check silently followed a planted
    symlink pointing at a file, either miscounting it into ``actual_files``
    or (for a symlinked directory) omitting its contents entirely — in
    neither case did it explicitly reject the symlink itself. The migrated
    ``strict_walk``-based check now rejects any symlink outright."""
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    root = tmp_path / "contracts"
    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=root)
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    store.finalize(correlation_key, "resumable")
    stored = store.load("resumable")

    decoy_target = tmp_path / "decoy.txt"
    decoy_target.write_text("planted", encoding="utf-8")
    (stored.snapshot_dir / "planted_link").symlink_to(decoy_target)

    with pytest.raises(ValueError, match="symlink"):
        store.load("resumable")


def test_store_raises_not_silently_omits_when_snapshot_subtree_vanishes_mid_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #4770 test 11: a subdirectory deleted mid-enumeration during the
    set-equality completeness check must raise, not silently produce a
    shrunk ``actual_files`` set that could spuriously match ``declared_files``
    (defeating the tamper check)."""
    from autoskillit.execution.session import DefaultSkillSessionContractStore

    root = tmp_path / "contracts"
    text = "projected\n"
    store = DefaultSkillSessionContractStore(root=root)
    correlation_key = store.create_provisional(
        contract=_contract(tmp_path, text),
        snapshot={".claude/skills/root/SKILL.md": text},
    )
    store.finalize(correlation_key, "resumable")
    stored = store.load("resumable")

    extra_dir = stored.snapshot_dir / "extra_subtree"
    extra_dir.mkdir()
    (extra_dir / "leaf.txt").write_text("leaf", encoding="utf-8")

    inject_vanishing_subtree_on_descent(monkeypatch, extra_dir)
    with pytest.raises(ValueError) as excinfo:
        store.load("resumable")
    assert "file set mismatch" not in str(excinfo.value)
