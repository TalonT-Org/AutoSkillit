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
        max_results=100,
        max_report_bytes=20_000,
        evidence_version=1,
        native_dispatch=True,
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
        resolved_command="/root",
        expected_output_patterns=(),
        write_behavior=WriteBehaviorSpec(),
        read_only=True,
        completion_required=False,
        skill_contract_json="",
        execution_identity=identity,
    )
    assert contract.exploration_vectors == {"root": (vector,)}
    assert contract.execution_identity is identity

    store = DefaultSkillSessionContractStore(root=tmp_path / "contracts")
    correlation_key = store.create_provisional(contract, snapshot)
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
