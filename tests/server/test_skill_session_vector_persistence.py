"""Fresh and resumed skill-session exploration-vector persistence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    BackendConventions,
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
    ValidatedAddDir,
    WriteBehaviorSpec,
)
from autoskillit.execution.session import DefaultSkillSessionContractStore
from autoskillit.server.tools._execution_helpers import (
    build_skill_session_contract,
    rehydrate_skill_invocation,
)
from autoskillit.workspace import EffectiveSkillInvocation, SkillInfo, SkillProjectionContext

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_fresh_builder_and_resume_preserve_vectors_and_execution_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = """---
name: root
description: Test skill.
execution_role: session
---
<!-- autoskillit:exploration-vector id="inspect-consumers" -->
Inspect consumers.
<!-- /autoskillit:exploration-vector -->
"""
    vector = ExplorationVectorDef(
        id="inspect-consumers",
        disposition=ExplorationVectorDisposition.MIGRATED,
        rationale="Native semantic navigation covers the reviewed vector.",
        applicability=ExplorationVectorApplicabilityId.ALWAYS,
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
    )
    project_root = tmp_path / "project"
    source_path = project_root / ".claude/skills/root/SKILL.md"
    member = SkillInfo(
        name="root",
        source=SkillSource.PROJECT_LOCAL,
        path=source_path,
        execution_role=SkillExecutionRole.SESSION,
        exploration_vectors=(vector,),
        canonical_content=content,
        canonical_digest=hashlib.sha256(content.encode()).hexdigest(),
    )
    invocation = EffectiveSkillInvocation(
        root=member,
        closure=(member,),
        capability_union=frozenset(),
        project_root=project_root,
        execution_role=SkillExecutionRole.SESSION,
    )
    conventions = BackendConventions(skills_subdir=Path(".agents/skills"))
    backend = SimpleNamespace(name="codex", conventions=conventions)
    context = SkillProjectionContext(
        cwd=project_root,
        project_root=project_root,
        invocation=invocation,
        backend=backend,  # type: ignore[arg-type]
        conventions=conventions,
        parent_sandbox_mode="read-only",
    )
    session_root = tmp_path / "session"
    projected_path = session_root / conventions.skills_subdir / "root/SKILL.md"
    projected_path.parent.mkdir(parents=True)
    projected_path.write_text(content, encoding="utf-8")
    identity = ExecutionIdentity(
        children=(
            ChildExecutionIdentity(
                task_id=vector.task.task_id,
                role="semantic-code-navigator",
                plan_digest=vector.digest,
                definition_digest="definition-digest",
                requested_backend="codex",
                requested_model="gpt-5.6-luna",
                requested_effort="max",
            ),
        ),
    )

    contract, snapshot = build_skill_session_contract(
        session_root=ValidatedAddDir(path=str(session_root)),
        invocation=invocation,
        projection_context=context,
        member_names=("root",),
        resolved_command="/root",
        expected_output_patterns=(),
        write_behavior=WriteBehaviorSpec(),
        read_only=True,
        scope_discipline=False,
        completion_required=False,
        skill_contract_json="",
        execution_identity=identity,
    )
    assert contract.exploration_vectors == {"root": (vector,)}
    assert contract.execution_identity is identity

    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(contract=contract, snapshot=snapshot)
    store.finalize(correlation_key, "resume-vector")
    loaded = store.load("resume-vector").contract
    monkeypatch.setattr(
        "autoskillit.workspace.skills._bind_exploration_vector_markers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected reparse")),
    )

    resumed, _resumed_context = rehydrate_skill_invocation(
        loaded,
        backend,  # type: ignore[arg-type]
    )

    assert resumed.root.exploration_vectors == (vector,)
    assert loaded.execution_identity == identity


def test_fresh_contract_and_resume_retain_only_admitted_invocation_members(
    tmp_path: Path,
) -> None:
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    root_path = tmp_path / "project" / ".claude" / "skills" / "root" / "SKILL.md"
    dependency_path = tmp_path / "project" / ".claude" / "skills" / "dependency" / "SKILL.md"
    root_path.parent.mkdir(parents=True)
    dependency_path.parent.mkdir(parents=True)
    root_path.write_text(
        "---\n"
        "name: root\n"
        "description: Supported root.\n"
        "execution_role: session\n"
        "activate_deps: [dependency]\n"
        "---\n"
        "Run the root.\n",
        encoding="utf-8",
    )
    dependency_path.write_text(
        "---\n"
        "name: dependency\n"
        "description: Refused dependency.\n"
        "execution_role: session\n"
        "uses_capabilities: [agent_subagent]\n"
        "---\n"
        "Run the dependency.\n",
        encoding="utf-8",
    )
    root = _skill_info_from_frontmatter("root", SkillSource.PROJECT_LOCAL, root_path)
    dependency = _skill_info_from_frontmatter(
        "dependency",
        SkillSource.PROJECT_LOCAL,
        dependency_path,
    )
    project_root = tmp_path / "project"
    invocation = EffectiveSkillInvocation(
        root=root,
        closure=(root, dependency),
        capability_union=frozenset({"agent_subagent"}),
        project_root=project_root,
        execution_role=SkillExecutionRole.SESSION,
    )
    conventions = BackendConventions(skills_subdir=Path(".agents/skills"))
    backend = SimpleNamespace(name="test-backend", conventions=conventions)
    context = SkillProjectionContext(
        cwd=project_root,
        project_root=project_root,
        invocation=invocation,
        backend=backend,  # type: ignore[arg-type]
        conventions=conventions,
    )
    session_root = tmp_path / "session"
    projected_root = session_root / conventions.skills_subdir / "root" / "SKILL.md"
    projected_root.parent.mkdir(parents=True)
    projected_root.write_text(root.canonical_content, encoding="utf-8")

    contract, snapshot = build_skill_session_contract(
        session_root=ValidatedAddDir(path=str(session_root)),
        invocation=invocation,
        projection_context=context,
        member_names=("root",),
        resolved_command="/root",
        expected_output_patterns=(),
        write_behavior=WriteBehaviorSpec(),
        read_only=False,
        scope_discipline=False,
        completion_required=False,
        skill_contract_json="",
        execution_identity=ExecutionIdentity(),
    )

    assert contract.closure == ("root",)
    assert contract.capability_union == frozenset()
    assert contract.member_capabilities == {"root": frozenset()}
    assert contract.member_activate_deps == {"root": ()}
    assert tuple(snapshot) == (".agents/skills/root/SKILL.md",)
    assert "dependency" not in contract.canonical_contents

    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(contract=contract, snapshot=snapshot)
    store.finalize(correlation_key, "admitted-only")

    loaded = store.load("admitted-only").contract
    resumed, _resumed_context = rehydrate_skill_invocation(
        loaded,
        backend,  # type: ignore[arg-type]
    )

    assert loaded.closure == ("root",)
    assert tuple(member.name for member in resumed.closure) == ("root",)
    assert resumed.capability_union == frozenset()


@pytest.mark.parametrize(
    "member_names",
    [("dependency",), ("root", "missing")],
    ids=("refused-root", "unknown-member"),
)
def test_fresh_contract_rejects_member_sets_outside_admitted_rooted_closure(
    tmp_path: Path,
    member_names: tuple[str, ...],
) -> None:
    from autoskillit.core import SkillContractError

    root = SimpleNamespace(name="root")
    invocation = SimpleNamespace(
        root=root,
        closure=(root, SimpleNamespace(name="dependency")),
    )

    with pytest.raises(
        SkillContractError,
        match="Projected members do not match the effective invocation",
    ):
        build_skill_session_contract(
            session_root=ValidatedAddDir(path=str(tmp_path / "session")),
            invocation=invocation,
            projection_context=SimpleNamespace(),  # type: ignore[arg-type]
            member_names=member_names,
            resolved_command="/root",
            expected_output_patterns=(),
            write_behavior=WriteBehaviorSpec(),
            read_only=False,
            scope_discipline=False,
            completion_required=False,
            skill_contract_json="",
            execution_identity=ExecutionIdentity(),
        )
