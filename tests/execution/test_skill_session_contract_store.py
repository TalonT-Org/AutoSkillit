"""Always-on persistence contracts for resumable projected skill sessions."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import SkillExecutionRole

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _contract(tmp_path: Path, projected_text: str):
    from autoskillit.core import (
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
        projection_version=1,
        project_root=str(tmp_path / "project"),
        cwd=str(tmp_path / "worktree"),
        backend="claude-code",
        resolved_command="/root do work",
        member_roles={"root": SkillExecutionRole.SESSION},
        member_capabilities={"root": frozenset({"github_api_write"})},
        member_activate_deps={"root": ()},
        canonical_contents={"root": projected_text},
    )


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
    assert stored.raw_session_id == "backend/session:final"
    assert (stored.snapshot_dir / ".claude/skills/root/SKILL.md").read_text() == text
    assert stored.snapshot_dir.resolve().is_relative_to(root.resolve())


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
    ],
)
def test_store_rejects_malformed_serialized_boolean_authority(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    from autoskillit.execution.session import DefaultSkillSessionContractStore
    from autoskillit.execution.session._skill_session_contract_store import _digest_json

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
